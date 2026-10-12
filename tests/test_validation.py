"""Public vault validation API: verdict derivation, finding routing, the
reconciliation, ERC20 holdings and the `validate_vault` orchestration.

States are plain test dataclasses satisfying `VaultState`, so nothing here
depends on the CLI's fetch types.
"""

from __future__ import annotations

import dataclasses
from dataclasses import dataclass, field, replace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from _multicall import multicall_aware
from _validation import holding, validate
from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion import (
    BalanceFuse,
    Erc20Holdings,
    Finding,
    LendingMarketHealth,
    PlasmaVault,
    VaultLendingHealth,
    VaultValidation,
    Web3Context,
    read_erc20_holdings,
    validate_vault,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import ChainId, MarketId
from ipor_fusion.validation import GET_MARKET_SUBSTRATES, Reconciliation


def _addr(suffix: str) -> str:
    return Web3.to_checksum_address("0x" + suffix.rjust(40, "0"))


VAULT = _addr("1111")
ASSET = _addr("a1")
ORACLE = _addr("0c")
TOKEN = _addr("b1")
OTHER = _addr("b2")
FUSE = _addr("f1")
ORPHAN_FUSE = _addr("f5")
AAVE = MarketId(IporFusionMarkets.AAVE_V3)
MORPHO = MarketId(IporFusionMarkets.MORPHO)
ERC20_MARKET = MarketId(IporFusionMarkets.ERC20_VAULT_BALANCE)
WAD = 10**18


def _substrate(address: str) -> bytes:
    return bytes(12) + bytes.fromhex(address[2:])


@dataclass
class Queue:
    shares_to_release: int


@dataclass
class Unpriceable:
    market_id: int
    token: str
    via: str
    zero_balance_reverts: bool
    position_open: bool | None = None
    morpho_collateral: bool = False
    price_source: str | None = None


@dataclass
class State:
    block_number: int = 100
    block_timestamp: int = 1_700_000_000
    asset: str = ASSET
    asset_symbol: str = "USDC"
    asset_decimals: int = 18
    asset_price_usd: float | None = 1.0
    total_assets: int = 100 * WAD
    underlying_balance_on_vault: int = 0
    price_oracle_addr: str = ORACLE
    balance_fuses: list[BalanceFuse] = field(
        default_factory=lambda: [BalanceFuse(AAVE, FUSE)]  # type: ignore[arg-type]
    )
    market_substrates: dict[int, list[bytes]] | None = None
    fuse_markets: dict[str, int] | None = None
    dependency_graph: dict[int, list[int]] | None = None
    withdraw_manager_data: Queue | None = None
    lending_health: VaultLendingHealth | None = None
    unpriceable_priced_tokens: list[Unpriceable] | None = None
    unimplemented_getters: list[str] = field(default_factory=list)


def _lending(health_factor: float | None, usage: float | None = 50.0):
    return VaultLendingHealth(
        markets=[
            LendingMarketHealth(
                protocol="morpho",
                market_id=MORPHO,
                market_name="MORPHO",
                current_ltv=0.5,
                max_ltv=0.86,
                health_factor=health_factor,
                total_collateral_usd=None,
                total_debt_usd=None,
                ltv_usage_percent=usage,
            )
        ]
    )


def _checks(validation: VaultValidation) -> list[tuple[str, str]]:
    return [(f.check, f.severity) for f in validation.findings]


def _validation(*findings: Finding) -> VaultValidation:
    return dataclasses.replace(validate(State(total_assets=0)), findings=findings)


class TestVerdict:
    def test_no_findings_is_ok(self):
        validation = _validation()
        assert validation.verdict == "ok"
        assert (validation.criticals, validation.warnings, validation.ok) == (
            (),
            (),
            (),
        )

    def test_passing_checks_alone_are_ok(self):
        assert _validation(Finding("lending_health", "ok", "fine")).verdict == "ok"

    def test_any_warning_without_critical_is_warning(self):
        validation = _validation(
            Finding("lending_health", "ok", "fine"),
            Finding("missing_price_feed", "warning", "w"),
        )
        assert validation.verdict == "warning"
        assert validation.warnings == ("w",)
        assert validation.ok == ("fine",)

    def test_any_critical_is_critical(self):
        validation = _validation(
            Finding("missing_price_feed", "warning", "w1"),
            Finding("orphan_fuse", "critical", "c1"),
            Finding("erc20_cache_stale", "warning", "w2"),
            Finding("lending_health", "critical", "c2"),
        )
        assert validation.verdict == "critical"
        assert validation.criticals == ("c1", "c2")
        assert validation.warnings == ("w1", "w2")

    def test_derived_fields_are_not_constructor_arguments(self):
        names = {f.name for f in dataclasses.fields(VaultValidation) if not f.init}
        assert names == {"verdict", "criticals", "warnings", "ok"}

    def test_result_is_frozen(self):
        with pytest.raises(dataclasses.FrozenInstanceError):
            _validation().verdict = "critical"  # type: ignore[misc]


class TestRouting:
    def test_clean_vault_passes(self):
        validation = validate(State(), balance_fuses_total=100 * WAD)
        assert _checks(validation) == [("total_assets_reconciliation", "ok")]
        assert validation.verdict == "ok"

    def test_unimplemented_getter_is_warning(self):
        validation = validate(State(unimplemented_getters=[GET_MARKET_SUBSTRATES]))
        assert ("unimplemented_getter", "warning") in _checks(validation)

    def test_orphan_action_fuse_is_critical(self):
        state = State(fuse_markets={ORPHAN_FUSE: MORPHO}, total_assets=0)
        validation = validate(state)
        assert _checks(validation) == [("orphan_fuse", "critical")]
        assert ORPHAN_FUSE in validation.criticals[0]

    def test_missing_erc20_dependency_is_critical(self):
        state = State(
            total_assets=0,
            balance_fuses=[BalanceFuse(AAVE, FUSE), BalanceFuse(ERC20_MARKET, FUSE)],  # type: ignore[arg-type]
            market_substrates={ERC20_MARKET: [_substrate(TOKEN)]},
            dependency_graph={AAVE: []},
        )
        assert _checks(validate(state)) == [("missing_erc20_dependency", "critical")]

    @pytest.mark.parametrize(
        ("token", "severity"),
        [
            (
                Unpriceable(AAVE, TOKEN, "reserve", zero_balance_reverts=True),
                "critical",
            ),
            (
                Unpriceable(AAVE, TOKEN, "reserve", False, position_open=True),
                "critical",
            ),
            (
                Unpriceable(AAVE, TOKEN, "reserve", False, position_open=False),
                "warning",
            ),
            (Unpriceable(AAVE, TOKEN, "reserve", False), "warning"),
        ],
    )
    def test_unpriceable_token(self, token: Unpriceable, severity: str):
        validation = validate(State(total_assets=0, unpriceable_priced_tokens=[token]))
        assert _checks(validation) == [("unpriceable_token", severity)]

    @pytest.mark.parametrize(
        ("health_factor", "severity"),
        [(1.6, "ok"), (1.08, "warning"), (1.03, "critical")],
    )
    def test_lending_health(self, health_factor: float, severity: str):
        validation = validate(
            State(total_assets=0, lending_health=_lending(health_factor))
        )
        assert _checks(validation) == [("lending_health", severity)]
        assert validation.lending_health is not None

    def test_near_liquidation_makes_the_verdict_critical(self):
        validation = validate(State(total_assets=0, lending_health=_lending(1.03)))
        assert validation.verdict == "critical"
        assert "NEAR LIQUIDATION" in validation.criticals[0]

    def test_lending_market_without_usage_is_skipped(self):
        state = State(total_assets=0, lending_health=_lending(None, usage=None))
        assert validate(state).findings == ()

    def test_total_assets_mismatch_with_pending_withdrawals_and_drift(self):
        plasma_vault = MagicMock()
        plasma_vault.convert_to_assets.return_value.call.return_value = 30 * WAD
        state = State(withdraw_manager_data=Queue(shares_to_release=29 * WAD))
        validation = validate(state, 150 * WAD, plasma_vault=plasma_vault)
        assert _checks(validation) == [
            ("total_assets_reconciliation", "warning"),
            ("pending_withdrawals", "warning"),
            ("market_storage_drift", "warning"),
        ]

    def test_uncovered_erc20_is_one_multiline_warning(self):
        erc20 = Erc20Holdings(
            tokens=(
                holding(TOKEN, "AAA", balance=WAD, price_usd=2.0),
                holding(OTHER, "BBB", balance=WAD, price_usd=3.0),
            )
        )
        validation = validate(State(), 100 * WAD, erc20)
        assert ("uncovered_erc20", "warning") in _checks(validation)
        (message,) = [
            f.message for f in validation.findings if f.check == "uncovered_erc20"
        ]
        heading, *rows = message.split("\n")
        assert heading == "ERC20 holdings not covered by balance fuses:"
        assert [row.split()[0] for row in rows] == ["AAA", "BBB"]

    def test_stale_erc20_cache_and_missing_price_feed(self):
        erc20 = Erc20Holdings(
            value_in_asset=50 * WAD,
            market_cached_value=WAD,
            unpriced_tokens=(f"{TOKEN} (TKN)",),
        )
        validation = validate(State(), 100 * WAD, erc20)
        assert _checks(validation) == [
            ("total_assets_reconciliation", "ok"),
            ("erc20_cache_stale", "warning"),
            ("missing_price_feed", "warning"),
        ]


class TestReconciliation:
    def test_balances_sum_once_per_market_with_idle_underlying(self):
        state = State(total_assets=100 * WAD, underlying_balance_on_vault=10 * WAD)
        recon = validate(state, 89 * WAD).reconciliation
        assert recon == Reconciliation(
            balance_fuses_total=89 * WAD,
            balance_fuses_total_usd=89.0,
            underlying_on_vault=10 * WAD,
            underlying_on_vault_usd=10.0,
            erc20_direct_total=0,
            erc20_direct_total_usd=0.0,
            sum=99 * WAD,
            sum_usd=99.0,
            on_chain_total_assets=100 * WAD,
            on_chain_total_assets_usd=100.0,
            delta=-(WAD),
            delta_usd=1.0,
            delta_percent=1.0,
            pending_withdrawals=0,
            pending_withdrawals_usd=0.0,
            implied_market_total=90 * WAD,
        )
        assert recon.market_storage_divergence == -(WAD)

    def test_without_asset_price_usd_values_are_zero(self):
        recon = validate(State(asset_price_usd=None), 100 * WAD).reconciliation
        assert recon.on_chain_total_assets_usd is None
        assert (recon.sum_usd, recon.delta_usd) == (0.0, 0)

    def test_unreadable_pending_withdrawals_count_as_zero(self):
        plasma_vault = MagicMock()
        plasma_vault.convert_to_assets.return_value.call.side_effect = (
            ContractLogicError("revert")
        )
        state = State(withdraw_manager_data=Queue(shares_to_release=WAD))
        recon = validate(state, 100 * WAD, plasma_vault=plasma_vault).reconciliation
        assert recon.pending_withdrawals == 0


def _selector(signature: str) -> bytes:
    return function_signature_to_4byte_selector(signature)


class _Chain:
    """eth_call answers for one vault, its ERC20 market tokens and oracle."""

    def __init__(self) -> None:
        self.blocks: list[Any] = []
        self.market_balances = {AAVE: 60 * WAD, ERC20_MARKET: 2 * WAD}
        self.balances = {ASSET: 5 * WAD, TOKEN: 3 * WAD, OTHER: WAD}
        self.prices = {ASSET: WAD, TOKEN: 2 * WAD}
        self.symbols = {TOKEN: "TKN"}

    def handle(self, to: str, data: bytes) -> bytes:  # noqa: C901, PLR0911
        to = Web3.to_checksum_address(to)
        selector, args = data[:4], data[4:]
        if to == VAULT and selector == _selector("totalAssetsInMarket(uint256)"):
            (market,) = decode(["uint256"], args)
            return encode(["uint256"], [self.market_balances[market]])
        if to == VAULT and selector == _selector("convertToAssets(uint256)"):
            (shares,) = decode(["uint256"], args)
            return encode(["uint256"], [shares])
        if to == ORACLE and selector == _selector("getAssetPrice(address)"):
            (token,) = decode(["address"], args)
            if (price := self.prices.get(Web3.to_checksum_address(token))) is None:
                raise ContractLogicError("no price")
            return encode(["uint256", "uint256"], [price, 18])
        if selector == _selector("decimals()") and to in self.balances:
            return encode(["uint8"], [18])
        if selector == _selector("balanceOf(address)") and to in self.balances:
            return encode(["uint256"], [self.balances[to]])
        if selector == _selector("symbol()") and to in self.symbols:
            return encode(["string"], [self.symbols[to]])
        raise ContractLogicError(f"unexpected call to {to}")

    def web3(self) -> MagicMock:
        call = multicall_aware(self.handle)
        web3 = MagicMock()
        web3.eth.block_number = 777

        def eth_call(tx: dict, block_identifier: Any = None):
            self.blocks.append(block_identifier)
            return call(tx["to"], tx["data"])

        web3.eth.call.side_effect = eth_call
        return web3


def _erc20_state(**overrides: Any) -> State:
    return replace(
        State(
            total_assets=67 * WAD,
            underlying_balance_on_vault=5 * WAD,
            balance_fuses=[
                BalanceFuse(AAVE, FUSE),  # type: ignore[arg-type]
                BalanceFuse(ERC20_MARKET, FUSE),  # type: ignore[arg-type]
                BalanceFuse(AAVE, ORPHAN_FUSE),  # type: ignore[arg-type]
            ],
            market_substrates={
                ERC20_MARKET: [_substrate(ASSET), _substrate(TOKEN), _substrate(OTHER)]
            },
            dependency_graph={AAVE: [ERC20_MARKET]},
            withdraw_manager_data=Queue(shares_to_release=0),
        ),
        **overrides,
    )


class TestReadErc20Holdings:
    def _read(self, state: State, **kwargs: Any) -> Erc20Holdings:
        chain = _Chain()
        ctx = Web3Context(chain.web3(), ChainId(1))
        return read_erc20_holdings(ctx, PlasmaVault(ctx, VAULT), state, **kwargs)  # type: ignore[arg-type]

    def test_values_priced_tokens_in_the_underlying(self):
        erc20 = self._read(_erc20_state())
        assert [(t.symbol, t.balance, t.price_usd, t.note) for t in erc20.tokens] == [
            ("?", 5 * WAD, 1.0, "underlying asset"),
            ("TKN", 3 * WAD, 2.0, "$6.00 not reflected in totalAssets (stale cache)"),
            ("?", WAD, None, "in ERC20_VAULT_BALANCE"),
        ]
        assert (erc20.value_in_asset, erc20.value_usd) == (11 * WAD, 11.0)
        assert erc20.market_cached_value == 2 * WAD
        assert erc20.unpriced_tokens == (f"{OTHER.lower()} (?)",)

    def test_resolve_symbol_overrides_the_onchain_symbol(self):
        erc20 = self._read(_erc20_state(), resolve_symbol=lambda a: a[-2:])
        assert [t.symbol for t in erc20.tokens] == ["a1", "b1", "b2"]

    def test_no_erc20_market(self):
        assert self._read(State()) == Erc20Holdings()

    def test_market_without_substrates_keeps_its_cached_value(self):
        erc20 = self._read(_erc20_state(market_substrates=None))
        assert erc20 == Erc20Holdings(market_cached_value=2 * WAD)


class TestValidateVault:
    def _run(self, ctx: Web3Context, state: State, block: int | None = None):
        with patch(
            "ipor_fusion.cli.vault_fetcher._fetch_vault_data", return_value=state
        ) as fetch:
            validation = validate_vault(ctx, VAULT, block)
        return validation, fetch

    def test_reads_every_market_once_at_the_pinned_block(self):
        chain = _Chain()
        ctx = Web3Context(chain.web3(), ChainId(1))
        state = _erc20_state(block_number=123)

        validation, fetch = self._run(ctx, state, block=123)

        pinned, plasma_vault, block = fetch.call_args.args
        assert (block, fetch.call_args.kwargs) == (123, {"chain_id": 1})
        assert pinned is not ctx and pinned.default_block == 123
        assert plasma_vault.address == VAULT
        assert ctx.default_block == "latest"
        assert set(chain.blocks) == {123}
        assert validation.reconciliation.balance_fuses_total == 62 * WAD
        assert (validation.vault, validation.chain_id) == (VAULT, 1)
        assert (validation.block_number, validation.asset_symbol) == (123, "USDC")
        assert validation.erc20.value_in_asset == 11 * WAD
        assert validation.verdict == "warning"

    def test_defaults_to_the_context_block(self):
        chain = _Chain()
        ctx = Web3Context(chain.web3(), ChainId(1))
        ctx.default_block = 456
        _, fetch = self._run(ctx, _erc20_state())
        assert fetch.call_args.args[2] == 456

    def test_pins_the_latest_block_otherwise(self):
        chain = _Chain()
        ctx = Web3Context(chain.web3(), ChainId(1))
        _, fetch = self._run(ctx, _erc20_state())
        assert fetch.call_args.args[2] == 777
        assert set(chain.blocks) == {777}

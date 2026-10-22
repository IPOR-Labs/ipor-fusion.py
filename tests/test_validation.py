"""Public vault validation API: verdict derivation, finding routing, the
reconciliation, ERC20 holdings and the `validate_vault` orchestration.

States are plain test dataclasses satisfying `VaultState`, so nothing here
depends on the CLI's fetch types.
"""

from __future__ import annotations

import dataclasses
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field, replace
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
from _multicall import multicall_aware
from _validation import holding, validate
from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3RPCError

from ipor_fusion import (
    BalanceFuse,
    Erc20Holdings,
    Finding,
    LendingMarketHealth,
    UnpriceableCheck,
    UnreadableLendingMarket,
    VaultLendingHealth,
    VaultValidation,
    Web3Context,
    validate_vault,
    vault_data,
)
from ipor_fusion.cache import NullCache
from ipor_fusion.core.oracle import price_oracle_middleware
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import ChainId, MarketId
from ipor_fusion.validation import GET_MARKET_SUBSTRATES, Reconciliation
from ipor_fusion.vault_data import VaultData, _MarketReads, _VaultReads
from ipor_fusion.vault_erc20 import _Asset, _erc20_holdings, _read_erc20_market


def _addr(suffix: str) -> str:
    return Web3.to_checksum_address("0x" + suffix.rjust(40, "0"))


VAULT = _addr("1111")
ASSET = _addr("a1")
ORACLE = _addr("0c")
TOKEN = _addr("b1")
OTHER = _addr("b2")
FUSE = _addr("f1")
ORPHAN_FUSE = _addr("f5")
AAVE = IporFusionMarkets.AAVE_V3
MORPHO = IporFusionMarkets.MORPHO
ERC20_MARKET = IporFusionMarkets.ERC20_VAULT_BALANCE
WAD = 10**18


def _substrate(address: str) -> bytes:
    return bytes(12) + bytes.fromhex(address[2:])


@dataclass
class Queue:
    shares_to_release: int
    shares_to_release_assets: int | None = None


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
    vault: str = VAULT
    chain_id: int = 1
    block_number: int = 100
    block_timestamp: int = 1_700_000_000
    asset: str = ASSET
    asset_symbol: str = "USDC"
    asset_decimals: int = 18
    asset_price_usd: float | None = 1.0
    total_assets: int = 100 * WAD
    underlying_balance_on_vault: int = 0
    rewards_manager_balance: int | None = 0
    unrealized_management_fee: int | None = None
    stored_markets_total: int | None = None
    price_oracle: str = ORACLE
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
    failed_reads: list[str] = field(default_factory=list)
    market_balances: dict[int, int] = field(default_factory=dict)
    erc20: Erc20Holdings = field(default_factory=Erc20Holdings)


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
        state = State(
            withdraw_manager_data=Queue(
                shares_to_release=29 * WAD, shares_to_release_assets=30 * WAD
            )
        )
        validation = validate(state, 150 * WAD)
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


class TestIncompleteReads:
    def test_a_failed_validation_input_is_a_warning_not_ok(self):
        state = State(failed_reads=["lending_health", "unpriceable_priced_tokens"])
        validation = validate(state, 100 * WAD)
        incomplete = [f for f in validation.findings if f.check == "incomplete_read"]
        assert [f.severity for f in incomplete] == ["warning", "warning"]
        assert "lending_health could not be read" in incomplete[0].message
        assert validation.verdict == "warning"

    def test_a_substrate_the_price_check_missed_is_named(self):
        read = f"unpriceable_priced_tokens: Morpho position in market 0x{'ab' * 32}"
        (finding,) = [
            f
            for f in validate(State(failed_reads=[read]), 100 * WAD).findings
            if f.check == "incomplete_read"
        ]
        assert finding.message.startswith(
            "WARNING — unpriceable_priced_tokens (Morpho position in market 0x"
        )

    def test_an_unreadable_lending_market_is_a_warning(self):
        health = VaultLendingHealth(
            markets=[],
            unreadable=[
                UnreadableLendingMarket(
                    protocol="morpho",
                    market_id=MarketId(14),
                    market_name="MORPHO",
                    reason="oracle price read failed: Web3RPCError",
                    substrate_id="0x" + "ab" * 32,
                )
            ],
        )
        validation = validate(State(lending_health=health), 100 * WAD)
        (finding,) = [f for f in validation.findings if f.check == "lending_health"]
        assert finding.severity == "warning"
        assert "health unknown, oracle price read failed" in finding.message
        assert validation.verdict == "warning"


class TestReconciliation:
    def test_the_rewards_manager_balance_is_part_of_total_assets(self):
        state = State(
            total_assets=100 * WAD,
            underlying_balance_on_vault=10 * WAD,
            rewards_manager_balance=5 * WAD,
        )
        validation = validate(state, 85 * WAD)
        recon = validation.reconciliation
        assert recon.rewards_manager_balance == 5 * WAD
        assert recon.rewards_manager_balance_usd == 5.0
        assert recon.sum == 100 * WAD
        assert recon.delta == 0
        assert recon.implied_market_total == 85 * WAD
        assert recon.market_storage_divergence == 0
        assert validation.verdict == "ok"

    def test_a_netting_vault_reconciles_net_of_the_management_fee(self):
        # Contracts before ipor-fusion #475: totalAssets = gross - unrealized fee.
        state = State(
            total_assets=97 * WAD,
            underlying_balance_on_vault=10 * WAD,
            unrealized_management_fee=3 * WAD,
            stored_markets_total=90 * WAD,
        )
        validation = validate(state, 90 * WAD)
        recon = validation.reconciliation
        assert recon.management_fee_netted == 3 * WAD
        assert recon.management_fee_netted_usd == 3.0
        assert recon.sum == 97 * WAD
        assert recon.delta == 0
        assert recon.implied_market_total == 90 * WAD
        assert recon.market_storage_divergence == 0
        assert validation.verdict == "ok"
        (line,) = [
            f.message
            for f in validation.findings
            if f.check == "total_assets_reconciliation"
        ]
        assert line.startswith(
            "Balance fuses + underlying - unrealized management fee = 97"
        )

    def test_a_gross_vault_does_not_net_its_management_fee(self):
        # ipor-fusion #475 and later: totalAssets is gross despite an accrued fee.
        state = State(
            total_assets=100 * WAD,
            underlying_balance_on_vault=10 * WAD,
            unrealized_management_fee=3 * WAD,
            stored_markets_total=90 * WAD,
        )
        recon = validate(state, 90 * WAD).reconciliation
        assert recon.management_fee_netted == 0
        assert recon.delta == 0

    def test_market_drift_equal_to_the_fee_is_not_taken_for_the_fee(self):
        # A gross vault whose stored global total (87) lags the per-market
        # caches (90) by exactly the accrued fee (3): drift, not netting.
        state = State(
            total_assets=97 * WAD,
            underlying_balance_on_vault=10 * WAD,
            unrealized_management_fee=3 * WAD,
            stored_markets_total=87 * WAD,
        )
        validation = validate(state, 90 * WAD)
        recon = validation.reconciliation
        assert recon.management_fee_netted == 0
        assert recon.delta == 3 * WAD
        assert recon.implied_market_total == 87 * WAD
        assert "market_storage_drift" in {f.check for f in validation.findings}

    def test_a_fee_above_gross_nets_to_zero_total_assets(self):
        state = State(
            total_assets=0,
            unrealized_management_fee=5 * WAD,
            stored_markets_total=2 * WAD,
        )
        recon = validate(state, 2 * WAD).reconciliation
        assert recon.management_fee_netted == 2 * WAD
        assert recon.sum == 0

    def test_without_the_stored_total_nothing_is_netted(self):
        state = State(total_assets=97 * WAD, unrealized_management_fee=3 * WAD)
        recon = validate(state, 100 * WAD).reconciliation
        assert recon.management_fee_netted == 0
        assert recon.delta == 3 * WAD

    def test_an_unread_management_fee_nets_nothing(self):
        state = State(total_assets=97 * WAD, unrealized_management_fee=None)
        recon = validate(state, 100 * WAD).reconciliation
        assert recon.management_fee_netted == 0
        assert recon.delta == 3 * WAD

    def test_an_unreadable_rewards_manager_balance_warns(self):
        state = State(total_assets=100 * WAD, rewards_manager_balance=None)
        validation = validate(state, 100 * WAD)
        assert validation.reconciliation.rewards_manager_balance == 0
        (finding,) = [
            f for f in validation.findings if f.check == "rewards_manager_balance"
        ]
        assert finding.severity == "warning"
        assert validation.verdict == "warning"

    def test_an_unknown_rewards_balance_is_not_called_market_drift(self):
        state = State(total_assets=100 * WAD, rewards_manager_balance=None)
        checks = {f.check for f in validate(state, 90 * WAD).findings}
        assert "rewards_manager_balance" in checks
        assert "total_assets_reconciliation" in checks
        assert "market_storage_drift" not in checks

    def test_the_reconciliation_line_names_the_rewards_manager(self):
        state = State(total_assets=100 * WAD, rewards_manager_balance=5 * WAD)
        (finding,) = [
            f
            for f in validate(state, 95 * WAD).findings
            if f.check == "total_assets_reconciliation"
        ]
        assert finding.message.startswith(
            "Balance fuses + underlying + rewards manager = 100"
        )

    def test_balances_sum_once_per_market_with_idle_underlying(self):
        state = State(total_assets=100 * WAD, underlying_balance_on_vault=10 * WAD)
        recon = validate(state, 89 * WAD).reconciliation
        assert recon == Reconciliation(
            balance_fuses_total=89 * WAD,
            balance_fuses_total_usd=89.0,
            underlying_on_vault=10 * WAD,
            underlying_on_vault_usd=10.0,
            rewards_manager_balance=0,
            rewards_manager_balance_usd=0.0,
            management_fee_netted=0,
            management_fee_netted_usd=0.0,
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
        state = State(
            withdraw_manager_data=Queue(
                shares_to_release=WAD, shares_to_release_assets=None
            )
        )
        recon = validate(state, 100 * WAD).reconciliation
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
        self.symbol_reads = 0

    def handle(self, to: str, data: bytes) -> bytes:  # noqa: C901, PLR0911
        to = Web3.to_checksum_address(to)
        selector, args = data[:4], data[4:]
        if selector == _selector("symbol()"):
            self.symbol_reads += 1
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
        web3.eth.get_code.side_effect = AssertionError("no per-token code read")
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
    def _read(
        self, state: State, symbols: dict[str, str] | None = None
    ) -> Erc20Holdings:
        chain = _Chain()
        ctx = Web3Context(chain.web3(), ChainId(1), cache=NullCache())
        market = _read_erc20_market(
            ctx,
            VAULT,  # type: ignore[arg-type]
            balance_fuses=state.balance_fuses,
            market_substrates=state.market_substrates or {},  # type: ignore[arg-type]
            market_balances=chain.market_balances,  # type: ignore[arg-type]
            oracle=price_oracle_middleware(ctx, ORACLE),  # type: ignore[arg-type]
        )
        assert chain.symbol_reads == 0
        return _erc20_holdings(
            _Asset(ASSET, state.asset_decimals, state.asset_price_usd),  # type: ignore[arg-type]
            market,
            {TOKEN.lower(): "TKN"} if symbols is None else symbols,  # type: ignore[dict-item]
        )

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

    def test_symbols_name_the_holdings(self):
        symbols = {a.lower(): a.lower()[-2:] for a in (ASSET, TOKEN, OTHER)}
        erc20 = self._read(_erc20_state(), symbols)
        assert [t.symbol for t in erc20.tokens] == ["a1", "b1", "b2"]
        assert self._read(_erc20_state(), {}).unpriced_tokens == (
            f"{OTHER.lower()} (?)",
        )

    def test_no_erc20_market(self):
        assert self._read(State()) == Erc20Holdings()

    def test_market_without_substrates_keeps_its_cached_value(self):
        erc20 = self._read(_erc20_state(market_substrates=None))
        assert erc20 == Erc20Holdings(market_cached_value=2 * WAD)


class TestSnapshotTokenMetadata:
    """`fetch_vault_data`'s optional token metadata. The vault, fee, queue,
    role and market pipelines (the underlying asset's symbol and decimals
    among them) are stubbed; the ERC20 market and the metadata batch run on
    the fake chain, whose code reads raise."""

    def _fetch(self, *, metadata: bool) -> tuple[_Chain, VaultData]:
        state = _erc20_state()
        chain = _Chain()
        ctx = Web3Context(chain.web3(), ChainId(1), cache=NullCache())
        markets = _MarketReads(
            balance_fuses=state.balance_fuses,
            dependency_graph={},
            market_substrates=state.market_substrates,  # type: ignore[arg-type]
            aave_pools={},
            market_balances=chain.market_balances,  # type: ignore[arg-type]
        )
        vault_reads = _VaultReads(
            share_decimals=18,
            total_assets=state.total_assets,
            total_supply=state.total_assets,
            supply_cap=state.total_assets,
            asset=ASSET,
            access_manager=_addr("ac"),
            price_oracle=ORACLE,
            fuses=[FUSE],
            instant_fuses=[],
            vault_name="Vault",
            rewards_manager=None,
            asset_decimals=state.asset_decimals,
            underlying_balance_on_vault=state.underlying_balance_on_vault,
            asset_symbol="USDC",
            asset_price_usd=state.asset_price_usd,
            fuse_markets=None,
            unimplemented_getters=[],
            rewards_manager_balance=0,
        )
        oracle = price_oracle_middleware(ctx, ORACLE)  # type: ignore[arg-type]
        with (
            patch.object(vault_data, "_block_timestamp", return_value=1),
            patch.object(
                vault_data, "_fetch_vault_reads", return_value=(vault_reads, oracle)
            ),
            patch.object(vault_data, "_fetch_fee_data", return_value=None),
            patch.object(
                vault_data, "_fetch_withdraw_manager", return_value=(None, None)
            ),
            patch.object(vault_data, "_fetch_role_accounts", return_value=None),
            patch.object(vault_data, "_fetch_market_reads", return_value=markets),
            patch.object(vault_data, "_fetch_hypercore_state", return_value=None),
        ):
            data = vault_data.fetch_vault_data(
                ctx,
                VAULT,  # type: ignore[arg-type]
                block=123,
                lending=False,
                metadata=metadata,
            )
        return chain, data

    def test_without_metadata_no_token_symbol_is_read(self):
        chain, data = self._fetch(metadata=False)

        assert chain.symbol_reads == 0
        assert (data.token_symbols, data.token_decimals) == ({}, {})
        assert [t.symbol for t in data.erc20.tokens] == ["?", "?", "?"]
        assert [t.decimals for t in data.erc20.tokens] == [18, 18, 18]
        assert data.erc20.value_in_asset == 11 * WAD
        assert data.asset_symbol == "USDC"

    def test_metadata_names_the_holdings_in_one_batch(self):
        chain, data = self._fetch(metadata=True)

        without, _ = self._fetch(metadata=False)
        assert chain.symbol_reads == 3
        assert len(chain.blocks) == len(without.blocks) + 1
        assert data.token_symbols == {
            ASSET.lower(): "",
            TOKEN.lower(): "TKN",
            OTHER.lower(): "",
        }
        assert [t.symbol for t in data.erc20.tokens] == ["?", "TKN", "?"]


class TestValidateVault:
    def test_validates_the_snapshot_it_fetches(self):
        ctx = MagicMock()
        state = _erc20_state(
            block_number=123,
            market_balances={AAVE: 60 * WAD, ERC20_MARKET: 2 * WAD},
            erc20=Erc20Holdings(value_in_asset=11 * WAD),
        )
        with patch(
            "ipor_fusion.validation.fetch_vault_data", return_value=state
        ) as fetch:
            validation = validate_vault(ctx, VAULT, block=123)  # type: ignore[arg-type]
            fetch.assert_called_once_with(ctx, VAULT, block=123, metadata=False)
            validate_vault(ctx, VAULT, block=123, metadata=True)  # type: ignore[arg-type]
            fetch.assert_called_with(ctx, VAULT, block=123, metadata=True)

        assert validation.reconciliation.balance_fuses_total == 62 * WAD
        assert (validation.vault, validation.chain_id) == (VAULT, 1)
        assert (validation.block_number, validation.asset_symbol) == (123, "USDC")
        assert validation.erc20.value_in_asset == 11 * WAD


class TestFailedValidationInputs:
    """A validation input whose read fails is named in `failed_reads`, so
    validation warns instead of passing it as checked."""

    @staticmethod
    def _markets() -> _MarketReads:
        return _MarketReads(
            balance_fuses=[],
            dependency_graph={},
            market_substrates={},
            aave_pools={},
            market_balances={},
        )

    def test_a_failed_lending_health_read_is_recorded(self):
        with (
            patch.object(
                vault_data,
                "fetch_vault_lending_health",
                side_effect=Web3RPCError("rate limited"),
            ),
            ThreadPoolExecutor() as pool,
        ):
            reads = vault_data._fetch_lending_reads(
                MagicMock(), pool, VAULT, self._markets()
            )
        assert reads.lending_health is None
        assert reads.failed == ["lending_health"]

    def test_a_read_lending_health_is_not_recorded(self):
        with (
            patch.object(
                vault_data,
                "fetch_vault_lending_health",
                return_value=VaultLendingHealth(markets=[]),
            ),
            ThreadPoolExecutor() as pool,
        ):
            reads = vault_data._fetch_lending_reads(
                MagicMock(), pool, VAULT, self._markets()
            )
        assert reads.failed == []

    def test_uncovered_substrates_reach_the_verdict(self):
        """A partly read Morpho breakdown and a discovery gap of the price
        check travel through the snapshot to incomplete_read warnings."""
        reads = _VaultReads(
            share_decimals=18,
            total_assets=100 * WAD,
            total_supply=100 * WAD,
            supply_cap=100 * WAD,
            asset=ASSET,
            access_manager=_addr("ac"),
            price_oracle=ORACLE,
            fuses=[],
            instant_fuses=[],
            vault_name="Vault",
            rewards_manager=None,
            asset_decimals=18,
            underlying_balance_on_vault=0,
            asset_symbol="USDC",
            asset_price_usd=1.0,
            fuse_markets=None,
            unimplemented_getters=[],
            rewards_manager_balance=0,
        )
        lending = vault_data._LendingReads(
            lending_health=VaultLendingHealth(markets=[]),
            morpho_unread=["Morpho position in market 0xcd"],
        )
        check = UnpriceableCheck(tokens=[], unread=["asset() of granted vault 0xee"])
        with (
            patch.object(vault_data, "_read_erc20_market", return_value=None),
            patch.object(
                vault_data, "fetch_unpriceable_priced_tokens", return_value=check
            ),
            patch.object(vault_data, "_fetch_breakdown_token_prices", return_value={}),
            ThreadPoolExecutor() as pool,
        ):
            derived = vault_data._derived_reads(
                MagicMock(),
                pool,
                VAULT,
                reads,
                MagicMock(),
                self._markets(),
                lending,
                metadata=False,
            )
        assert derived.failed == [
            "unpriceable_priced_tokens: Morpho position in market 0xcd",
            "unpriceable_priced_tokens: asset() of granted vault 0xee",
        ]
        validation = validate(State(failed_reads=derived.failed), 100 * WAD)
        incomplete = [f for f in validation.findings if f.check == "incomplete_read"]
        assert len(incomplete) == 2
        assert validation.verdict == "warning"

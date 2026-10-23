"""`fusion vault info` and the MCP `vault_info` tool pinned byte for byte on
recorded fixtures.

Each scenario builds a fully fetched vault snapshot and answers every
eth_call the rendering makes from a fixed table, so the text (ANSI colors
included), the `--json` output and the MCP tool's text are deterministic. The
expected bytes live in `tests/fixtures/vault_info_golden/` (`<scenario>.txt`,
`<scenario>.json`, `<scenario>.mcp.json`); regenerate them with
`UPDATE_GOLDEN=1 uv run pytest tests/test_cli_vault_info_golden.py` only when
an output change is intended (see `tests/_golden.py`).
"""

from __future__ import annotations

import json
from dataclasses import replace
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest
from _golden import FIXTURES as GOLDEN_FIXTURES
from _golden import FrozenDatetime, assert_golden, mcp_tool_text
from _multicall import multicall_aware
from click.testing import CliRunner
from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from test_mcp_hypercore import PENDING
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.types import Timestamp

from ipor_fusion import vault_data, vault_erc20, vault_info
from ipor_fusion.cache import (
    ContractCache,
    FileContractCache,
    NullCache,
    cache_key,
    default_contract_cache,
)
from ipor_fusion.cli import config_store, vault_rendering
from ipor_fusion.cli.config_store import FusionConfig
from ipor_fusion.cli.vault_info_view import (
    _print_vault_info,
)
from ipor_fusion.config.roles import Roles
from ipor_fusion.core.access import RoleAccount
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.fee_manager import HighWaterMarkPerformanceFee, RecipientFee
from ipor_fusion.core.oracle import price_oracle_middleware
from ipor_fusion.core.plasma_vault import BalanceFuse, PlasmaVault
from ipor_fusion.core.withdraw_manager import AccountRequest
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.mcp.models import VaultInfoResponse
from ipor_fusion.readers.aave_v3 import AaveV3PositionBreakdown
from ipor_fusion.readers.hypercore import (
    HyperCoreEffectKind,
    HyperCoreNav,
    HyperCorePerpLeg,
    HyperCorePerpMarket,
    HyperCoreSpotLeg,
    HyperCoreVaultState,
)
from ipor_fusion.readers.lending_health import LendingMarketHealth, VaultLendingHealth
from ipor_fusion.readers.morpho import MorphoPositionBreakdown
from ipor_fusion.readers.unpriceable import MiddlewarePricedToken
from ipor_fusion.types import (
    Amount,
    ChainId,
    Decimals,
    Fee,
    MarketId,
    Period,
    Price,
    RoleId,
    Shares,
    parse_hex_address,
    parse_morpho_market_id,
)
from ipor_fusion.vault_data import (
    GET_TOTAL_SUPPLY_CAP,
    FeeData,
    VaultData,
    WithdrawManagerData,
)
from ipor_fusion.vault_info import build_vault_info_json

FIXTURES = GOLDEN_FIXTURES / "vault_info_golden"


def _addr(suffix: str) -> str:
    return Web3.to_checksum_address("0x" + suffix.rjust(40, "0"))


VAULT = _addr("1111")
ASSET = _addr("a1")
ORACLE = _addr("0c")
ACCESS = _addr("ac")
WITHDRAW_MANAGER = _addr("3d")
TOKEN_STALE = _addr("b1")
TOKEN_UNPRICED = _addr("b2")
TOKEN_EMPTY = _addr("b3")
TOKEN_BROKEN = _addr("b4")
FUSE_AAVE = _addr("f1")
FUSE_MORPHO = _addr("f2")
FUSE_ERC20 = _addr("f3")
FUSE_AAVE_DUP = _addr("f4")
FUSE_ORPHAN = _addr("f5")
PRICE_SOURCE = _addr("5e")
FUSE_HYPERCORE = _addr("f6")
A_TOKEN = _addr("a7")
VARIABLE_DEBT_TOKEN = _addr("a8")
STABLE_DEBT_TOKEN = _addr("a9")
FEE_MANAGER = _addr("fe")
DAO = _addr("da0")
ALPHA = _addr("a1fa")
ATOMIST = _addr("a70")
USER_READY = _addr("e1")
USER_WAITING = _addr("e2")

AAVE = IporFusionMarkets.AAVE_V3
MORPHO = IporFusionMarkets.MORPHO
ERC20_MARKET = IporFusionMarkets.ERC20_VAULT_BALANCE
ORPHAN_MARKET = IporFusionMarkets.COMPOUND_V3_USDC

USDC = 10**6
WAD = 10**18


def _substrate(address: str) -> bytes:
    return bytes(12) + bytes.fromhex(address[2:])


def _selector(signature: str) -> bytes:
    return function_signature_to_4byte_selector(signature)


class _Chain:
    """Fixed answers per (contract, selector, first argument)."""

    def __init__(self, market_balances: dict[int, int], convert_to_assets: int = 0):
        self.market_balances = market_balances
        self.convert_to_assets = convert_to_assets
        self.decimals = {
            ASSET: 6,
            TOKEN_STALE: 18,
            TOKEN_UNPRICED: 18,
            TOKEN_EMPTY: 18,
        }
        self.balances = {
            ASSET: 50 * USDC,
            TOKEN_STALE: 10 * WAD,
            TOKEN_UNPRICED: 3 * WAD,
            TOKEN_EMPTY: 0,
            TOKEN_BROKEN: 7,
        }
        self.prices = {ASSET: WAD, TOKEN_STALE: 2 * WAD, TOKEN_EMPTY: WAD}

    def handle(self, to: str, data: bytes) -> bytes:  # noqa: C901
        to = Web3.to_checksum_address(to)
        selector, args = data[:4], data[4:]
        if to == VAULT and selector == _selector("totalAssetsInMarket(uint256)"):
            (market,) = decode(["uint256"], args)
            return encode(["uint256"], [self.market_balances[market]])
        if to == VAULT and selector == _selector("convertToAssets(uint256)"):
            return encode(["uint256"], [self.convert_to_assets])
        if to == ORACLE and selector == _selector("getAssetPrice(address)"):
            (token,) = decode(["address"], args)
            if (price := self.prices.get(Web3.to_checksum_address(token))) is None:
                raise ContractLogicError("no price")
            return encode(["uint256", "uint256"], [price, 18])
        if selector == _selector("decimals()") and to in self.decimals:
            return encode(["uint8"], [self.decimals[to]])
        if selector == _selector("balanceOf(address)") and to in self.balances:
            return encode(["uint256"], [self.balances[to]])
        raise ContractLogicError(f"unexpected call to {to} 0x{data[:4].hex()}")


def _rich_vault() -> tuple[VaultData, _Chain]:
    lending = VaultLendingHealth(
        markets=[
            LendingMarketHealth(
                protocol="Morpho",
                market_id=MORPHO,
                market_name="wstETH/USDC",
                current_ltv=0.5,
                max_ltv=0.86,
                health_factor=1.72,
                total_collateral_usd=2000.0,
                total_debt_usd=1000.0,
                ltv_usage_percent=58.14,
                substrate_id="ab" * 32,
            ),
            LendingMarketHealth(
                protocol="Aave V3",
                market_id=AAVE,
                market_name="AAVE_V3",
                current_ltv=0.75,
                max_ltv=0.8,
                health_factor=1.08,
                total_collateral_usd=1000.0,
                total_debt_usd=750.0,
                ltv_usage_percent=93.75,
            ),
            LendingMarketHealth(
                protocol="Morpho",
                market_id=MORPHO,
                market_name="WBTC/USDC",
                current_ltv=0.84,
                max_ltv=0.86,
                health_factor=1.02,
                total_collateral_usd=500.0,
                total_debt_usd=420.0,
                ltv_usage_percent=97.67,
                substrate_id="cd" * 32,
            ),
            LendingMarketHealth(
                protocol="Morpho",
                market_id=MORPHO,
                market_name="idle",
                current_ltv=None,
                max_ltv=0.86,
                health_factor=None,
                total_collateral_usd=None,
                total_debt_usd=None,
                ltv_usage_percent=None,
                substrate_id="ef" * 32,
            ),
        ]
    )
    data = VaultData(
        vault=VAULT,
        chain_id=ChainId(1),
        block_number=21_000_000,
        is_latest=False,
        block_timestamp=1_730_000_000,
        share_decimals=8,
        asset_decimals=6,
        total_assets=3_000 * USDC,
        total_supply=2_900 * 10**8,
        supply_cap=2**256 - 1,
        asset=ASSET,
        asset_symbol="USDC",
        access_manager=ACCESS,
        price_oracle=ORACLE,
        rewards_manager=None,
        withdraw_manager=WITHDRAW_MANAGER,
        asset_price_usd=1.0,
        fuses=[FUSE_AAVE, FUSE_MORPHO, FUSE_ORPHAN],
        balance_fuses=[
            BalanceFuse(AAVE, FUSE_AAVE),  # type: ignore[arg-type]
            BalanceFuse(MORPHO, FUSE_MORPHO),  # type: ignore[arg-type]
            BalanceFuse(ERC20_MARKET, FUSE_ERC20),  # type: ignore[arg-type]
            BalanceFuse(AAVE, FUSE_AAVE_DUP),  # type: ignore[arg-type]
        ],
        instant_fuses=[],
        underlying_balance_on_vault=50 * USDC,
        vault_name="Golden Vault",
        withdraw_manager_data=WithdrawManagerData(
            withdraw_window=86_400,
            request_fee=0,
            withdraw_fee=0,
            shares_to_release=40 * 10**8,
            last_release_funds_timestamp=0,
            pending_requests=[],
        ),
        dependency_graph={AAVE: [ERC20_MARKET], MORPHO: []},
        lending_health=lending,
        unpriceable_priced_tokens=[
            MiddlewarePricedToken(
                market_id=MORPHO,
                token=TOKEN_UNPRICED,  # type: ignore[arg-type]
                via="collateral token",
                zero_balance_reverts=True,
            ),
            MiddlewarePricedToken(
                market_id=MORPHO,
                token=TOKEN_UNPRICED,  # type: ignore[arg-type]
                via="loan token",
                zero_balance_reverts=False,
                position_open=True,
                price_source=PRICE_SOURCE,  # type: ignore[arg-type]
            ),
            MiddlewarePricedToken(
                market_id=AAVE,
                token=TOKEN_EMPTY,  # type: ignore[arg-type]
                via="reserve",
                zero_balance_reverts=False,
                position_open=False,
                morpho_collateral=True,
            ),
            MiddlewarePricedToken(
                market_id=AAVE,
                token=TOKEN_STALE,  # type: ignore[arg-type]
                via="reserve",
                zero_balance_reverts=False,
            ),
        ],
        fuse_markets={FUSE_AAVE: AAVE, FUSE_MORPHO: MORPHO, FUSE_ORPHAN: ORPHAN_MARKET},
        market_substrates={
            AAVE: [_substrate(ASSET)],
            MORPHO: [bytes.fromhex("ab" * 32)],
            ERC20_MARKET: [
                _substrate(ASSET),
                _substrate(TOKEN_STALE),
                _substrate(TOKEN_UNPRICED),
                _substrate(TOKEN_EMPTY),
                _substrate(TOKEN_BROKEN),
            ],
        },
        unimplemented_getters=[GET_TOTAL_SUPPLY_CAP],
    )
    chain = _Chain(
        market_balances={
            AAVE: 1_000 * USDC,
            MORPHO: 2_000 * USDC,
            ERC20_MARKET: 5 * USDC,
        },
        convert_to_assets=41 * USDC,
    )
    return data, chain


def _clean_vault() -> tuple[VaultData, _Chain]:
    data = VaultData(
        vault=VAULT,
        chain_id=ChainId(1),
        block_number=100,
        is_latest=True,
        block_timestamp=1_700_000_000,
        share_decimals=6,
        asset_decimals=6,
        total_assets=0,
        total_supply=0,
        supply_cap=1_000_000 * USDC,
        asset=ASSET,
        asset_symbol="USDC",
        access_manager=ACCESS,
        price_oracle=ORACLE,
        rewards_manager=None,
        withdraw_manager=None,
        asset_price_usd=None,
        fuses=[FUSE_AAVE],
        balance_fuses=[BalanceFuse(AAVE, FUSE_AAVE)],  # type: ignore[arg-type]
        instant_fuses=[],
        fuse_markets={FUSE_AAVE: AAVE},
        market_substrates={AAVE: [_substrate(ASSET)]},
    )
    return data, _Chain(market_balances={AAVE: 0})


def _hypercore() -> HyperCoreVaultState:
    nav = HyperCoreNav(
        core_user_exists=True,
        perp_dex_bitmap=2,
        spot=(
            HyperCoreSpotLeg(
                0,
                ASSET,
                824_500,
                0,
                8,
                Price(ASSET, Amount(10**8), Decimals(8)),
                8_245_000_000_000_000,
            ),
        ),
        native_perp=HyperCorePerpLeg(0, 0, 0),
        hip3=(HyperCorePerpLeg(1, -5, -5 * 10**12),),
        value_wad=8_245_000_000_000_000 - 5 * 10**12,
    )
    return HyperCoreVaultState(
        market_id=MarketId(55),
        balance_fuse=FUSE_HYPERCORE,  # type: ignore[arg-type]
        nav=nav,
        balance_fuse_value_wad=nav.value_wad,
        pending=replace(
            PENDING,
            effect_kind=HyperCoreEffectKind.CORE_SPOT,
            timeout_at=1_790_609_822,
            expected_balance=5_000_000_000,
            last_order_l1_block=1_164_341_700,
        ),
        perp_markets=(
            HyperCorePerpMarket(110_002, 1, 10_002, "xyz:NVDA", 15_000_000, False),
        ),
    )


def _full_vault() -> tuple[VaultData, _Chain]:
    """The rich vault with every optional block filled: position breakdowns
    with USD prices, pending withdrawal requests, both exit fees, the fee
    manager, HyperCore, role accounts and a found deployment."""
    data, chain = _rich_vault()
    data.morpho_positions = {
        MORPHO: [
            MorphoPositionBreakdown(
                market_id=parse_morpho_market_id("0x" + "ab" * 32),
                loan_token=ASSET,
                collateral_token=TOKEN_STALE,
                collateral=Amount(3 * WAD),
                borrow_assets=Amount(1_000 * USDC),
                supply_assets=Amount(0),
            ),
            MorphoPositionBreakdown(
                market_id=parse_morpho_market_id("0x" + "ef" * 32),
                loan_token=ASSET,
                collateral_token=TOKEN_EMPTY,
                collateral=Amount(0),
                borrow_assets=Amount(0),
                supply_assets=Amount(2_000 * USDC),
            ),
        ]
    }
    data.aave_positions = {
        AAVE: [
            AaveV3PositionBreakdown(
                asset=ASSET,
                a_token=A_TOKEN,
                variable_debt_token=VARIABLE_DEBT_TOKEN,
                stable_debt_token=STABLE_DEBT_TOKEN,
                supply=Amount(1_750 * USDC),
                variable_debt=Amount(750 * USDC),
                stable_debt=Amount(0),
            )
        ]
    }
    data.token_prices_usd = {
        parse_hex_address(ASSET): 1.0,
        parse_hex_address(TOKEN_STALE): 2.0,
    }
    data.withdraw_manager_data = WithdrawManagerData(
        withdraw_window=Period(86_400),
        request_fee=Fee(10**15),
        withdraw_fee=Fee(2 * 10**15),
        shares_to_release=Shares(40 * 10**8),
        last_release_funds_timestamp=Timestamp(1_729_900_000),
        pending_requests=[
            AccountRequest(
                account=USER_READY,  # type: ignore[arg-type]
                shares=Shares(25 * 10**8),
                end_withdraw_window_timestamp=Timestamp(1_730_050_000),
                can_withdraw=True,
            ),
            AccountRequest(
                account=USER_WAITING,  # type: ignore[arg-type]
                shares=Shares(15 * 10**8),
                end_withdraw_window_timestamp=Timestamp(1_730_090_000),
                can_withdraw=False,
            ),
        ],
    )
    data.fee_data = FeeData(
        performance_fee_vault_bps=Fee(1_000),
        management_fee_vault_bps=Fee(100),
        management_fee_last_update=Timestamp(1_729_990_000),
        unrealized_management_fee=Amount(12 * USDC),
        fee_manager=FEE_MANAGER,  # type: ignore[arg-type]
        ipor_dao_fee_recipient=DAO,  # type: ignore[arg-type]
        deposit_fee_wad=Fee(5 * 10**15),
        performance_fee_manager_bps=Fee(1_000),
        management_fee_manager_bps=Fee(90),
        performance_fee_recipients=[
            RecipientFee(recipient=DAO, fee_value=Fee(200)),  # type: ignore[arg-type]
            RecipientFee(recipient=ATOMIST, fee_value=Fee(800)),  # type: ignore[arg-type]
        ],
        management_fee_recipients=[
            RecipientFee(recipient=ATOMIST, fee_value=Fee(90)),  # type: ignore[arg-type]
        ],
        high_water_mark=HighWaterMarkPerformanceFee(
            high_water_mark=1_020_000,
            last_update=Timestamp(1_729_000_000),
            update_interval=Period(604_800),
        ),
    )
    data.hypercore = _hypercore()
    data.role_accounts = [
        RoleAccount(
            account=ATOMIST,  # type: ignore[arg-type]
            role_id=RoleId(Roles.ATOMIST_ROLE),
            is_member=True,
            execution_delay=Period(0),
        ),
        RoleAccount(
            account=ALPHA,  # type: ignore[arg-type]
            role_id=RoleId(Roles.ALPHA_ROLE),
            is_member=True,
            execution_delay=Period(3_600),
        ),
    ]
    data.deployment_block = 20_500_000
    data.deployment_timestamp = Timestamp(1_723_000_000)
    return data, chain


def _deployment_error_vault() -> tuple[VaultData, _Chain]:
    """The clean vault whose deployment lookup failed."""
    data, chain = _clean_vault()
    data.deployment_error = "rpc-fetch-failed"
    return data, chain


SCENARIOS = {
    "rich": _rich_vault,
    "clean": _clean_vault,
    "full": _full_vault,
    "deployment_error": _deployment_error_vault,
}

NAMES = {
    FUSE_AAVE: "AaveV3BalanceFuse",
    FUSE_MORPHO: "MorphoBalanceFuse",
    FUSE_ERC20: "ERC20BalanceFuse",
    FUSE_AAVE_DUP: "AaveV3BalanceFuse",
    FUSE_ORPHAN: "CompoundV3SupplyFuse",
    FUSE_HYPERCORE: "HyperCoreBalanceFuse",
    ASSET: "FiatTokenProxy",
}


_SYMBOLS = {
    ASSET: "USDC",
    TOKEN_STALE: "STALE",
    TOKEN_UNPRICED: "NOPRICE",
    TOKEN_EMPTY: "EMPTY",
    TOKEN_BROKEN: "BROKEN",
}
_CACHED = {cache_key(ChainId(1), a, "name"): n for a, n in NAMES.items()} | {
    cache_key(ChainId(1), a, "symbol"): s for a, s in _SYMBOLS.items()
}


@pytest.fixture(autouse=True)
def frozen_clock(monkeypatch):
    """Ages in the output are relative to `FROZEN_NOW`, not the wall clock."""
    monkeypatch.setattr(vault_info, "datetime", FrozenDatetime)
    monkeypatch.setattr(vault_rendering, "datetime", FrozenDatetime)


@pytest.fixture
def isolated_cache(tmp_path, monkeypatch):
    cache_dir = tmp_path / ".cache"
    monkeypatch.setattr(config_store, "CONFIG_DIR", tmp_path / ".fusion")
    monkeypatch.setattr(config_store, "CONFIG_FILE", tmp_path / ".fusion" / "c.json")
    monkeypatch.setenv("XDG_CACHE_HOME", str(cache_dir))
    default_contract_cache().put(_CACHED)


def _snapshot(
    scenario: str, cache: ContractCache | None = None
) -> tuple[Web3Context, VaultData]:
    """The scenario's `VaultData` completed from its fake chain by the readers
    `fetch_vault_data` uses: market balances, ERC20 holdings, withdrawal
    assets and token metadata."""
    data, chain = SCENARIOS[scenario]()
    ctx = Web3Context(web3=MagicMock(), chain_id=ChainId(1), cache=cache)
    ctx.call = multicall_aware(chain.handle)  # type: ignore[method-assign]
    vault = PlasmaVault(VAULT)  # type: ignore[arg-type]
    data.market_balances = {
        bf.market_id: vault.total_assets_in_market(bf.market_id).call(ctx)
        for bf in data.balance_fuses
    }
    data.token_symbols, data.token_decimals = vault_data._token_metadata(
        ctx,
        SimpleNamespace(market_substrates=data.market_substrates or {}),  # type: ignore[arg-type]
        SimpleNamespace(  # type: ignore[arg-type]
            morpho_positions=data.morpho_positions, aave_positions=data.aave_positions
        ),
    )
    data.erc20 = vault_erc20._erc20_holdings(
        vault_erc20._Asset(data.asset, data.asset_decimals, data.asset_price_usd),
        vault_erc20._read_erc20_market(
            ctx,
            data.vault,
            balance_fuses=data.balance_fuses,
            market_substrates=data.market_substrates or {},
            market_balances=data.market_balances,
            oracle=price_oracle_middleware(ctx, data.price_oracle),
        ),
        data.token_symbols,
    )
    if (wmd := data.withdraw_manager_data) is not None:
        wmd.shares_to_release_assets = vault.convert_to_assets(
            Shares(wmd.shares_to_release)
        ).call(ctx)
        wmd.pending_request_assets = {
            req.account: vault.convert_to_assets(req.shares).call(ctx)
            for req in wmd.pending_requests or []
        }
    return ctx, data


def _deployment(data: VaultData) -> tuple[int | None, Timestamp | None, str | None]:
    """What `fetch_deployment_info` answers for the scenario."""
    return data.deployment_block, data.deployment_timestamp, data.deployment_error


def _render(
    scenario: str, json_output: bool, cache: ContractCache | None = None
) -> str:
    ctx, data = _snapshot(scenario, cache)
    runner = CliRunner()
    with (
        patch(
            "ipor_fusion.cli.vault_info_view.fetch_deployment_info",
            return_value=_deployment(data),
        ),
        runner.isolation(color=True) as (out, _err, _in),
    ):
        _print_vault_info(ctx, FusionConfig(), data, json_output)
    return out.getvalue().decode()


@pytest.mark.usefixtures("isolated_cache")
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
def test_vault_info_output_matches_fixture(scenario: str, json_output: bool) -> None:
    rendered = _render(scenario, json_output)
    assert_golden(FIXTURES / f"{scenario}.{'json' if json_output else 'txt'}", rendered)
    if json_output:
        json.loads(rendered)


@pytest.mark.usefixtures("isolated_cache")
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_mcp_vault_info_matches_fixture(scenario: str) -> None:
    """The MCP `vault_info` tool, called through the server, emits the pinned
    text for the same snapshot: the CLI document with the response model's
    defaults filled in."""
    ctx, data = _snapshot(scenario)
    with (
        patch("ipor_fusion.mcp.server._build_ctx", return_value=(ctx, None)),
        patch("ipor_fusion.mcp.server.resolve_access_manager"),
        patch("ipor_fusion.mcp.server.fetch_vault_data", return_value=data),
        patch(
            "ipor_fusion.mcp.server.fetch_deployment_info",
            return_value=_deployment(data),
        ),
    ):
        text = mcp_tool_text(
            "vault_info",
            {"vault_address": VAULT, "chain_id": 1},
            VaultInfoResponse,
        )
    assert_golden(FIXTURES / f"{scenario}.mcp.json", text)
    cli_document = json.loads((FIXTURES / f"{scenario}.json").read_text("utf-8"))
    assert VaultInfoResponse.model_validate(cli_document).model_dump_json(indent=2) == (
        FIXTURES / f"{scenario}.mcp.json"
    ).read_text("utf-8")


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
@pytest.mark.parametrize("json_output", [False, True], ids=["text", "json"])
def test_vault_info_reads_only_the_injected_cache(
    scenario: str, json_output: bool, tmp_path: Path
) -> None:
    """Names and symbols come from the context's cache in both outputs; the
    default file cache is never consulted."""
    cache = FileContractCache(tmp_path / "injected.json")
    cache.put(_CACHED)
    untouchable = MagicMock(side_effect=AssertionError("default cache used"))
    with (
        patch("ipor_fusion.explorer.default_contract_cache", untouchable),
        patch("ipor_fusion.core.context.default_contract_cache", untouchable),
    ):
        rendered = _render(scenario, json_output, cache)
    path = FIXTURES / f"{scenario}.{'json' if json_output else 'txt'}"
    assert rendered == path.read_text(encoding="utf-8")


@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_public_builder_names_nothing_without_a_resolver(scenario: str) -> None:
    _, data = _snapshot(scenario, NullCache())
    untouchable = MagicMock(side_effect=AssertionError("default cache used"))
    with patch("ipor_fusion.explorer.default_contract_cache", untouchable):
        result = build_vault_info_json(data)
    fuses = result["fuses"] + result["balance_fuses"]
    assert fuses
    assert {fuse["contract"] for fuse in fuses} == {"?"}


# The CLI's cached lookup matches an address in any spelling; so does this one.
_NAMES_BY_ADDRESS = {address.lower(): name for address, name in NAMES.items()}


@pytest.mark.usefixtures("isolated_cache")
@pytest.mark.parametrize("scenario", sorted(SCENARIOS))
def test_public_builder_renders_the_cli_json(scenario: str) -> None:
    """`build_vault_info_json` with an injected name resolver produces the
    exact document `fusion vault info --json` prints."""
    _, data = _snapshot(scenario)
    result = build_vault_info_json(
        data, resolve_name=lambda address: _NAMES_BY_ADDRESS.get(address.lower(), "")
    )
    expected = (FIXTURES / f"{scenario}.json").read_text(encoding="utf-8")
    assert json.dumps(result, indent=2) + "\n" == expected

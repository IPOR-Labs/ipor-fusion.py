# pyright: reportOperatorIssue=false, reportOptionalOperand=false
"""Unit tests for lending health computation — mock on-chain calls."""

from concurrent.futures import ThreadPoolExecutor
from unittest.mock import MagicMock, patch

import pytest
from _multicall import multicall_aware, sequenced
from _validation import validate
from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.exceptions import ContractLogicError, Web3RPCError, Web3ValueError

from ipor_fusion import vault_data
from ipor_fusion.cli.vault_markets_view import (
    _index_lending_health,
    _print_lending_health,
)
from ipor_fusion.core.plasma_vault import BalanceFuse, PlasmaVault
from ipor_fusion.fuses.euler_v2 import euler_substrate
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.readers.aave_v3 import AaveV3Reader
from ipor_fusion.readers.euler_v2 import EULER_USD_UNIT_OF_ACCOUNT, EulerV2Substrate
from ipor_fusion.readers.lending_health import (
    AAVE_V3_MARKET_IDS,
    AAVE_V3_POOL,
    AAVE_V4_MARKET_IDS,
    COMPOUND_V3_MARKET_IDS,
    EULER_V2_MARKET_IDS,
    LIDO_LEND_MARKET_IDS,
    MORPHO_MARKET_IDS,
    ORACLE_PRICE_SCALE,
    WAD,
    LendingMarketHealth,
    UnreadableLendingMarket,
    VaultLendingHealth,
    _collect_lending_markets,
    _compute_aave_market_health,
    _compute_aave_v4_market_health,
    _compute_compound_v3_market_health,
    _compute_euler_v2_account_health,
    _compute_morpho_market_health,
    _EulerV2Account,
    fetch_vault_lending_health,
)
from ipor_fusion.readers.lido_lend import LidoLendReader
from ipor_fusion.readers.morpho import (
    MorphoReader,
    morpho_blue_address,
    to_assets_down,
    to_assets_up,
)
from ipor_fusion.readers.unpriceable import UnpriceableCheck
from ipor_fusion.types import ChainId, MarketId, MorphoBlueMarketId
from ipor_fusion.vault_positions import (
    _fetch_lido_lend_cores,
    _fetch_lido_lend_positions,
)

VAULT_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
ORACLE_ADDR = Web3.to_checksum_address("0xdDdDddDdDdddDDddDDddDDDDdDdDDdDDdDDDDDDd")
TOKEN_A = Web3.to_checksum_address("0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB")
TOKEN_B = Web3.to_checksum_address("0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC")
IRM_ADDR = Web3.to_checksum_address("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE")
MORPHO_MARKET_ID = MorphoBlueMarketId("a" * 64)

AAVE_V4_SPOKE = Web3.to_checksum_address("0x17905db0e4a3514467539956c084180616ae7b8d")
AAVE_V4_SPOKE_B = Web3.to_checksum_address("0x" + "5b" * 20)
AAVE_V4_ORACLE = Web3.to_checksum_address("0xaBaf048fD7675Ea34a84332371ffd5D55E322A47")
_SELECTOR_ACCOUNT_DATA = function_signature_to_4byte_selector(
    "getUserAccountData(address)"
)
_SELECTOR_ORACLE = function_signature_to_4byte_selector("ORACLE()")
_SELECTOR_DECIMALS = function_signature_to_4byte_selector("decimals()")

# Live `getUserAccountData` of vault 0x31744e44…641b ("Apple Carry Trade",
# Base) on its Spoke, 2026-10-09: AAPLc collateral, USDC debt, 8-decimal oracle.
APPLE_CARRY_ACCOUNT = (
    0,
    780_000_000_000_000_000,
    1_468_096_760_188_303_344,
    5_015_412_721_457_600_000_000_000_000_000,
    2_664_689_432_483_427_092_993_009_501_428_797_296_391_519_279_000_000_000_000,
    1,
    1,
)
NO_DEBT_ACCOUNT = (0, 780_000_000_000_000_000, 2**256 - 1, 10**30, 0, 1, 0)


def _aave_v4_ctx(accounts: dict[str, tuple[int, ...]]) -> MagicMock:
    """Answer Spoke and oracle reads; `accounts` maps Spoke -> account tuple."""

    def call(to, data, *_):
        selector = bytes(data[:4])
        if selector == _SELECTOR_ACCOUNT_DATA:
            return encode(["uint256"] * 7, list(accounts[to]))
        if selector == _SELECTOR_ORACLE:
            return encode(["address"], [AAVE_V4_ORACLE])
        assert selector == _SELECTOR_DECIMALS and to == AAVE_V4_ORACLE
        return encode(["uint8"], [8])

    ctx = MagicMock()
    ctx.call.side_effect = call
    return ctx


def _aave_v4_substrate(spoke: str, reserve_id: int, flags: int) -> bytes:
    """AaveV4SubstrateLib: type 1 << 248 | spoke << 88 | reserveId << 56 | flags << 48."""
    word = 1 << 248 | int(spoke, 16) << 88 | reserve_id << 56 | flags << 48
    return word.to_bytes(32, "big")


def _make_morpho_reader():
    ctx = MagicMock()
    return MorphoReader(morpho_blue_address(ChainId(1))), ctx


def _make_aave_reader():
    ctx = MagicMock()
    pool = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")
    return AaveV3Reader(pool), ctx


def _aave_account_data(health_factor_wad: int) -> bytes:
    """getUserAccountData(): $100 collateral, $80 debt, 85% liquidation threshold."""
    return encode(
        ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"],
        [100_000_000_00, 80_000_000_00, 0, 8500, 8000, health_factor_wad],
    )


# ── SharesMathLib ─────────────────────────────────────────────────────

# Vectors worked by hand from Morpho Blue's SharesMathLib (VIRTUAL_SHARES = 1e6,
# VIRTUAL_ASSETS = 1): toAssetsDown = shares * (assets + 1) / (shares_total + 1e6)
# rounded down, toAssetsUp the same rounded up (mulDivUp).


class TestSharesMath:
    def test_zero_shares(self):
        assert to_assets_up(0, 1000, 1000) == 0
        assert to_assets_down(0, 1000, 1000) == 0

    def test_an_empty_market_still_divides_by_the_virtual_shares(self):
        # 100 * 1001 / 1_000_000 = 0.1001
        assert to_assets_up(100, 1000, 0) == 1
        assert to_assets_down(100, 1000, 0) == 0

    def test_a_tiny_pool_is_dominated_by_the_virtual_shares(self):
        # 3 * 1001 / 1_000_003 = 0.003003
        assert to_assets_up(3, 1000, 3) == 1
        assert to_assets_down(3, 1000, 3) == 0

    def test_realistic_share_scale_converts_back_to_assets(self):
        # Shares minted at 1e6 per asset, then 250 assets of interest:
        # 500e6 * 1_500_251 / (1.5e12 + 1e6) = 500.0833..., so up 501, down 500.
        assert to_assets_up(500 * 10**6, 1_500_250, 1_500_000 * 10**6) == 501
        assert to_assets_down(500 * 10**6, 1_500_250, 1_500_000 * 10**6) == 500

    def test_large_values_keep_integer_precision(self):
        # shares / total_shares = 1 / 1000 of 987654321987654321987 assets (+1):
        # 987654321987654321.988 before the virtual-share correction, so the
        # exact results straddle ...321 / ...322; a float division loses the
        # last two digits (it gave 987654321987654272).
        shares = 123456789123456789123456789
        total_shares = shares * 1000
        assets = 987654321987654321987
        assert to_assets_up(shares, assets, total_shares) == 987654321987654322
        assert to_assets_down(shares, assets, total_shares) == 987654321987654321


# ── LendingMarketHealth properties ───────────────────────────────────


class TestLendingMarketHealthProperties:
    def test_no_warning_when_healthy(self):
        m = LendingMarketHealth(
            protocol="morpho",
            market_id=14,
            market_name="MORPHO",
            current_ltv=0.5,
            max_ltv=0.86,
            health_factor=1.72,
            total_collateral_usd=None,
            total_debt_usd=None,
            ltv_usage_percent=58.14,
        )
        assert not m.is_warning
        assert not m.is_critical

    def test_warning_when_hf_below_1_1(self):
        m = LendingMarketHealth(
            protocol="morpho",
            market_id=14,
            market_name="MORPHO",
            current_ltv=0.69,
            max_ltv=0.86,
            health_factor=1.09,
            total_collateral_usd=None,
            total_debt_usd=None,
            ltv_usage_percent=80.0,
        )
        assert m.is_warning
        assert not m.is_critical

    def test_no_warning_at_hf_exactly_1_1(self):
        m = LendingMarketHealth(
            protocol="morpho",
            market_id=14,
            market_name="MORPHO",
            current_ltv=0.69,
            max_ltv=0.86,
            health_factor=1.1,
            total_collateral_usd=None,
            total_debt_usd=None,
            ltv_usage_percent=80.0,
        )
        assert not m.is_warning
        assert not m.is_critical

    def test_critical_when_hf_at_1_05(self):
        m = LendingMarketHealth(
            protocol="morpho",
            market_id=14,
            market_name="MORPHO",
            current_ltv=0.774,
            max_ltv=0.86,
            health_factor=1.05,
            total_collateral_usd=None,
            total_debt_usd=None,
            ltv_usage_percent=90.0,
        )
        assert m.is_warning
        assert m.is_critical

    def test_critical_when_hf_below_1_05(self):
        m = LendingMarketHealth(
            protocol="morpho",
            market_id=14,
            market_name="MORPHO",
            current_ltv=0.82,
            max_ltv=0.86,
            health_factor=1.02,
            total_collateral_usd=None,
            total_debt_usd=None,
            ltv_usage_percent=95.0,
        )
        assert m.is_warning
        assert m.is_critical

    def test_none_hf_no_warning(self):
        m = LendingMarketHealth(
            protocol="morpho",
            market_id=14,
            market_name="MORPHO",
            current_ltv=None,
            max_ltv=0.86,
            health_factor=None,
            total_collateral_usd=None,
            total_debt_usd=None,
            ltv_usage_percent=None,
        )
        assert not m.is_warning
        assert not m.is_critical


# ── VaultLendingHealth properties ─────────────────────────────────────


class TestVaultLendingHealthProperties:
    def test_empty(self):
        vlh = VaultLendingHealth(markets=[])
        assert not vlh.has_lending_positions
        assert vlh.worst_ltv_usage is None

    def test_worst_usage(self):
        m1 = LendingMarketHealth(
            protocol="morpho",
            market_id=14,
            market_name="MORPHO",
            current_ltv=0.5,
            max_ltv=0.86,
            health_factor=1.72,
            total_collateral_usd=None,
            total_debt_usd=None,
            ltv_usage_percent=58.14,
        )
        m2 = LendingMarketHealth(
            protocol="aave_v3",
            market_id=1,
            market_name="AAVE_V3",
            current_ltv=0.7,
            max_ltv=0.85,
            health_factor=1.21,
            total_collateral_usd=1000.0,
            total_debt_usd=700.0,
            ltv_usage_percent=82.35,
        )
        vlh = VaultLendingHealth(markets=[m1, m2])
        assert vlh.has_lending_positions
        assert vlh.worst_ltv_usage == 82.35


# ── Morpho health computation ────────────────────────────────────────


class TestComputeMorphoMarketHealth:
    def test_no_borrow_returns_zero_ltv(self):
        reader, ctx = _make_morpho_reader()

        # position: no borrow
        ctx.call.side_effect = sequenced(
            [
                # position(market_id, user) → supply_shares=1000, borrow_shares=0, collateral=500
                encode(["uint256", "uint128", "uint128"], [1000, 0, 500]),
                # market(market_id) — read in the same batch, unused without a borrow
                encode(
                    ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
                    [2000, 2000, 0, 0, 1700000000, 0],
                ),
                # market_params(market_id) → lltv=0.86e18
                encode(
                    ["address", "address", "address", "address", "uint256"],
                    [TOKEN_A, TOKEN_B, ORACLE_ADDR, IRM_ADDR, 860000000000000000],
                ),
            ]
        )

        result = _compute_morpho_market_health(
            ctx, reader, MORPHO_MARKET_ID, VAULT_ADDR, 14, "MORPHO"
        )

        assert isinstance(result, LendingMarketHealth)
        assert result.protocol == "morpho"
        assert result.current_ltv == 0.0
        assert result.max_ltv == 0.86
        assert result.ltv_usage_percent == 0.0
        assert not result.is_warning

    def test_active_borrow_computes_ltv(self):
        reader, ctx = _make_morpho_reader()

        # 50% LTV scenario:
        # borrow_shares=500e6, total_borrow_assets=1000, total_borrow_shares=1000e6
        # (Morpho mints 1e6 shares per asset) → borrowed = 500
        # collateral=1000, oracle_price=1e36 (1:1 price)
        # → collateral_value = 1000
        # → current_ltv = 500/1000 = 0.5
        # lltv = 0.86 → usage = 0.5/0.86 ≈ 58.14%

        ctx.call.side_effect = sequenced(
            [
                # position
                encode(["uint256", "uint128", "uint128"], [0, 500 * 10**6, 1000]),
                # market
                encode(
                    ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
                    [2000, 2000 * 10**6, 1000, 1000 * 10**6, 1700000000, 0],
                ),
                # market_params
                encode(
                    ["address", "address", "address", "address", "uint256"],
                    [TOKEN_A, TOKEN_B, ORACLE_ADDR, IRM_ADDR, 860000000000000000],
                ),
                # oracle price() → 1e36 (1:1)
                encode(["uint256"], [ORACLE_PRICE_SCALE]),
            ]
        )

        result = _compute_morpho_market_health(
            ctx, reader, MORPHO_MARKET_ID, VAULT_ADDR, 14, "MORPHO"
        )

        assert isinstance(result, LendingMarketHealth)
        assert result.protocol == "morpho"
        assert 0.49 < result.current_ltv < 0.51
        assert result.max_ltv == 0.86
        assert result.health_factor is not None
        assert result.health_factor > 1.0
        assert 55.0 < result.ltv_usage_percent < 62.0
        assert not result.is_warning

    def test_high_ltv_triggers_warning(self):
        reader, ctx = _make_morpho_reader()

        # ~83% LTV → 83/86 ≈ 96.5% usage → critical
        # borrowed ≈ 830, collateral=1000, oracle_price=1e36
        ctx.call.side_effect = sequenced(
            [
                encode(["uint256", "uint128", "uint128"], [0, 830 * 10**6, 1000]),
                encode(
                    ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
                    [2000, 2000 * 10**6, 1000, 1000 * 10**6, 1700000000, 0],
                ),
                encode(
                    ["address", "address", "address", "address", "uint256"],
                    [TOKEN_A, TOKEN_B, ORACLE_ADDR, IRM_ADDR, 860000000000000000],
                ),
                encode(["uint256"], [ORACLE_PRICE_SCALE]),
            ]
        )

        result = _compute_morpho_market_health(
            ctx, reader, MORPHO_MARKET_ID, VAULT_ADDR, 14, "MORPHO"
        )

        assert isinstance(result, LendingMarketHealth)
        assert result.is_critical
        assert result.is_warning

    def test_zero_collateral_returns_none_ltv(self):
        reader, ctx = _make_morpho_reader()

        ctx.call.side_effect = sequenced(
            [
                encode(["uint256", "uint128", "uint128"], [0, 100, 0]),
                encode(
                    ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
                    [2000, 2000, 1000, 1000, 1700000000, 0],
                ),
                encode(
                    ["address", "address", "address", "address", "uint256"],
                    [TOKEN_A, TOKEN_B, ORACLE_ADDR, IRM_ADDR, 860000000000000000],
                ),
                encode(["uint256"], [ORACLE_PRICE_SCALE]),
            ]
        )

        result = _compute_morpho_market_health(
            ctx, reader, MORPHO_MARKET_ID, VAULT_ADDR, 14, "MORPHO"
        )

        assert isinstance(result, LendingMarketHealth)
        assert result.current_ltv is None
        assert result.ltv_usage_percent is None

    def test_position_read_failure_is_unreadable(self):
        reader, ctx = _make_morpho_reader()
        ctx.call.side_effect = Web3RPCError("RPC error")

        result = _compute_morpho_market_health(
            ctx, reader, MORPHO_MARKET_ID, VAULT_ADDR, 14, "MORPHO"
        )

        assert isinstance(result, UnreadableLendingMarket)
        assert "read failed: Web3RPCError" in result.reason

    def test_market_data_failure_is_unreadable(self):
        reader, ctx = _make_morpho_reader()
        # First call succeeds (position), second fails (market)
        ctx.call.side_effect = sequenced(
            [
                encode(["uint256", "uint128", "uint128"], [0, 100, 1000]),
                Web3RPCError("market read failed"),
            ]
        )

        result = _compute_morpho_market_health(
            ctx, reader, MORPHO_MARKET_ID, VAULT_ADDR, 14, "MORPHO"
        )

        assert isinstance(result, UnreadableLendingMarket)
        assert "read failed: Web3RPCError" in result.reason


# ── Aave V3 health computation ───────────────────────────────────────


class TestComputeAaveMarketHealth:
    def test_no_debt_returns_zero_ltv(self):
        reader, ctx = _make_aave_reader()

        # total_collateral=10e8, debt=0, available=8e8, liq_threshold=8500, ltv=8000, hf=max
        ctx.call.return_value = encode(
            ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"],
            [10_000_000_00, 0, 8_000_000_00, 8500, 8000, 2**256 - 1],
        )

        result = _compute_aave_market_health(ctx, reader, VAULT_ADDR, 1, "AAVE_V3")

        assert isinstance(result, LendingMarketHealth)
        assert result.protocol == "aave_v3"
        assert result.current_ltv == 0.0
        assert result.ltv_usage_percent == 0.0
        assert result.total_collateral_usd == 10.0  # 10_000_000_00 / 1e8
        assert result.total_debt_usd == 0.0

    def test_active_debt_computes_health(self):
        reader, ctx = _make_aave_reader()

        # collateral=100e8 ($100), debt=50e8 ($50)
        # liq_threshold=8500 (85%), ltv=8000 (80%)
        # current_ltv = 50/100 = 0.5, usage = 0.5/0.85 ≈ 58.8%
        # health_factor = 1.7e18
        ctx.call.return_value = encode(
            ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"],
            [
                100_000_000_00,
                50_000_000_00,
                30_000_000_00,
                8500,
                8000,
                1_700_000_000_000_000_000,
            ],
        )

        result = _compute_aave_market_health(ctx, reader, VAULT_ADDR, 1, "AAVE_V3")

        assert isinstance(result, LendingMarketHealth)
        assert result.current_ltv == 0.5
        assert result.max_ltv == 0.85
        assert result.health_factor == 1.7
        assert 58.0 < result.ltv_usage_percent < 59.0
        assert not result.is_warning

    def test_high_ltv_triggers_warning(self):
        reader, ctx = _make_aave_reader()

        # collateral=100e8, debt=80e8 → LTV=0.8, liq_threshold=85% → usage=94.1%
        # health_factor=1.0625 → < 1.1 (warning) but > 1.05 (not critical)
        ctx.call.return_value = encode(
            ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"],
            [
                100_000_000_00,
                80_000_000_00,
                5_000_000_00,
                8500,
                8000,
                1_062_500_000_000_000_000,
            ],
        )

        result = _compute_aave_market_health(ctx, reader, VAULT_ADDR, 1, "AAVE_V3")

        assert isinstance(result, LendingMarketHealth)
        assert result.is_warning
        assert not result.is_critical
        assert result.health_factor == 1.0625

    def test_very_high_ltv_triggers_critical(self):
        reader, ctx = _make_aave_reader()

        # health_factor=1.02 → <= 1.05 → critical
        ctx.call.return_value = encode(
            ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"],
            [
                100_000_000_00,
                83_000_000_00,
                2_000_000_00,
                8500,
                8000,
                1_020_000_000_000_000_000,
            ],
        )

        result = _compute_aave_market_health(ctx, reader, VAULT_ADDR, 1, "AAVE_V3")

        assert isinstance(result, LendingMarketHealth)
        assert result.is_critical
        assert result.is_warning
        assert result.health_factor == 1.02

    def test_reader_failure_is_unreadable(self):
        reader, ctx = _make_aave_reader()
        ctx.call.side_effect = Web3RPCError("RPC error")

        result = _compute_aave_market_health(ctx, reader, VAULT_ADDR, 1, "AAVE_V3")

        assert isinstance(result, UnreadableLendingMarket)
        assert "read failed: Web3RPCError" in result.reason


class TestComputeAaveV4MarketHealth:
    def test_live_carry_position(self):
        ctx = _aave_v4_ctx({AAVE_V4_SPOKE: APPLE_CARRY_ACCOUNT})

        result = _compute_aave_v4_market_health(
            ctx, AAVE_V4_SPOKE, VAULT_ADDR, 49, "AAVE_V4"
        )

        assert isinstance(result, LendingMarketHealth)
        assert result.protocol == "aave_v4"
        assert result.substrate_id == AAVE_V4_SPOKE
        assert result.total_collateral_usd == 50154.13
        assert result.total_debt_usd == 26646.89
        assert result.current_ltv == 0.5313
        assert result.max_ltv == 0.78
        assert result.health_factor == 1.4681
        assert result.ltv_usage_percent == 68.12
        assert not result.is_warning

    def test_no_borrow_has_no_health_factor(self):
        ctx = _aave_v4_ctx({AAVE_V4_SPOKE: NO_DEBT_ACCOUNT})

        result = _compute_aave_v4_market_health(
            ctx, AAVE_V4_SPOKE, VAULT_ADDR, 49, "AAVE_V4"
        )

        assert isinstance(result, LendingMarketHealth)
        assert result.health_factor is None
        assert result.current_ltv == 0.0
        assert result.ltv_usage_percent == 0.0
        assert result.total_collateral_usd == 10_000.0
        assert result.total_debt_usd == 0.0

    def test_debt_without_collateral_is_critical(self):
        account = (0, 0, 0, 0, 10**27 * 10**26, 0, 1)
        ctx = _aave_v4_ctx({AAVE_V4_SPOKE: account})

        result = _compute_aave_v4_market_health(
            ctx, AAVE_V4_SPOKE, VAULT_ADDR, 49, "AAVE_V4"
        )

        assert isinstance(result, LendingMarketHealth)
        assert result.current_ltv is None
        assert result.ltv_usage_percent is None
        assert result.total_debt_usd == 1.0
        assert result.is_critical

    def test_read_failure_is_unreadable(self):
        ctx = MagicMock()
        ctx.call.side_effect = Web3RPCError("RPC error")

        result = _compute_aave_v4_market_health(
            ctx, AAVE_V4_SPOKE, VAULT_ADDR, 49, "AAVE_V4"
        )

        assert isinstance(result, UnreadableLendingMarket)
        assert "read failed: Web3RPCError" in result.reason


# ── fetch_vault_lending_health integration ───────────────────────────


class TestFetchVaultLendingHealth:
    def test_no_lending_markets_returns_empty(self):
        ctx = MagicMock()
        result = fetch_vault_lending_health(
            ctx,
            VAULT_ADDR,
            balance_fuse_market_ids=[
                7,
                12,
            ],  # ERC20_VAULT_BALANCE, UNIVERSAL_TOKEN_SWAPPER
            market_substrates={},
        )
        assert not result.has_lending_positions

    def test_morpho_market_ids_recognized(self):
        assert IporFusionMarkets.MORPHO in MORPHO_MARKET_IDS
        # Flash loans, rewards, and liquidity-in-markets are excluded:
        # no persistent borrow risk
        assert IporFusionMarkets.MORPHO_FLASH_LOAN not in MORPHO_MARKET_IDS
        assert IporFusionMarkets.MORPHO_REWARDS not in MORPHO_MARKET_IDS

    def test_aave_market_ids_recognized(self):
        assert IporFusionMarkets.AAVE_V3 in AAVE_V3_MARKET_IDS
        assert IporFusionMarkets.AAVE_V3_LIDO in AAVE_V3_MARKET_IDS
        assert IporFusionMarkets.SPARK_LEND in AAVE_V3_MARKET_IDS

    def test_each_aave_market_reads_its_own_pool(self):
        """Aave V3 Core, Prime and SparkLend are separate Pools: one account-level
        row per market, each from its own Pool — never one Pool's figures
        repeated under another market's label."""
        pools = {
            IporFusionMarkets.AAVE_V3: Web3.to_checksum_address("0x" + "a1" * 20),
            IporFusionMarkets.AAVE_V3_LIDO: Web3.to_checksum_address("0x" + "a2" * 20),
            IporFusionMarkets.SPARK_LEND: Web3.to_checksum_address("0x" + "a3" * 20),
        }
        health_factor_wad = {
            pools[IporFusionMarkets.AAVE_V3]: 1_057_200_000_000_000_000,
            pools[IporFusionMarkets.AAVE_V3_LIDO]: 1_073_900_000_000_000_000,
            pools[IporFusionMarkets.SPARK_LEND]: 1_035_000_000_000_000_000,
        }
        ctx = MagicMock()
        ctx.call.side_effect = lambda to, _data: _aave_account_data(
            health_factor_wad[to]
        )

        result = fetch_vault_lending_health(
            ctx,
            VAULT_ADDR,
            balance_fuse_market_ids=list(pools),
            market_substrates={},
            aave_pools=pools,
        )

        by_market = {m.market_id: m for m in result.markets}
        assert by_market.keys() == set(pools)
        assert by_market[IporFusionMarkets.AAVE_V3].health_factor == 1.0572
        assert by_market[IporFusionMarkets.AAVE_V3_LIDO].health_factor == 1.0739
        spark = by_market[IporFusionMarkets.SPARK_LEND]
        assert spark.health_factor == 1.035
        assert spark.market_name == "SPARK_LEND"
        assert spark.is_critical

    def test_default_pools_cover_core_market_only(self):
        """Without `aave_pools` only AAVE_V3 has a known Pool (the chain's Core
        pool); Prime and SparkLend are skipped rather than shown with Core's
        figures."""
        ctx = MagicMock(chain_id=ChainId(1))
        ctx.call.return_value = _aave_account_data(1_700_000_000_000_000_000)

        result = fetch_vault_lending_health(
            ctx,
            VAULT_ADDR,
            balance_fuse_market_ids=[
                IporFusionMarkets.AAVE_V3,
                IporFusionMarkets.AAVE_V3_LIDO,
                IporFusionMarkets.SPARK_LEND,
            ],
            market_substrates={},
        )

        assert [m.market_id for m in result.markets] == [IporFusionMarkets.AAVE_V3]
        ctx.call.assert_called_once()
        assert ctx.call.call_args.args[0] == AAVE_V3_POOL[1]

    @patch("ipor_fusion.readers.lending_health.market_name", return_value="Morpho")
    @patch(
        "ipor_fusion.readers.lending_health._compute_morpho_market_health",
        return_value=LendingMarketHealth(
            protocol="morpho",
            market_id=14,
            market_name="Morpho",
            current_ltv=0.5,
            max_ltv=0.86,
            health_factor=1.72,
            total_collateral_usd=None,
            total_debt_usd=None,
            ltv_usage_percent=58.14,
        ),
    )
    def test_morpho_returns_result_via_threadpool(self, mock_compute, mock_name):
        ctx = MagicMock()
        ctx.chain_id = 1
        morpho_mid = IporFusionMarkets.MORPHO
        substrate = bytes.fromhex("ab" * 32)

        result = fetch_vault_lending_health(
            ctx,
            VAULT_ADDR,
            balance_fuse_market_ids=[morpho_mid],
            market_substrates={morpho_mid: [substrate]},
        )
        assert result.has_lending_positions
        assert len(result.markets) == 1
        assert result.markets[0].protocol == "morpho"

    def test_aave_v4_market_ids_recognized(self):
        assert IporFusionMarkets.AAVE_V4 in AAVE_V4_MARKET_IDS

    def test_aave_v4_one_row_per_spoke(self):
        """Health is account-level per Spoke: reserves of one Spoke collapse
        into one row, and substrates of another type are skipped."""
        mid = IporFusionMarkets.AAVE_V4
        substrates = [
            _aave_v4_substrate(AAVE_V4_SPOKE, 7, 0b10),
            _aave_v4_substrate(AAVE_V4_SPOKE, 0, 0b01),
            _aave_v4_substrate(AAVE_V4_SPOKE_B, 3, 0b11),
            (2 << 248).to_bytes(32, "big"),
        ]
        ctx = _aave_v4_ctx(
            {AAVE_V4_SPOKE: APPLE_CARRY_ACCOUNT, AAVE_V4_SPOKE_B: NO_DEBT_ACCOUNT}
        )

        result = fetch_vault_lending_health(
            ctx,
            VAULT_ADDR,
            balance_fuse_market_ids=[mid],
            market_substrates={mid: substrates},
        )

        by_spoke = {m.substrate_id: m for m in result.markets}
        assert by_spoke.keys() == {AAVE_V4_SPOKE, AAVE_V4_SPOKE_B}
        assert by_spoke[AAVE_V4_SPOKE].health_factor == 1.4681
        assert by_spoke[AAVE_V4_SPOKE_B].health_factor is None
        assert all(m.market_name == "AAVE_V4" for m in result.markets)
        assert result.worst_ltv_usage == 68.12

    def test_an_aave_market_without_a_pool_is_unreadable(self):
        ctx = MagicMock()
        # Chain 999999 has no Aave V3 pool configured
        result = fetch_vault_lending_health(
            ctx,
            VAULT_ADDR,
            balance_fuse_market_ids=[1],  # AAVE_V3
            market_substrates={},
        )
        assert result.markets == []
        (unreadable,) = result.unreadable
        assert unreadable.protocol == "aave_v3"
        assert not result.is_complete

    @pytest.mark.parametrize("error", [TypeError("bug"), Web3ValueError("bug")])
    def test_a_programming_error_is_not_swallowed(self, error):
        ctx = MagicMock()
        ctx.call.side_effect = error
        with pytest.raises(type(error), match="bug"):
            fetch_vault_lending_health(
                ctx,
                VAULT_ADDR,
                balance_fuse_market_ids=[1],
                market_substrates={},
                aave_pools={MarketId(1): AAVE_V3_POOL[ChainId(1)]},
            )


# ── Health check integration with lending_health ─────────────────────


class TestHealthCheckLendingWarnings:
    def test_lending_warnings_in_health_check(self):
        from ipor_fusion.vault_data import VaultData

        addr = Web3.to_checksum_address("0x" + "11" * 20)
        lending = VaultLendingHealth(
            markets=[
                LendingMarketHealth(
                    protocol="morpho",
                    market_id=14,
                    market_name="MORPHO",
                    current_ltv=0.78,
                    max_ltv=0.86,
                    health_factor=1.03,
                    total_collateral_usd=None,
                    total_debt_usd=None,
                    ltv_usage_percent=90.7,
                ),
            ]
        )

        data = VaultData(
            vault=VAULT_ADDR,
            chain_id=ChainId(1),
            block_number=123,
            is_latest=True,
            block_timestamp=1700000000,
            share_decimals=18,
            asset_decimals=18,
            total_assets=100 * 10**18,
            total_supply=100 * 10**18,
            supply_cap=0,
            asset=addr,
            vault_name="Test",
            asset_symbol="USDC",
            access_manager=addr,
            price_oracle=addr,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=1.0,
            fuses=[],
            balance_fuses=[],
            instant_fuses=[],
            lending_health=lending,
        )

        health = validate(data, 100 * 10**18)

        assert len(health.criticals) == 1
        assert "NEAR LIQUIDATION" in health.criticals[0]
        assert not any("CRITICAL" in w for w in health.warnings)

    def test_healthy_lending_in_ok(self):
        from ipor_fusion.vault_data import VaultData

        addr = Web3.to_checksum_address("0x" + "11" * 20)
        lending = VaultLendingHealth(
            markets=[
                LendingMarketHealth(
                    protocol="aave_v3",
                    market_id=1,
                    market_name="AAVE_V3",
                    current_ltv=0.3,
                    max_ltv=0.85,
                    health_factor=2.83,
                    total_collateral_usd=1000.0,
                    total_debt_usd=300.0,
                    ltv_usage_percent=35.29,
                ),
            ]
        )

        data = VaultData(
            vault=VAULT_ADDR,
            chain_id=ChainId(1),
            block_number=123,
            is_latest=True,
            block_timestamp=1700000000,
            share_decimals=18,
            asset_decimals=18,
            total_assets=100 * 10**18,
            total_supply=100 * 10**18,
            supply_cap=0,
            asset=addr,
            vault_name="Test",
            asset_symbol="USDC",
            access_manager=addr,
            price_oracle=addr,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=1.0,
            fuses=[],
            balance_fuses=[],
            instant_fuses=[],
            lending_health=lending,
        )

        health = validate(data, 100 * 10**18)

        assert any("aave_v3" in ok and "35.3%" in ok for ok in health.ok)
        assert not any("CRITICAL" in w or "WARNING" in w for w in health.warnings)

    def test_no_lending_health_no_warnings(self):
        from ipor_fusion.vault_data import VaultData

        addr = Web3.to_checksum_address("0x" + "11" * 20)

        data = VaultData(
            vault=VAULT_ADDR,
            chain_id=ChainId(1),
            block_number=123,
            is_latest=True,
            block_timestamp=1700000000,
            share_decimals=18,
            asset_decimals=18,
            total_assets=100 * 10**18,
            total_supply=100 * 10**18,
            supply_cap=0,
            asset=addr,
            vault_name="Test",
            asset_symbol="USDC",
            access_manager=addr,
            price_oracle=addr,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=1.0,
            fuses=[],
            balance_fuses=[],
            instant_fuses=[],
            lending_health=None,
        )

        health = validate(data, 100 * 10**18)

        # Only the standard reconciliation check, no lending warnings
        assert not any("CRITICAL" in w for w in health.warnings)


class TestOrphanFuseMarketsHealthCheck:
    """The CLI must flag a critical when an action fuse claims a market that
    has no balance fuse — positions opened via that fuse silently bypass
    totalAssets accounting (the misconfiguration scenario reported by users)."""

    def _make_data(
        self,
        fuse_markets,
        balance_fuses,
        dependency_graph=None,
        market_substrates=None,
    ):
        from ipor_fusion.vault_data import VaultData

        addr = Web3.to_checksum_address("0x" + "11" * 20)
        return VaultData(
            vault=VAULT_ADDR,
            chain_id=ChainId(1),
            block_number=123,
            is_latest=True,
            block_timestamp=1700000000,
            share_decimals=18,
            asset_decimals=18,
            total_assets=100 * 10**18,
            total_supply=100 * 10**18,
            supply_cap=0,
            asset=addr,
            vault_name="Test",
            asset_symbol="USDC",
            access_manager=addr,
            price_oracle=addr,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=1.0,
            fuses=list(fuse_markets.keys()) if fuse_markets else [],
            balance_fuses=balance_fuses,
            instant_fuses=[],
            fuse_markets=fuse_markets,
            dependency_graph=dependency_graph,
            market_substrates=market_substrates,
        )

    @staticmethod
    def _erc20_substrate(addr: str) -> bytes:
        """32-byte substrate word holding a token address in the low 20 bytes."""
        return bytes.fromhex(addr.lower().removeprefix("0x").rjust(64, "0"))

    def test_morpho_orphan_marked_critical(self):
        from ipor_fusion.core.plasma_vault import BalanceFuse

        fuse_addr = "0x" + "aa" * 20
        balance_fuse = BalanceFuse(
            market_id=IporFusionMarkets.ERC20_VAULT_BALANCE,
            fuse=Web3.to_checksum_address("0x" + "bb" * 20),
        )
        data = self._make_data(
            fuse_markets={fuse_addr: IporFusionMarkets.MORPHO},
            balance_fuses=[balance_fuse],
        )

        health = validate(data, 100 * 10**18)

        assert len(health.criticals) == 1
        line = health.criticals[0]
        assert "CRITICAL" in line
        assert "MORPHO" in line
        assert fuse_addr in line
        assert "totalAssets" in line

    def test_flash_loan_action_fuse_not_flagged(self):

        data = self._make_data(
            fuse_markets={"0x" + "cc" * 20: IporFusionMarkets.MORPHO_FLASH_LOAN},
            balance_fuses=[],
        )

        health = validate(data)

        assert not health.criticals

    def test_no_fuse_markets_no_criticals(self):

        data = self._make_data(fuse_markets=None, balance_fuses=[])
        health = validate(data)
        assert not health.criticals

    def test_balance_fuse_market_missing_erc20_dep_marked_critical(self):
        """The Base vault case: balance fuse for MORPHO_LIQUIDITY_IN_MARKETS
        (41) exists, but its dependency graph entry doesn't include
        ERC20_VAULT_BALANCE — updateMarketsBalances(41) silently skips the
        ERC20 cache refresh. The vault tracks a non-underlying token idle
        (ERC20_VAULT_BALANCE substrate), so the dependency genuinely matters."""
        from ipor_fusion.core.plasma_vault import BalanceFuse

        morpho_lim = 41
        bf = BalanceFuse(
            market_id=morpho_lim, fuse=Web3.to_checksum_address("0x" + "bb" * 20)
        )
        non_underlying = "0x" + "22" * 20
        data = self._make_data(
            fuse_markets=None,
            balance_fuses=[bf],
            dependency_graph={},
            market_substrates={
                IporFusionMarkets.ERC20_VAULT_BALANCE: [
                    self._erc20_substrate(non_underlying)
                ]
            },
        )

        health = validate(data)

        critical_lines = [c for c in health.criticals if "ERC20_VAULT_BALANCE" in c]
        assert len(critical_lines) == 1
        assert "(41)" in critical_lines[0]

    def test_single_asset_vault_missing_erc20_dep_not_flagged(self):
        """Single-asset optimizer: ERC20_VAULT_BALANCE tracks only the
        underlying (or no such market at all). Idle underlying is already in
        totalAssets via ERC4626, so a missing dependency edge is NOT a bug and
        must not be flagged — even though the dependency graph omits it."""
        from ipor_fusion.core.plasma_vault import BalanceFuse

        bf = BalanceFuse(
            market_id=IporFusionMarkets.AAVE_V3,
            fuse=Web3.to_checksum_address("0x" + "bb" * 20),
        )
        underlying = "0x" + "11" * 20  # matches _make_data asset
        data = self._make_data(
            fuse_markets=None,
            balance_fuses=[bf],
            dependency_graph={},
            market_substrates={
                IporFusionMarkets.ERC20_VAULT_BALANCE: [
                    self._erc20_substrate(underlying)
                ]
            },
        )

        health = validate(data)

        assert not [c for c in health.criticals if "ERC20_VAULT_BALANCE" in c]

    def test_no_erc20_balance_market_missing_dep_not_flagged(self):
        """No ERC20_VAULT_BALANCE market at all (no idle-token tracking): there
        is nothing to depend on, so the check must not fire."""
        from ipor_fusion.core.plasma_vault import BalanceFuse

        bf = BalanceFuse(
            market_id=IporFusionMarkets.AAVE_V3,
            fuse=Web3.to_checksum_address("0x" + "bb" * 20),
        )
        data = self._make_data(
            fuse_markets=None,
            balance_fuses=[bf],
            dependency_graph={},
            market_substrates=None,
        )

        health = validate(data)

        assert not [c for c in health.criticals if "ERC20_VAULT_BALANCE" in c]

    def test_balance_fuse_with_correct_erc20_dep_no_critical(self):
        from ipor_fusion.core.plasma_vault import BalanceFuse

        bf = BalanceFuse(
            market_id=IporFusionMarkets.EULER_V2,
            fuse=Web3.to_checksum_address("0x" + "bb" * 20),
        )
        data = self._make_data(
            fuse_markets=None,
            balance_fuses=[bf],
            dependency_graph={
                IporFusionMarkets.EULER_V2: [IporFusionMarkets.ERC20_VAULT_BALANCE]
            },
        )

        health = validate(data)

        assert not [c for c in health.criticals if "ERC20_VAULT_BALANCE" in c]


# ── Euler V2 ──────────────────────────────────────────────────────────

# Live reads of vault 0x3e212F13…93bF ("TESS cbETH (Base) Loop Vault") at Base
# block 52995835: sub-account 0x01 owes eWETH-36 against ecbETH-5, USD unit
# of account, 97% liquidation LTV.
EULER_VAULT = Web3.to_checksum_address("0x3e212F136244465d8E524Fb658E6Daa1fb0593bF")
EULER_SUB_ACCOUNT = Web3.to_checksum_address(
    "0x3e212f136244465d8e524fb658e6daa1fb0593be"
)
EULER_COLLATERAL = Web3.to_checksum_address(
    "0x60c61a47804283a514bfcdf163138b7528720d0f"
)
EULER_BORROW = Web3.to_checksum_address("0xdc4efb20ce286b421f6361734a2a006a1f24af8d")
EULER_DEBT = 1_686_324_901_840_101_461_983
EULER_ADJUSTED_COLLATERAL = 4_740_289_721_451_937_074_302_855
EULER_LIABILITY = 4_050_377_393_812_968_788_103_822

_SELECTOR_DEBT_OF = function_signature_to_4byte_selector("debtOf(address)")
_SELECTOR_LIQUIDITY_FULL = function_signature_to_4byte_selector(
    "accountLiquidityFull(address,bool)"
)
_SELECTOR_LTV_LIQUIDATION = function_signature_to_4byte_selector(
    "LTVLiquidation(address)"
)
_SELECTOR_UNIT_OF_ACCOUNT = function_signature_to_4byte_selector("unitOfAccount()")


EULER_SUBSTRATES = [
    euler_substrate(
        euler_vault=EULER_COLLATERAL,
        is_collateral=True,
        can_borrow=False,
        sub_account=1,
    ),
    euler_substrate(
        euler_vault=EULER_BORROW, is_collateral=False, can_borrow=True, sub_account=1
    ),
]
EULER_ACCOUNT = _EulerV2Account(EULER_SUB_ACCOUNT, (EULER_BORROW,))


def _euler_ctx(
    *,
    debt: int = EULER_DEBT,
    unit: str = EULER_USD_UNIT_OF_ACCOUNT,
    liquidity_reverts: bool = False,
) -> MagicMock:
    """Answer the TESS sub-account's reads; only the borrow vault is its controller."""

    def handler(to: str, data: bytes) -> bytes:
        selector = data[:4]
        assert to == EULER_BORROW
        if selector == _SELECTOR_DEBT_OF:
            (account,) = decode(["address"], data[4:])
            assert Web3.to_checksum_address(account) == EULER_SUB_ACCOUNT
            return encode(["uint256"], [debt])
        if selector == _SELECTOR_LIQUIDITY_FULL:
            if liquidity_reverts:
                raise ContractLogicError("E_NoLiability")
            return encode(
                ["address[]", "uint256[]", "uint256"],
                [[EULER_COLLATERAL], [EULER_ADJUSTED_COLLATERAL], EULER_LIABILITY],
            )
        if selector == _SELECTOR_UNIT_OF_ACCOUNT:
            return encode(["address"], [unit])
        assert selector == _SELECTOR_LTV_LIQUIDATION
        return encode(["uint16"], [9700])

    ctx = MagicMock()
    ctx.call.side_effect = multicall_aware(handler)
    return ctx


class TestEulerV2Substrate:
    def test_decodes_what_the_fuse_encoder_packs(self):
        substrate = EulerV2Substrate.from_bytes(EULER_SUBSTRATES[1])

        assert substrate == EulerV2Substrate(
            euler_vault=EULER_BORROW,
            is_collateral=False,
            can_borrow=True,
            sub_account_id=1,
        )
        assert substrate.sub_account(EULER_VAULT) == EULER_SUB_ACCOUNT


class TestEulerV2Health:
    def test_borrowing_sub_account_matches_the_live_position(self):
        result = _compute_euler_v2_account_health(
            _euler_ctx(), EULER_ACCOUNT, MarketId(11), "EULER_V2"
        )

        assert isinstance(result, LendingMarketHealth)
        assert result.protocol == "euler_v2"
        assert result.substrate_id == EULER_SUB_ACCOUNT
        assert result.health_factor == 1.1703
        assert result.current_ltv == 0.828824
        assert result.max_ltv == 0.97
        assert result.ltv_usage_percent == 85.45
        assert result.total_collateral_usd == 4_886_896.62
        assert result.total_debt_usd == 4_050_377.39

    def test_no_debt_is_no_position(self):
        result = _compute_euler_v2_account_health(
            _euler_ctx(debt=0),
            EULER_ACCOUNT,
            MarketId(11),
            "EULER_V2",
        )

        assert result is None

    def test_values_outside_usd_are_not_reported_as_usd(self):
        result = _compute_euler_v2_account_health(
            _euler_ctx(unit=TOKEN_A),
            EULER_ACCOUNT,
            MarketId(11),
            "EULER_V2",
        )

        assert isinstance(result, LendingMarketHealth)
        assert result.health_factor == 1.1703
        assert result.total_collateral_usd is None
        assert result.total_debt_usd is None

    def test_failed_debt_read_is_unreadable(self):
        ctx = MagicMock()
        ctx.call.side_effect = Web3RPCError("rpc down")

        result = _compute_euler_v2_account_health(
            ctx, EULER_ACCOUNT, MarketId(11), "EULER_V2"
        )

        assert isinstance(result, UnreadableLendingMarket)
        assert result.reason == "debt read failed: Web3RPCError"

    def test_failed_liquidity_read_is_unreadable(self):
        result = _compute_euler_v2_account_health(
            _euler_ctx(liquidity_reverts=True),
            EULER_ACCOUNT,
            MarketId(11),
            "EULER_V2",
        )

        assert isinstance(result, UnreadableLendingMarket)
        assert result.substrate_id == EULER_SUB_ACCOUNT
        assert "account liquidity" in result.reason

    def test_vault_reads_one_row_per_borrowing_sub_account(self):
        """Only substrates that may borrow name a sub-account to read; the
        collateral vault is never asked for liquidity."""
        assert IporFusionMarkets.EULER_V2 in EULER_V2_MARKET_IDS

        result = fetch_vault_lending_health(
            _euler_ctx(),
            EULER_VAULT,
            balance_fuse_market_ids=[IporFusionMarkets.EULER_V2],
            market_substrates={IporFusionMarkets.EULER_V2: EULER_SUBSTRATES},
        )

        assert [m.substrate_id for m in result.markets] == [EULER_SUB_ACCOUNT]
        assert result.is_complete
        assert result.worst_ltv_usage == 85.45

    def test_supply_only_euler_market_has_no_position(self):
        ctx = MagicMock()

        result = fetch_vault_lending_health(
            ctx,
            EULER_VAULT,
            balance_fuse_market_ids=[IporFusionMarkets.EULER_V2],
            market_substrates={IporFusionMarkets.EULER_V2: EULER_SUBSTRATES[:1]},
        )

        assert not result.has_lending_positions
        ctx.call.assert_not_called()


# ── Borrow markets without a reader ───────────────────────────────────


def _dolomite_substrate(asset: str, *, can_borrow: bool) -> bytes:
    """asset << 96 | subAccountId << 88 | canBorrow << 80."""
    return (int(asset, 16) << 96 | 1 << 88 | int(can_borrow) << 80).to_bytes(32, "big")


class TestUnsupportedBorrowMarkets:
    @pytest.mark.parametrize(
        "market_id",
        [
            IporFusionMarkets.MOONWELL,
            IporFusionMarkets.SILO_V2,
            IporFusionMarkets.EBISU,
        ],
    )
    def test_borrow_capable_market_is_unreadable_not_absent(self, market_id):
        result = fetch_vault_lending_health(
            MagicMock(),
            VAULT_ADDR,
            balance_fuse_market_ids=[market_id],
            market_substrates={},
        )

        assert not result.markets
        [unreadable] = result.unreadable
        assert unreadable.market_id == market_id
        assert not result.is_complete

    def test_dolomite_counts_only_when_a_substrate_may_borrow(self):
        mid = IporFusionMarkets.DOLOMITE

        def health(can_borrow: bool) -> VaultLendingHealth:
            return fetch_vault_lending_health(
                MagicMock(),
                VAULT_ADDR,
                balance_fuse_market_ids=[mid],
                market_substrates={
                    mid: [_dolomite_substrate(TOKEN_A, can_borrow=can_borrow)]
                },
            )

        assert not health(can_borrow=False).has_lending_positions
        assert [u.market_id for u in health(can_borrow=True).unreadable] == [mid]

    def test_supply_only_market_is_not_flagged(self):
        result = fetch_vault_lending_health(
            MagicMock(),
            VAULT_ADDR,
            balance_fuse_market_ids=[IporFusionMarkets.LIQUITY_V2],
            market_substrates={},
        )

        assert not result.has_lending_positions


# ── Compound V3 ─────────────────────────────────────────────────────────

COMET = Web3.to_checksum_address("0x207158a267CBD2598BB3d611D8CBdEE2709F2F8C")
COMET_WETH = Web3.to_checksum_address("0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2")
COMET_WBTC = Web3.to_checksum_address("0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599")
COMET_WETH_FEED = Web3.to_checksum_address("0x" + "1" * 40)
COMET_WBTC_FEED = Web3.to_checksum_address("0x" + "2" * 40)
COMET_BASE_FEED = Web3.to_checksum_address("0x" + "3" * 40)


def _comet_ctx(
    *,
    debt: int = 20_000 * 10**6,
    weth: int = 10 * 10**18,
    wbtc: int = 0,
    broken_feed: str | None = None,
    base_scale: int = 10**6,
) -> MagicMock:
    """A USDC Comet listing WETH at $3,000 (liquidation factor 0.9) and WBTC at
    $100,000 (0.85), with USDC at $1."""
    prices = {
        COMET_WETH_FEED: 3_000 * 10**8,
        COMET_WBTC_FEED: 100_000 * 10**8,
        COMET_BASE_FEED: 10**8,
    }
    listings = [
        (COMET_WETH, COMET_WETH_FEED, 10**18, 9 * 10**17, weth),
        (COMET_WBTC, COMET_WBTC_FEED, 10**8, 85 * 10**16, wbtc),
    ]

    def handler(to: str, data: bytes) -> bytes:
        assert to == COMET
        selector = data[:4]
        if selector == function_signature_to_4byte_selector("numAssets()"):
            return encode(["uint8"], [len(listings)])
        if selector == function_signature_to_4byte_selector("baseScale()"):
            return encode(["uint256"], [base_scale])
        if selector == function_signature_to_4byte_selector("baseTokenPriceFeed()"):
            return encode(["address"], [COMET_BASE_FEED])
        if selector == function_signature_to_4byte_selector("borrowBalanceOf(address)"):
            return encode(["uint256"], [debt])
        if selector == function_signature_to_4byte_selector("getPrice(address)"):
            (raw_feed,) = decode(["address"], data[4:])
            feed = Web3.to_checksum_address(raw_feed)
            if feed == broken_feed:
                raise ContractLogicError("BadPrice")
            return encode(["uint256"], [prices[feed]])
        if selector == function_signature_to_4byte_selector("getAssetInfo(uint8)"):
            (index,) = decode(["uint8"], data[4:])
            asset, feed, scale, factor, _ = listings[index]
            return encode(
                ["(uint8,address,address,uint64,uint64,uint64,uint64,uint128)"],
                [(index, asset, feed, scale, 8 * 10**17, factor, 10**18, 10**30)],
            )
        assert selector == function_signature_to_4byte_selector(
            "collateralBalanceOf(address,address)"
        )
        (_, asset) = decode(["address", "address"], data[4:])
        checksum = Web3.to_checksum_address(asset)
        return encode(["uint128"], [next(x[4] for x in listings if x[0] == checksum)])

    ctx = MagicMock()
    ctx.call.side_effect = multicall_aware(handler)
    return ctx


def _compound_health(
    ctx: MagicMock,
    market_id: MarketId = IporFusionMarkets.COMPOUND_V3_INSTITUTIONAL_USDC,
):
    return _compute_compound_v3_market_health(
        ctx, COMET, VAULT_ADDR, market_id, "COMPOUND_V3_INSTITUTIONAL_USDC"
    )


class TestCompoundV3Health:
    def test_markets_cover_every_comet_market(self):
        assert COMPOUND_V3_MARKET_IDS == {
            IporFusionMarkets.COMPOUND_V3_USDC,
            IporFusionMarkets.COMPOUND_V3_USDT,
            IporFusionMarkets.COMPOUND_V3_WETH,
            IporFusionMarkets.COMPOUND_V3_INSTITUTIONAL_USDC,
        }

    def test_borrowing_account_is_measured_against_the_liquidation_limit(self):
        result = _compound_health(_comet_ctx())

        assert isinstance(result, LendingMarketHealth)
        assert result.protocol == "compound_v3"
        assert result.substrate_id is None
        # $30,000 of WETH at 0.9 is a $27,000 limit against $20,000 of debt.
        assert result.total_collateral_usd == 30_000.0
        assert result.total_debt_usd == 20_000.0
        assert result.current_ltv == 0.666667
        assert result.max_ltv == 0.9
        assert result.health_factor == 1.35
        assert result.ltv_usage_percent == 74.07

    def test_every_listed_collateral_counts_at_its_own_factor(self):
        result = _compound_health(_comet_ctx(wbtc=10**8))

        assert isinstance(result, LendingMarketHealth)
        # Adds $100,000 of WBTC at 0.85: a $112,000 limit on $130,000.
        assert result.total_collateral_usd == 130_000.0
        assert result.max_ltv == 0.861538
        assert result.health_factor == 5.6

    def test_health_factor_reaches_one_at_the_liquidation_limit(self):
        result = _compound_health(_comet_ctx(debt=27_000 * 10**6))

        assert isinstance(result, LendingMarketHealth)
        assert result.health_factor == 1.0
        assert result.ltv_usage_percent == 100.0
        assert result.is_critical

    def test_no_debt_is_no_position(self):
        assert _compound_health(_comet_ctx(debt=0)) is None

    def test_debt_without_collateral_is_critical_not_unknown(self):
        result = _compound_health(_comet_ctx(weth=0))

        assert isinstance(result, LendingMarketHealth)
        assert result.health_factor == 0.0
        assert result.current_ltv is None
        assert result.max_ltv == 0.0
        assert result.ltv_usage_percent is None
        assert result.is_critical

    def test_dust_debt_that_prices_to_nothing_is_no_position(self):
        assert _compound_health(_comet_ctx(debt=1, base_scale=10**18)) is None

    def test_broken_feed_of_a_held_asset_is_unreadable(self):
        result = _compound_health(_comet_ctx(broken_feed=COMET_WETH_FEED))

        assert isinstance(result, UnreadableLendingMarket)
        assert result.market_id == MarketId(57)

    def test_broken_feed_of_an_unheld_asset_does_not_hide_a_critical_position(self):
        result = _compound_health(
            _comet_ctx(debt=30_000 * 10**6, broken_feed=COMET_WBTC_FEED)
        )

        assert isinstance(result, LendingMarketHealth)
        assert result.health_factor == 0.9
        assert result.is_critical

    def test_weth_comet_values_are_not_reported_as_usd(self):
        result = _compound_health(_comet_ctx(), IporFusionMarkets.COMPOUND_V3_WETH)

        assert isinstance(result, LendingMarketHealth)
        assert result.total_collateral_usd is None
        assert result.total_debt_usd is None
        assert result.health_factor == 1.35

    def test_vault_health_reads_the_market_comet(self):
        result = fetch_vault_lending_health(
            _comet_ctx(),
            VAULT_ADDR,
            balance_fuse_market_ids=[MarketId(57)],
            market_substrates={},
            comets={MarketId(57): COMET},
        )

        assert [m.health_factor for m in result.markets] == [1.35]
        assert result.is_complete

    def test_supply_only_vault_has_no_row_and_no_warning(self):
        result = fetch_vault_lending_health(
            _comet_ctx(debt=0),
            VAULT_ADDR,
            balance_fuse_market_ids=[MarketId(2)],
            market_substrates={},
            comets={MarketId(2): COMET},
        )

        assert not result.markets
        assert result.is_complete

    def test_market_whose_fuse_names_no_comet_is_unreadable(self):
        result = fetch_vault_lending_health(
            MagicMock(),
            VAULT_ADDR,
            balance_fuse_market_ids=[MarketId(13)],
            market_substrates={},
        )

        [unreadable] = result.unreadable
        assert unreadable.market_id == MarketId(13)
        assert unreadable.reason == "the balance fuse names no Comet"

    def test_row_is_rendered_under_its_market(self):
        row = _compound_health(_comet_ctx())
        assert isinstance(row, LendingMarketHealth)

        _, account_level = _index_lending_health(VaultLendingHealth(markets=[row]))

        assert account_level == {row.market_id: row}


# ── Lido Lend ───────────────────────────────────────────────────────────

LIDO_CORE = Web3.to_checksum_address("0x78d3262E82A35d290507bCF9B4834735DdA6b854")
LIDO_FUSE = Web3.to_checksum_address("0x" + "f5" * 20)
LIDO_MID = MarketId(58)
LIDO_WORD = "0x" + "ab" * 32
LIDO_WORD_B = "0x" + "cd" * 32
LIDO_WORD_BYTES = bytes.fromhex("ab" * 32)
LIDO_WORD_B_BYTES = bytes.fromhex("cd" * 32)
# type byte 0x01, zeros, token address: a collateral word, not a market.
LIDO_COLLATERAL_WORD = (1 << 248 | int(TOKEN_A, 16)).to_bytes(32, "big")
_MORPHO_MARKET = ["uint128"] * 6
_PARAMS = ["address", "address", "address", "address", "uint256"]


def _sel(signature: str) -> bytes:
    return function_signature_to_4byte_selector(signature)


def _lido_core_ctx(
    *,
    borrow_shares: int = 500 * 10**6,
    collateral: int = 1000,
    price: int | Exception = ORACLE_PRICE_SCALE,
    broken: Exception | None = None,
    broken_words: tuple[bytes, ...] = (),
) -> MagicMock:
    """A Lido Lend core answering the Morpho reads (50% LTV by default); the
    controller lookups are never made by the health read."""

    def handler(to: str, data: bytes) -> bytes:
        selector = data[:4]
        if selector == _sel("price()"):
            assert to == ORACLE_ADDR
            if isinstance(price, Exception):
                raise price
            return encode(["uint256"], [price])
        assert to == LIDO_CORE
        if broken is not None:
            raise broken
        if data[4:36] in broken_words:
            raise ContractLogicError("execution reverted")
        if selector == _sel("position(bytes32,address)"):
            return encode(
                ["uint256", "uint128", "uint128"], [0, borrow_shares, collateral]
            )
        if selector == _sel("market(bytes32)"):
            return encode(
                _MORPHO_MARKET, [2000, 2000 * 10**6, 1000, 1000 * 10**6, 1700000000, 0]
            )
        assert selector == _sel("idToMarketParams(bytes32)")
        return encode(_PARAMS, [TOKEN_A, TOKEN_B, ORACLE_ADDR, IRM_ADDR, 86 * 10**16])

    ctx = MagicMock()
    ctx.chain_id = ChainId(560048)
    ctx.call.side_effect = multicall_aware(handler)
    return ctx


def _lido_health(ctx: MagicMock, **overrides):
    return _compute_morpho_market_health(
        ctx,
        LidoLendReader(LIDO_CORE),
        MorphoBlueMarketId(LIDO_WORD),
        VAULT_ADDR,
        LIDO_MID,
        "LIDO_LEND",
        protocol="lido_lend",
        **overrides,
    )


class TestLidoLendCollection:
    def test_market_ids_cover_the_lending_market_only(self):
        assert LIDO_LEND_MARKET_IDS == {IporFusionMarkets.LIDO_LEND}
        assert IporFusionMarkets.LIDO_LEND_FLASH_LOAN not in LIDO_LEND_MARKET_IDS

    def test_one_row_per_market_word_and_collateral_words_are_skipped(self):
        markets = _collect_lending_markets(
            VAULT_ADDR,
            [LIDO_MID],
            {LIDO_MID: [LIDO_WORD_BYTES, LIDO_COLLATERAL_WORD, LIDO_WORD_B_BYTES]},
            {},
            {},
            {LIDO_MID: LIDO_CORE},
        )

        assert markets.lido_lend == [
            (LIDO_MID, "LIDO_LEND", LIDO_CORE, MorphoBlueMarketId(LIDO_WORD)),
            (LIDO_MID, "LIDO_LEND", LIDO_CORE, MorphoBlueMarketId(LIDO_WORD_B)),
        ]
        assert markets.unreadable == []

    def test_without_a_core_the_market_is_unreadable(self):
        markets = _collect_lending_markets(
            VAULT_ADDR, [LIDO_MID], {LIDO_MID: [LIDO_WORD_BYTES]}, {}, {}, {}
        )

        assert markets.lido_lend == []
        [unreadable] = markets.unreadable
        assert unreadable == UnreadableLendingMarket(
            protocol="lido_lend",
            market_id=LIDO_MID,
            market_name="LIDO_LEND",
            reason="no Lido Lend core known for this market (the balance fuse names none or none was passed)",
        )

    def test_unreadable_market_without_core_through_fetch(self):
        result = fetch_vault_lending_health(
            MagicMock(),
            VAULT_ADDR,
            balance_fuse_market_ids=[LIDO_MID],
            market_substrates={LIDO_MID: [LIDO_WORD_BYTES]},
        )
        assert [u.protocol for u in result.unreadable] == ["lido_lend"]
        assert not result.is_complete


class TestLidoLendHealth:
    def test_health_uses_the_morpho_math_on_the_core(self):
        result = fetch_vault_lending_health(
            _lido_core_ctx(),
            VAULT_ADDR,
            balance_fuse_market_ids=[LIDO_MID],
            market_substrates={LIDO_MID: [LIDO_WORD_BYTES, LIDO_COLLATERAL_WORD]},
            lido_lend_cores={LIDO_MID: LIDO_CORE},
        )

        [row] = result.markets
        assert row.protocol == "lido_lend"
        assert row.market_id == LIDO_MID
        assert row.substrate_id == LIDO_WORD
        # 500 borrowed against 1000 collateral at 1:1, lltv 0.86.
        assert row.current_ltv == 0.5
        assert row.max_ltv == 0.86
        assert row.health_factor == 1.72
        assert row.ltv_usage_percent == 58.14
        assert result.is_complete

    def test_every_word_is_read_from_the_core(self):
        ctx = _lido_core_ctx()
        result = fetch_vault_lending_health(
            ctx,
            VAULT_ADDR,
            balance_fuse_market_ids=[LIDO_MID],
            market_substrates={LIDO_MID: [LIDO_WORD_BYTES, LIDO_WORD_B_BYTES]},
            lido_lend_cores={LIDO_MID: LIDO_CORE},
        )
        assert {m.substrate_id for m in result.markets} == {LIDO_WORD, LIDO_WORD_B}

    def test_no_debt_is_labelled(self):
        result = _lido_health(_lido_core_ctx(borrow_shares=0))
        assert isinstance(result, LendingMarketHealth)
        assert result.protocol == "lido_lend"
        assert result.current_ltv == 0.0
        assert result.substrate_id == LIDO_WORD

    def test_debt_without_collateral_is_labelled(self):
        result = _lido_health(_lido_core_ctx(collateral=0))
        assert isinstance(result, LendingMarketHealth)
        assert result.protocol == "lido_lend"
        assert result.current_ltv is None
        assert result.health_factor is None

    def test_market_data_failure_is_unreadable_and_labelled(self):
        result = _lido_health(_lido_core_ctx(broken=ContractLogicError("revert")))
        assert isinstance(result, UnreadableLendingMarket)
        assert result.protocol == "lido_lend"
        assert result.substrate_id == LIDO_WORD
        assert result.reason.startswith("market data read failed")

    def test_oracle_failure_is_unreadable_and_labelled(self):
        result = _lido_health(_lido_core_ctx(price=ContractLogicError("revert")))
        assert isinstance(result, UnreadableLendingMarket)
        assert result.protocol == "lido_lend"
        assert result.substrate_id == LIDO_WORD
        assert result.reason.startswith("oracle price read failed")

    def test_morpho_label_is_the_default(self):
        result = _compute_morpho_market_health(
            _lido_core_ctx(borrow_shares=0),
            LidoLendReader(LIDO_CORE),
            MorphoBlueMarketId(LIDO_WORD),
            VAULT_ADDR,
            LIDO_MID,
            "LIDO_LEND",
        )
        assert result.protocol == "morpho"


def _lido_vault_ctx(*, fuse_reverts: bool = False) -> MagicMock:
    """A vault with a Lido Lend market whose balance fuse binds the core."""

    def handler(to: str, data: bytes) -> bytes:
        selector = data[:4]
        if selector == _sel("LIDO_LEND()"):
            assert to == LIDO_FUSE
            if fuse_reverts:
                raise ContractLogicError("execution reverted")
            return encode(["address"], [LIDO_CORE])
        if selector == _sel("getDependencyBalanceGraph(uint256)"):
            return encode(["uint256[]"], [[]])
        if selector == _sel("totalAssetsInMarket(uint256)"):
            return encode(["uint256"], [0])
        assert selector == _sel("getMarketSubstrates(uint256)")
        return encode(["bytes32[]"], [[LIDO_WORD_BYTES]])

    ctx = MagicMock()
    ctx.chain_id = ChainId(560048)
    ctx.call.side_effect = multicall_aware(handler)
    return ctx


class TestLidoLendDiscovery:
    @staticmethod
    def _reads(ctx: MagicMock, *, lending: bool = True):
        fuses = [BalanceFuse(LIDO_MID, LIDO_FUSE)]
        with patch.object(PlasmaVault, "read_balance_fuses", return_value=fuses):
            return vault_data._fetch_market_reads(
                ctx, PlasmaVault(VAULT_ADDR), with_lending_venues=lending
            )

    def test_the_core_comes_from_the_balance_fuse(self):
        markets = self._reads(_lido_vault_ctx())
        assert markets.lido_lend_cores == {LIDO_MID: LIDO_CORE}

    def test_a_fuse_that_names_no_core_is_left_out(self):
        markets = self._reads(_lido_vault_ctx(fuse_reverts=True))
        assert markets.lido_lend_cores == {}

    def test_nothing_is_read_without_lending(self):
        ctx = _lido_vault_ctx()
        markets = self._reads(ctx, lending=False)
        assert markets.lido_lend_cores == {}
        assert not any(
            c.args[1][:4] == _sel("LIDO_LEND()") for c in ctx.call.call_args_list
        )

    def test_no_lido_fuse_means_no_read(self):
        ctx = MagicMock()
        assert _fetch_lido_lend_cores(ctx, [BalanceFuse(MarketId(14), LIDO_FUSE)]) == {}
        ctx.call.assert_not_called()


class TestLidoLendPositions:
    def test_positions_per_market_word(self):
        unread: list[str] = []
        positions = _fetch_lido_lend_positions(
            _lido_core_ctx(),
            VAULT_ADDR,
            {LIDO_MID: [LIDO_WORD_BYTES, LIDO_COLLATERAL_WORD, LIDO_WORD_B_BYTES]},
            {LIDO_MID: LIDO_CORE},
            unread,
        )

        assert positions is not None
        assert [pb.market_id for pb in positions[LIDO_MID]] == [LIDO_WORD, LIDO_WORD_B]
        assert positions[LIDO_MID][0].collateral == 1000
        assert unread == []

    def test_an_unread_word_is_named(self):
        unread: list[str] = []
        positions = _fetch_lido_lend_positions(
            _lido_core_ctx(broken_words=(LIDO_WORD_B_BYTES,)),
            VAULT_ADDR,
            {LIDO_MID: [LIDO_WORD_BYTES, LIDO_WORD_B_BYTES]},
            {LIDO_MID: LIDO_CORE},
            unread,
        )

        assert positions is not None
        assert [pb.market_id for pb in positions[LIDO_MID]] == [LIDO_WORD]
        assert unread == [f"Lido Lend position in market {LIDO_WORD_B}"]

    def test_a_suppressed_batch_failure_leaves_every_word_unread(self):
        unread: list[str] = []
        positions = _fetch_lido_lend_positions(
            _lido_core_ctx(broken=Web3RPCError("rate limited")),
            VAULT_ADDR,
            {LIDO_MID: [LIDO_WORD_BYTES, LIDO_WORD_B_BYTES]},
            {LIDO_MID: LIDO_CORE},
            unread,
        )

        assert positions is None
        assert unread == [
            f"Lido Lend position in market {LIDO_WORD}",
            f"Lido Lend position in market {LIDO_WORD_B}",
        ]

    def test_market_words_without_a_core_are_named_unread(self):
        ctx = MagicMock()
        unread: list[str] = []
        assert (
            _fetch_lido_lend_positions(
                ctx,
                VAULT_ADDR,
                {LIDO_MID: [LIDO_WORD_BYTES, LIDO_COLLATERAL_WORD, LIDO_WORD_B_BYTES]},
                {},
                unread,
            )
            is None
        )
        assert unread == [
            f"Lido Lend position in market {LIDO_WORD}",
            f"Lido Lend position in market {LIDO_WORD_B}",
        ]
        ctx.call.assert_not_called()

    def test_markets_without_words_or_core_are_skipped(self):
        ctx = MagicMock()
        unread: list[str] = []
        assert (
            _fetch_lido_lend_positions(
                ctx,
                VAULT_ADDR,
                {LIDO_MID: [LIDO_COLLATERAL_WORD]},
                {LIDO_MID: LIDO_CORE},
                unread,
            )
            is None
        )
        assert _fetch_lido_lend_positions(ctx, VAULT_ADDR, {}, {}, unread) is None
        ctx.call.assert_not_called()


def _markets_with_lido(**overrides) -> vault_data._MarketReads:
    overrides.setdefault("lido_lend_cores", {LIDO_MID: LIDO_CORE})
    return vault_data._MarketReads(
        balance_fuses=[BalanceFuse(LIDO_MID, LIDO_FUSE)],
        dependency_graph={},
        market_substrates={LIDO_MID: [LIDO_WORD_BYTES]},
        aave_pools={},
        comets={},
        market_balances={},
        **overrides,
    )


class TestLidoLendVaultDataWiring:
    def test_lending_reads_pass_the_cores_and_collect_the_positions(self):
        with (
            patch.object(
                vault_data,
                "fetch_vault_lending_health",
                return_value=VaultLendingHealth(markets=[]),
            ) as health,
            ThreadPoolExecutor() as pool,
        ):
            reads = vault_data._fetch_lending_reads(
                _lido_core_ctx(),
                pool,
                VAULT_ADDR,
                _markets_with_lido(),
            )

        assert health.call_args.kwargs["lido_lend_cores"] == {LIDO_MID: LIDO_CORE}
        assert reads.lido_lend_positions is not None
        assert reads.lido_lend_positions[LIDO_MID][0].market_id == LIDO_WORD
        assert reads.lido_lend_unread == []

    def test_unread_words_are_kept(self):
        with (
            patch.object(
                vault_data,
                "fetch_vault_lending_health",
                return_value=VaultLendingHealth(markets=[]),
            ),
            ThreadPoolExecutor() as pool,
        ):
            reads = vault_data._fetch_lending_reads(
                _lido_core_ctx(broken_words=(LIDO_WORD_BYTES,)),
                pool,
                VAULT_ADDR,
                _markets_with_lido(),
            )

        assert reads.lido_lend_positions is None
        assert reads.lido_lend_unread == [f"Lido Lend position in market {LIDO_WORD}"]

    @staticmethod
    def _derive(lending):
        reads = vault_data._VaultReads(
            share_decimals=18,
            total_assets=100 * WAD,
            total_supply=100 * WAD,
            supply_cap=100 * WAD,
            asset=TOKEN_B,
            access_manager=Web3.to_checksum_address("0x" + "ac" * 20),
            price_oracle=ORACLE_ADDR,
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
        check = UnpriceableCheck(tokens=[], unread=[])
        with (
            patch.object(vault_data, "_read_erc20_market", return_value=None),
            patch.object(
                vault_data, "fetch_unpriceable_priced_tokens", return_value=check
            ) as unpriceable,
            patch.object(vault_data, "_fetch_breakdown_token_prices", return_value={}),
            ThreadPoolExecutor() as pool,
        ):
            derived = vault_data._derived_reads(
                MagicMock(),
                pool,
                VAULT_ADDR,
                reads,
                MagicMock(),
                _markets_with_lido(),
                lending,
                metadata=False,
            )
        return derived, unpriceable

    def test_a_market_without_a_core_reaches_failed_reads(self):
        with (
            patch.object(
                vault_data,
                "fetch_vault_lending_health",
                return_value=VaultLendingHealth(markets=[]),
            ),
            ThreadPoolExecutor() as pool,
        ):
            lending = vault_data._fetch_lending_reads(
                _lido_core_ctx(),
                pool,
                VAULT_ADDR,
                _markets_with_lido(lido_lend_cores={}),
            )

        assert lending.lido_lend_positions is None
        assert lending.lido_lend_unread == [f"Lido Lend position in market {LIDO_WORD}"]
        derived, _ = self._derive(lending)
        assert derived.failed == [
            f"unpriceable_priced_tokens: Lido Lend position in market {LIDO_WORD}"
        ]

    def test_positions_reach_the_price_check_and_unread_words_fail_the_read(self):
        positions = {LIDO_MID: []}
        lending = vault_data._LendingReads(
            lending_health=VaultLendingHealth(markets=[]),
            lido_lend_positions=positions,
            lido_lend_unread=[f"Lido Lend position in market {LIDO_WORD_B}"],
        )
        derived, unpriceable = self._derive(lending)

        assert unpriceable.call_args.kwargs["lido_lend_positions"] is positions
        assert derived.failed == [
            f"unpriceable_priced_tokens: Lido Lend position in market {LIDO_WORD_B}"
        ]


def _lido_row(**overrides) -> LendingMarketHealth:
    values = {
        "protocol": "lido_lend",
        "market_id": LIDO_MID,
        "market_name": "LIDO_LEND",
        "current_ltv": 0.5,
        "max_ltv": 0.86,
        "health_factor": 1.72,
        "total_collateral_usd": None,
        "total_debt_usd": None,
        "ltv_usage_percent": 58.14,
        "substrate_id": LIDO_WORD,
    }
    values.update(overrides)
    return LendingMarketHealth(**values)


def _text_data(lh: VaultLendingHealth) -> MagicMock:
    data = MagicMock()
    data.lending_health = lh
    data.morpho_positions = None
    data.aave_positions = None
    return data


class TestLidoLendRendering:
    def test_row_is_indexed_under_its_market_word(self):
        row = _lido_row()
        morpho, account_level = _index_lending_health(VaultLendingHealth(markets=[row]))
        assert morpho == {}
        assert account_level == {LIDO_WORD: row}

    def test_row_without_a_word_is_not_indexed(self):
        row = _lido_row(substrate_id=None)
        assert _index_lending_health(VaultLendingHealth(markets=[row]))[1] == {}

    def test_text_output_shows_the_lido_row(self, capsys):
        _print_lending_health(_text_data(VaultLendingHealth(markets=[_lido_row()])))

        out = capsys.readouterr().out
        assert f"lido lend market {LIDO_WORD}:" in out
        assert "LIDO_LEND (58):" in out
        assert "Health Factor:" in out
        assert "1.7200" in out

    def test_text_output_shows_an_unreadable_market(self, capsys):
        unreadable = UnreadableLendingMarket(
            protocol="lido_lend",
            market_id=LIDO_MID,
            market_name="LIDO_LEND",
            reason="no Lido Lend core known for this market (the balance fuse names none or none was passed)",
        )
        _print_lending_health(
            _text_data(VaultLendingHealth(markets=[], unreadable=[unreadable]))
        )

        out = capsys.readouterr().out
        assert "lido_lend health unknown" in out
        assert (
            "no Lido Lend core known for this market (the balance fuse names none or none was passed)"
            in out
        )

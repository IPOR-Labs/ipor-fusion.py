# pylint: disable=unused-argument,import-outside-toplevel
"""Unit tests for lending health computation — mock on-chain calls."""

from unittest.mock import MagicMock, patch

from eth_abi import encode
from web3 import Web3

from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.readers.lending_health import (
    AAVE_V3_MARKET_IDS,
    MORPHO_BLUE_ADDRESS,
    MORPHO_MARKET_IDS,
    ORACLE_PRICE_SCALE,
    LendingMarketHealth,
    VaultLendingHealth,
    _compute_aave_market_health,
    _compute_morpho_market_health,
    _shares_to_assets_up,
    fetch_vault_lending_health,
)
from ipor_fusion.readers.aave_v3 import AaveV3Reader
from ipor_fusion.readers.morpho import MorphoReader
from ipor_fusion.types import MorphoBlueMarketId

VAULT_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
ORACLE_ADDR = Web3.to_checksum_address("0xdDdDddDdDdddDDddDDddDDDDdDdDDdDDdDDDDDDd")
TOKEN_A = Web3.to_checksum_address("0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB")
TOKEN_B = Web3.to_checksum_address("0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC")
IRM_ADDR = Web3.to_checksum_address("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE")
MORPHO_MARKET_ID = MorphoBlueMarketId("a" * 64)


def _make_morpho_reader():
    ctx = MagicMock()
    return MorphoReader(ctx, MORPHO_BLUE_ADDRESS), ctx


def _make_aave_reader():
    ctx = MagicMock()
    pool = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")
    return AaveV3Reader(ctx, pool), ctx


# ── shares_to_assets_up ──────────────────────────────────────────────


class TestSharesToAssetsUp:
    def test_zero_shares(self):
        assert _shares_to_assets_up(0, 1000, 1000) == 0

    def test_zero_total_shares(self):
        assert _shares_to_assets_up(100, 1000, 0) == 100

    def test_rounds_up(self):
        # 100 * (1001) / (1001) = 100, but with non-round: 3 * 1001 / 4 = 750.75 → 751
        assert _shares_to_assets_up(3, 1000, 3) == 751

    def test_exact_division(self):
        assert _shares_to_assets_up(500, 1000, 1000) == 500


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
        ctx.call.side_effect = [
            # position(market_id, user) → supply_shares=1000, borrow_shares=0, collateral=500
            encode(["uint256", "uint128", "uint128"], [1000, 0, 500]),
            # market_params(market_id) → lltv=0.86e18
            encode(
                ["address", "address", "address", "address", "uint256"],
                [TOKEN_A, TOKEN_B, ORACLE_ADDR, IRM_ADDR, 860000000000000000],
            ),
        ]

        result = _compute_morpho_market_health(
            ctx, reader, MORPHO_MARKET_ID, VAULT_ADDR, 14, "MORPHO"
        )

        assert result is not None
        assert result.protocol == "morpho"
        assert result.current_ltv == 0.0
        assert result.max_ltv == 0.86
        assert result.ltv_usage_percent == 0.0
        assert not result.is_warning

    def test_active_borrow_computes_ltv(self):
        reader, ctx = _make_morpho_reader()

        # 50% LTV scenario:
        # borrow_shares=500, total_borrow_assets=1000, total_borrow_shares=1000
        # → borrowed ≈ 500
        # collateral=1000, oracle_price=1e36 (1:1 price)
        # → collateral_value = 1000
        # → current_ltv = 500/1000 = 0.5
        # lltv = 0.86 → usage = 0.5/0.86 ≈ 58.14%

        ctx.call.side_effect = [
            # position
            encode(["uint256", "uint128", "uint128"], [0, 500, 1000]),
            # market
            encode(
                ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
                [2000, 2000, 1000, 1000, 1700000000, 0],
            ),
            # market_params
            encode(
                ["address", "address", "address", "address", "uint256"],
                [TOKEN_A, TOKEN_B, ORACLE_ADDR, IRM_ADDR, 860000000000000000],
            ),
            # oracle price() → 1e36 (1:1)
            encode(["uint256"], [ORACLE_PRICE_SCALE]),
        ]

        result = _compute_morpho_market_health(
            ctx, reader, MORPHO_MARKET_ID, VAULT_ADDR, 14, "MORPHO"
        )

        assert result is not None
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
        ctx.call.side_effect = [
            encode(["uint256", "uint128", "uint128"], [0, 830, 1000]),
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

        result = _compute_morpho_market_health(
            ctx, reader, MORPHO_MARKET_ID, VAULT_ADDR, 14, "MORPHO"
        )

        assert result is not None
        assert result.is_critical
        assert result.is_warning

    def test_zero_collateral_returns_none_ltv(self):
        reader, ctx = _make_morpho_reader()

        ctx.call.side_effect = [
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

        result = _compute_morpho_market_health(
            ctx, reader, MORPHO_MARKET_ID, VAULT_ADDR, 14, "MORPHO"
        )

        assert result is not None
        assert result.current_ltv is None
        assert result.ltv_usage_percent is None

    def test_position_read_failure_returns_none(self):
        reader, ctx = _make_morpho_reader()
        ctx.call.side_effect = Exception("RPC error")

        result = _compute_morpho_market_health(
            ctx, reader, MORPHO_MARKET_ID, VAULT_ADDR, 14, "MORPHO"
        )

        assert result is None

    def test_market_data_failure_returns_none(self):
        reader, ctx = _make_morpho_reader()
        # First call succeeds (position), second fails (market)
        ctx.call.side_effect = [
            encode(["uint256", "uint128", "uint128"], [0, 100, 1000]),
            Exception("market read failed"),
        ]

        result = _compute_morpho_market_health(
            ctx, reader, MORPHO_MARKET_ID, VAULT_ADDR, 14, "MORPHO"
        )

        assert result is None


# ── Aave V3 health computation ───────────────────────────────────────


class TestComputeAaveMarketHealth:
    def test_no_debt_returns_zero_ltv(self):
        reader, ctx = _make_aave_reader()

        # total_collateral=10e8, debt=0, available=8e8, liq_threshold=8500, ltv=8000, hf=max
        ctx.call.return_value = encode(
            ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"],
            [10_000_000_00, 0, 8_000_000_00, 8500, 8000, 2**256 - 1],
        )

        result = _compute_aave_market_health(reader, VAULT_ADDR, 1, "AAVE_V3")

        assert result is not None
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

        result = _compute_aave_market_health(reader, VAULT_ADDR, 1, "AAVE_V3")

        assert result is not None
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

        result = _compute_aave_market_health(reader, VAULT_ADDR, 1, "AAVE_V3")

        assert result is not None
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

        result = _compute_aave_market_health(reader, VAULT_ADDR, 1, "AAVE_V3")

        assert result is not None
        assert result.is_critical
        assert result.is_warning
        assert result.health_factor == 1.02

    def test_reader_failure_returns_none(self):
        reader, ctx = _make_aave_reader()
        ctx.call.side_effect = Exception("RPC error")

        result = _compute_aave_market_health(reader, VAULT_ADDR, 1, "AAVE_V3")

        assert result is None


# ── fetch_vault_lending_health integration ───────────────────────────


class TestFetchVaultLendingHealth:
    def test_no_lending_markets_returns_empty(self):
        ctx = MagicMock()
        result = fetch_vault_lending_health(
            ctx,
            VAULT_ADDR,
            1,
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

    @patch("ipor_fusion.cli.vault_substrate._market_name", return_value="Morpho")
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
        morpho_mid = IporFusionMarkets.MORPHO
        substrate = bytes.fromhex("ab" * 32)

        result = fetch_vault_lending_health(
            ctx,
            VAULT_ADDR,
            chain_id=1,
            balance_fuse_market_ids=[morpho_mid],
            market_substrates={morpho_mid: [substrate]},
        )
        assert result.has_lending_positions
        assert len(result.markets) == 1
        assert result.markets[0].protocol == "morpho"

    def test_unknown_chain_skips_aave(self):
        ctx = MagicMock()
        # Chain 999999 has no Aave V3 pool configured
        result = fetch_vault_lending_health(
            ctx,
            VAULT_ADDR,
            999999,
            balance_fuse_market_ids=[1],  # AAVE_V3
            market_substrates={},
        )
        assert not result.has_lending_positions


# ── Health check integration with lending_health ─────────────────────


class TestHealthCheckLendingWarnings:
    def test_lending_warnings_in_health_check(self):
        from ipor_fusion.cli.vault_fetcher import _VaultData
        from ipor_fusion.cli.vault_health import (
            _BalanceFuseTotals,
            _Erc20Totals,
            _compute_health_check,
        )

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

        data = _VaultData(
            block_label="123",
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
            price_oracle_addr=addr,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=1.0,
            fuses=[],
            balance_fuses=[],
            instant_fuses=[],
            lending_health=lending,
        )

        bf = _BalanceFuseTotals(raw_total=100 * 10**18)
        erc20 = _Erc20Totals()

        health = _compute_health_check(data, bf, erc20, set())

        critical_warnings = [w for w in health.warnings if "CRITICAL" in w]
        assert len(critical_warnings) == 1
        assert "NEAR LIQUIDATION" in critical_warnings[0]

    def test_healthy_lending_in_ok(self):
        from ipor_fusion.cli.vault_fetcher import _VaultData
        from ipor_fusion.cli.vault_health import (
            _BalanceFuseTotals,
            _Erc20Totals,
            _compute_health_check,
        )

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

        data = _VaultData(
            block_label="123",
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
            price_oracle_addr=addr,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=1.0,
            fuses=[],
            balance_fuses=[],
            instant_fuses=[],
            lending_health=lending,
        )

        bf = _BalanceFuseTotals(raw_total=100 * 10**18)
        erc20 = _Erc20Totals()

        health = _compute_health_check(data, bf, erc20, set())

        assert any("aave_v3" in ok and "35.3%" in ok for ok in health.ok)
        assert not any("CRITICAL" in w or "WARNING" in w for w in health.warnings)

    def test_no_lending_health_no_warnings(self):
        from ipor_fusion.cli.vault_fetcher import _VaultData
        from ipor_fusion.cli.vault_health import (
            _BalanceFuseTotals,
            _Erc20Totals,
            _compute_health_check,
        )

        addr = Web3.to_checksum_address("0x" + "11" * 20)

        data = _VaultData(
            block_label="123",
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
            price_oracle_addr=addr,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=1.0,
            fuses=[],
            balance_fuses=[],
            instant_fuses=[],
            lending_health=None,
        )

        bf = _BalanceFuseTotals(raw_total=100 * 10**18)
        erc20 = _Erc20Totals()

        health = _compute_health_check(data, bf, erc20, set())

        # Only the standard reconciliation check, no lending warnings
        assert not any("CRITICAL" in w for w in health.warnings)

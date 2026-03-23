"""Unit tests for reader decoding — mock _call(), verify dataclass output."""

from unittest.mock import MagicMock

from eth_abi import encode
from web3 import Web3

from ipor_fusion.readers.morpho import (
    MorphoReader,
    MorphoMarket,
    MorphoPosition,
    MorphoMarketParams,
)
from ipor_fusion.readers.aave_v3 import AaveV3Reader, AaveV3UserAccountData
from ipor_fusion.readers.compound_v3 import CompoundV3Reader
from ipor_fusion.readers.uniswap_v3 import UniswapV3Reader, UniswapV3Position
from ipor_fusion.readers.ramses_v2 import RamsesV2Reader, RamsesV2Position
from ipor_fusion.types import Amount

# Deterministic test addresses
CONTRACT_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
USER_ADDR = Web3.to_checksum_address("0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa")
TOKEN_A = Web3.to_checksum_address("0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB")
TOKEN_B = Web3.to_checksum_address("0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC")
ORACLE = Web3.to_checksum_address("0xdDdDddDdDdddDDddDDddDDDDdDdDDdDDdDDDDDDd")
IRM = Web3.to_checksum_address("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE")

MARKET_ID = "a" * 64


def _make_reader(cls):
    """Create a reader instance with a mocked Web3Context.

    Returns (reader, mock_ctx) so tests can set return values on mock_ctx
    without accessing protected attributes.
    """
    ctx = MagicMock()
    return cls(ctx, CONTRACT_ADDR), ctx


# ── Morpho ─────────────────────────────────────────────────────────────


class TestMorphoReaderMarket:
    def test_market_decodes_all_fields(self):
        reader, ctx = _make_reader(MorphoReader)
        raw = encode(
            ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
            [1000, 2000, 3000, 4000, 1700000000, 500],
        )
        ctx.call.return_value = raw

        result = reader.market(MARKET_ID)

        assert isinstance(result, MorphoMarket)
        assert result.total_supply_assets == 1000
        assert result.total_supply_shares == 2000
        assert result.total_borrow_assets == 3000
        assert result.total_borrow_shares == 4000
        assert result.last_update == 1700000000
        assert result.fee == 500


class TestMorphoReaderPosition:
    def test_position_decodes_all_fields(self):
        reader, ctx = _make_reader(MorphoReader)
        raw = encode(["uint256", "uint128", "uint128"], [5000, 100, 200])
        ctx.call.return_value = raw

        result = reader.position(MARKET_ID, USER_ADDR)

        assert isinstance(result, MorphoPosition)
        assert result.supply_shares == 5000
        assert result.borrow_shares == 100
        assert result.collateral == 200


class TestMorphoReaderMarketParams:
    def test_market_params_decodes_addresses_and_lltv(self):
        reader, ctx = _make_reader(MorphoReader)
        raw = encode(
            ["address", "address", "address", "address", "uint256"],
            [TOKEN_A, TOKEN_B, ORACLE, IRM, 860000000000000000],
        )
        ctx.call.return_value = raw

        result = reader.market_params(MARKET_ID)

        assert isinstance(result, MorphoMarketParams)
        assert result.loan_token == TOKEN_A
        assert result.collateral_token == TOKEN_B
        assert result.oracle == ORACLE
        assert result.irm == IRM
        assert result.lltv == 860000000000000000


# ── Aave V3 ────────────────────────────────────────────────────────────


class TestAaveV3Reader:
    def test_get_user_account_data_decodes_all_fields(self):
        reader, ctx = _make_reader(AaveV3Reader)
        raw = encode(
            ["uint256", "uint256", "uint256", "uint256", "uint256", "uint256"],
            [
                10_000_000_000,
                5_000_000_000,
                3_000_000_000,
                8500,
                8000,
                1_500_000_000_000_000_000,
            ],
        )
        ctx.call.return_value = raw

        result = reader.get_user_account_data(USER_ADDR)

        assert isinstance(result, AaveV3UserAccountData)
        assert result.total_collateral_base == 10_000_000_000
        assert result.total_debt_base == 5_000_000_000
        assert result.available_borrows_base == 3_000_000_000
        assert result.current_liquidation_threshold == 8500
        assert result.ltv == 8000
        assert result.health_factor == 1_500_000_000_000_000_000


# ── Compound V3 ────────────────────────────────────────────────────────


class TestCompoundV3Reader:
    def test_balance_of(self):
        reader, ctx = _make_reader(CompoundV3Reader)
        raw = encode(["uint256"], [42_000])
        ctx.call.return_value = raw

        result = reader.balance_of(USER_ADDR)

        assert result == Amount(42_000)

    def test_borrow_balance_of(self):
        reader, ctx = _make_reader(CompoundV3Reader)
        raw = encode(["uint256"], [7_500])
        ctx.call.return_value = raw

        result = reader.borrow_balance_of(USER_ADDR)

        assert result == Amount(7_500)


# ── Uniswap V3 ────────────────────────────────────────────────────────


class TestUniswapV3Reader:
    def test_positions_decodes_all_fields(self):
        reader, ctx = _make_reader(UniswapV3Reader)
        raw = encode(
            [
                "uint96",
                "address",
                "address",
                "address",
                "uint24",
                "int24",
                "int24",
                "uint128",
                "uint256",
                "uint256",
                "uint128",
                "uint128",
            ],
            [
                1,
                USER_ADDR,
                TOKEN_A,
                TOKEN_B,
                3000,
                -887220,
                887220,
                500_000,
                100,
                200,
                50,
                60,
            ],
        )
        ctx.call.return_value = raw

        result = reader.positions(42)

        assert isinstance(result, UniswapV3Position)
        assert result.nonce == 1
        assert result.operator == USER_ADDR
        assert result.token0 == TOKEN_A
        assert result.token1 == TOKEN_B
        assert result.fee == 3000
        assert result.tick_lower == -887220
        assert result.tick_upper == 887220
        assert result.liquidity == 500_000
        assert result.fee_growth_inside0_last_x128 == 100
        assert result.fee_growth_inside1_last_x128 == 200
        assert result.tokens_owed0 == 50
        assert result.tokens_owed1 == 60


# ── Ramses V2 ──────────────────────────────────────────────────────────


class TestRamsesV2Reader:
    def test_positions_decodes_all_fields(self):
        reader, ctx = _make_reader(RamsesV2Reader)
        raw = encode(
            [
                "uint96",
                "address",
                "address",
                "address",
                "uint24",
                "int24",
                "int24",
                "uint128",
                "uint256",
                "uint256",
                "uint128",
                "uint128",
            ],
            [
                0,
                USER_ADDR,
                TOKEN_A,
                TOKEN_B,
                500,
                -100,
                100,
                1_000_000,
                300,
                400,
                10,
                20,
            ],
        )
        ctx.call.return_value = raw

        result = reader.positions(99)

        assert isinstance(result, RamsesV2Position)
        assert result.nonce == 0
        assert result.operator == USER_ADDR
        assert result.token0 == TOKEN_A
        assert result.token1 == TOKEN_B
        assert result.fee == 500
        assert result.tick_lower == -100
        assert result.tick_upper == 100
        assert result.liquidity == 1_000_000
        assert result.fee_growth_inside0_last_x128 == 300
        assert result.fee_growth_inside1_last_x128 == 400
        assert result.tokens_owed0 == 10
        assert result.tokens_owed1 == 20

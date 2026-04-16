"""Unit tests for reader decoding — mock _call(), verify dataclass output."""

from unittest.mock import MagicMock

from eth_abi import encode
from web3 import Web3

from ipor_fusion.readers.morpho import (
    MorphoReader,
    MorphoMarket,
    MorphoPosition,
    MorphoMarketParams,
    MorphoPositionBreakdown,
)
from ipor_fusion.readers.aave_v3 import (
    AaveV3PositionBreakdown,
    AaveV3Reader,
    AaveV3ReserveTokens,
    AaveV3UserAccountData,
)
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


class TestAaveV3ReaderReserveTokens:
    A_TOKEN = Web3.to_checksum_address("0x1010101010101010101010101010101010101010")
    STABLE_DEBT = Web3.to_checksum_address("0x2020202020202020202020202020202020202020")
    VARIABLE_DEBT = Web3.to_checksum_address(
        "0x3030303030303030303030303030303030303030"
    )
    IR_STRATEGY = Web3.to_checksum_address(
        "0x4040404040404040404040404040404040404040"
    )

    def _encode_reserve_data(self) -> bytes:
        return encode(
            [
                "uint256",
                "uint128",
                "uint128",
                "uint128",
                "uint128",
                "uint128",
                "uint40",
                "uint16",
                "address",
                "address",
                "address",
                "address",
                "uint128",
                "uint128",
                "uint128",
            ],
            [
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                self.A_TOKEN,
                self.STABLE_DEBT,
                self.VARIABLE_DEBT,
                self.IR_STRATEGY,
                0,
                0,
                0,
            ],
        )

    def test_reserve_tokens_returns_three_addresses(self):
        reader, ctx = _make_reader(AaveV3Reader)
        ctx.call.return_value = self._encode_reserve_data()

        tokens = reader.reserve_tokens(TOKEN_A)

        assert isinstance(tokens, AaveV3ReserveTokens)
        assert tokens.a_token == self.A_TOKEN
        assert tokens.stable_debt_token == self.STABLE_DEBT
        assert tokens.variable_debt_token == self.VARIABLE_DEBT


class TestAaveV3ReaderPositionBreakdown:
    A_TOKEN = Web3.to_checksum_address("0x1010101010101010101010101010101010101010")
    STABLE_DEBT = Web3.to_checksum_address("0x2020202020202020202020202020202020202020")
    VARIABLE_DEBT = Web3.to_checksum_address(
        "0x3030303030303030303030303030303030303030"
    )
    ZERO = Web3.to_checksum_address("0x0000000000000000000000000000000000000000")

    @staticmethod
    def _reserve_data(stable_debt_token, a_token, variable_debt_token) -> bytes:
        return encode(
            [
                "uint256",
                "uint128",
                "uint128",
                "uint128",
                "uint128",
                "uint128",
                "uint40",
                "uint16",
                "address",
                "address",
                "address",
                "address",
                "uint128",
                "uint128",
                "uint128",
            ],
            [
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                0,
                a_token,
                stable_debt_token,
                variable_debt_token,
                Web3.to_checksum_address("0x" + "44" * 20),
                0,
                0,
                0,
            ],
        )

    def test_position_breakdown_aggregates_balances(self):
        reader, ctx = _make_reader(AaveV3Reader)
        ctx.call.side_effect = [
            self._reserve_data(self.STABLE_DEBT, self.A_TOKEN, self.VARIABLE_DEBT),
            encode(["uint256"], [12345]),  # aToken.balanceOf
            encode(["uint256"], [678]),  # variableDebtToken.balanceOf
            encode(["uint256"], [9]),  # stableDebtToken.balanceOf
        ]

        breakdown = reader.position_breakdown(TOKEN_A, USER_ADDR)

        assert isinstance(breakdown, AaveV3PositionBreakdown)
        assert breakdown.asset == TOKEN_A
        assert breakdown.a_token == self.A_TOKEN
        assert breakdown.variable_debt_token == self.VARIABLE_DEBT
        assert breakdown.stable_debt_token == self.STABLE_DEBT
        assert breakdown.supply == 12345
        assert breakdown.variable_debt == 678
        assert breakdown.stable_debt == 9
        assert not breakdown.is_empty

    def test_position_breakdown_skips_stable_when_zero_address(self):
        reader, ctx = _make_reader(AaveV3Reader)
        ctx.call.side_effect = [
            self._reserve_data(self.ZERO, self.A_TOKEN, self.VARIABLE_DEBT),
            encode(["uint256"], [0]),  # aToken.balanceOf
            encode(["uint256"], [0]),  # variableDebtToken.balanceOf
        ]

        breakdown = reader.position_breakdown(TOKEN_A, USER_ADDR)

        assert breakdown.stable_debt == 0
        assert breakdown.is_empty


class TestMorphoReaderPositionBreakdown:
    def test_position_breakdown_converts_shares_to_assets(self):
        reader, ctx = _make_reader(MorphoReader)
        # position(): supply_shares=1000, borrow_shares=500, collateral=42
        position_raw = encode(["uint256", "uint128", "uint128"], [1000, 500, 42])
        # market(): supply_assets=2_000_000, supply_shares=2_000_000,
        #          borrow_assets=1_500_000, borrow_shares=1_500_000
        market_raw = encode(
            ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
            [2_000_000, 2_000_000, 1_500_000, 1_500_000, 1700000000, 0],
        )
        params_raw = encode(
            ["address", "address", "address", "address", "uint256"],
            [TOKEN_A, TOKEN_B, ORACLE, IRM, 860000000000000000],
        )
        ctx.call.side_effect = [position_raw, market_raw, params_raw]

        result = reader.position_breakdown(MARKET_ID, USER_ADDR)

        assert isinstance(result, MorphoPositionBreakdown)
        assert result.market_id == MARKET_ID
        assert result.loan_token == TOKEN_A
        assert result.collateral_token == TOKEN_B
        assert result.collateral == 42
        # borrow_assets = ceil(500 * (1_500_000+1) / (1_500_000+1)) = 500
        assert result.borrow_assets == 500
        # supply_assets = 1000 * 2_000_000 // 2_000_000 = 1000
        assert result.supply_assets == 1000

    def test_position_breakdown_handles_empty_market(self):
        reader, ctx = _make_reader(MorphoReader)
        position_raw = encode(["uint256", "uint128", "uint128"], [0, 0, 0])
        market_raw = encode(
            ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
            [0, 0, 0, 0, 0, 0],
        )
        params_raw = encode(
            ["address", "address", "address", "address", "uint256"],
            [TOKEN_A, TOKEN_B, ORACLE, IRM, 0],
        )
        ctx.call.side_effect = [position_raw, market_raw, params_raw]

        result = reader.position_breakdown(MARKET_ID, USER_ADDR)

        assert result.borrow_assets == 0
        assert result.supply_assets == 0
        assert result.collateral == 0


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

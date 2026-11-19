"""Offline tests for the Lido Lend readers: selectors, decoding, batch order."""

from unittest.mock import MagicMock

import pytest
from _multicall import multicall_aware
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.errors import MorphoMarketNotFoundError, UnsupportedChainError
from ipor_fusion.readers.lido_lend import (
    LIDO_LEND_CORE_ADDRESSES,
    LidoLendBorrowerPosition,
    LidoLendCollateralActivationConfig,
    LidoLendFuseReader,
    LidoLendMarketController,
    LidoLendNextSettlement,
    LidoLendReader,
    LidoLendSettlementConfig,
    LidoLendSettlementLtv,
    LidoLendSettlementQuote,
    LidoLendSettlementSlice,
    lido_lend_core_address,
)
from ipor_fusion.readers.morpho import MorphoReader
from ipor_fusion.types import ZERO_ADDRESS, ChainId, MorphoBlueMarketId

_addr = Web3.to_checksum_address
CORE = _addr("0x78d3262E82A35d290507bCF9B4834735DdA6b854")
CONTROLLER = _addr("0x567032366c3Ccc84Af77647fffb3e7a00B8b3008")
USER = _addr("0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa")
LOAN = _addr("0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB")
COLLATERAL = _addr("0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC")
ORACLE = _addr("0xdDdDddDdDdddDDddDDddDDDDdDdDDdDDdDDDDDDd")
IRM = _addr("0x" + "ee" * 20)
MARKET_ID = MorphoBlueMarketId("0x" + "ab" * 32)

POSITION_TYPE = "(uint256,uint256,uint256,uint256,uint256,bool,uint256,uint256,bool,uint256,uint256)"
POSITION_VALUE = (1, 2, 3, 1700000000, 4, True, 5, 6, True, 7, 8)
ACTIVATION_TYPE = "(uint40,uint40,uint128,uint128,uint40,uint128,uint40,uint40,uint40)"
ACTIVATION_VALUE = (600, 7200, 10**22, 10**18, 0, 10**19, 129600, 7200, 7200)
SLICE_TYPE = "(uint256,uint256,uint256)"


def _selector(signature: str) -> bytes:
    return function_signature_to_4byte_selector(signature)


def _ctx(raw: bytes) -> MagicMock:
    ctx = MagicMock()
    ctx.call.return_value = raw
    return ctx


class TestCoreAddress:
    def test_hoodi_core(self):
        assert lido_lend_core_address(ChainId(560048)) == CORE
        assert LIDO_LEND_CORE_ADDRESSES == {ChainId(560048): CORE}

    def test_unknown_chain_names_hoodi(self):
        with pytest.raises(UnsupportedChainError) as exc_info:
            lido_lend_core_address(ChainId(1))
        message = str(exc_info.value)
        assert "1 (ethereum)" in message
        assert "560048 (hoodi)" in message

    def test_unnamed_chain(self):
        with pytest.raises(UnsupportedChainError, match="chain 999999;"):
            lido_lend_core_address(ChainId(999999))


class TestFuseReader:
    def test_lido_lend_selector_and_decoding(self):
        call = LidoLendFuseReader(USER).lido_lend()
        assert call.data == bytes.fromhex("39953552")
        assert call.call(_ctx(encode(["address"], [CORE]))) == CORE


class TestControllerSelectors:
    @pytest.mark.parametrize(
        ("call", "signature", "selector"),
        [
            (
                LidoLendMarketController(CONTROLLER).position_of(USER),
                "positionOf(address)",
                "fd2d39c5",
            ),
            (
                LidoLendMarketController(CONTROLLER).collateral_activation_config(),
                "collateralActivationConfig()",
                "60873bfc",
            ),
            (
                LidoLendMarketController(CONTROLLER).next_settlement_borrower(5),
                "nextSettlementBorrower(uint256)",
                "b9c7a210",
            ),
            (
                LidoLendMarketController(CONTROLLER).settlement_for(USER, 5),
                "settlementFor(address,uint256)",
                "7f1d2a90",
            ),
            (
                LidoLendMarketController(CONTROLLER).settlement_config(),
                "settlementConfig()",
                "b457b578",
            ),
            (
                LidoLendMarketController(CONTROLLER).settlement_ltv(USER),
                "settlementLtv(address)",
                "d10f050e",
            ),
            (
                LidoLendMarketController(CONTROLLER).has_bad_debt(),
                "hasBadDebt()",
                "7992a979",
            ),
            (
                LidoLendMarketController(CONTROLLER).has_instant_activation(USER),
                "hasInstantActivation(address)",
                "98e1214e",
            ),
            (
                LidoLendMarketController(CONTROLLER).is_paused(),
                "isPaused()",
                "b187bd26",
            ),
            (
                LidoLendMarketController(CONTROLLER).is_frozen(),
                "isFrozen()",
                "33eeb147",
            ),
            (
                LidoLendMarketController(CONTROLLER).get_resume_since_timestamp(),
                "getResumeSinceTimestamp()",
                "589ff76c",
            ),
            (
                LidoLendMarketController(CONTROLLER).supply_cap(),
                "supplyCap()",
                "8f770ad0",
            ),
            (
                LidoLendMarketController(CONTROLLER).max_deposit(),
                "maxDeposit()",
                "6083e59a",
            ),
            (
                LidoLendMarketController(CONTROLLER).max_withdrawable_assets(USER),
                "maxWithdrawableAssets(address)",
                "f04209b6",
            ),
            (
                LidoLendMarketController(CONTROLLER).settlement_borrowers_count(),
                "settlementBorrowersCount()",
                "139294b2",
            ),
            (
                LidoLendReader(CORE).market_controller(MARKET_ID),
                "marketController(bytes32)",
                "961496be",
            ),
            (
                LidoLendReader(CORE).is_settlement_enabled(MARKET_ID),
                "isSettlementEnabled(bytes32)",
                "d7bb876e",
            ),
        ],
    )
    def test_selector(self, call, signature, selector):
        assert call.data[:4] == _selector(signature)
        assert call.data[:4].hex() == selector

    def test_arguments_are_encoded(self):
        controller = LidoLendMarketController(CONTROLLER)
        assert controller.settlement_for(USER, 5).data[4:] == encode(
            ["address", "uint256"], [USER, 5]
        )
        assert LidoLendReader(CORE).market_controller(MARKET_ID).data[
            4:
        ] == bytes.fromhex("ab" * 32)


class TestControllerDecoding:
    def test_position_of(self):
        raw = encode([POSITION_TYPE], [POSITION_VALUE])
        result = LidoLendMarketController(CONTROLLER).position_of(USER).call(_ctx(raw))
        assert result == LidoLendBorrowerPosition(
            active=1,
            activating=2,
            dust=3,
            unlock_time=1700000000,
            flag_count=4,
            banned=True,
            debt_assets=5,
            settlement_ltv=6,
            is_next_to_settle=True,
            personal_slice_assets=7,
            queue_root_slice_assets=8,
        )

    def test_collateral_activation_config(self):
        raw = encode([ACTIVATION_TYPE], [ACTIVATION_VALUE])
        result = (
            LidoLendMarketController(CONTROLLER)
            .collateral_activation_config()
            .call(_ctx(raw))
        )
        assert result == LidoLendCollateralActivationConfig(
            min_activation_delay=600,
            max_activation_delay=7200,
            max_delay_inflow=10**22,
            min_deposit_amount=10**18,
            activating_lock_duration=0,
            min_activation_amount=10**19,
            max_guardians_quorum_delay=129600,
            delay_per_strike=7200,
            delay_per_suspect=7200,
        )

    def test_next_settlement_borrower(self):
        raw = encode(["address", SLICE_TYPE], [USER, (11, 12, 13)])
        result = (
            LidoLendMarketController(CONTROLLER)
            .next_settlement_borrower(0)
            .call(_ctx(raw))
        )
        assert result == LidoLendNextSettlement(
            borrower=USER,
            slice=LidoLendSettlementSlice(
                supply_shares=11, borrow_shares=12, remaining_borrow_shares=13
            ),
        )

    def test_next_settlement_borrower_empty_queue(self):
        raw = encode(["address", SLICE_TYPE], [ZERO_ADDRESS, (0, 0, 0)])
        result = (
            LidoLendMarketController(CONTROLLER)
            .next_settlement_borrower(0)
            .call(_ctx(raw))
        )
        assert result.borrower == ZERO_ADDRESS
        assert result.slice.borrow_shares == 0

    def test_settlement_for_nested_pair(self):
        raw = encode([f"({SLICE_TYPE},{SLICE_TYPE})"], [((1, 2, 3), (4, 5, 6))])
        result = (
            LidoLendMarketController(CONTROLLER).settlement_for(USER, 0).call(_ctx(raw))
        )
        assert result == LidoLendSettlementQuote(
            personal=LidoLendSettlementSlice(1, 2, 3),
            queue_root=LidoLendSettlementSlice(4, 5, 6),
        )

    def test_settlement_config(self):
        raw = encode(["(uint16,uint96,uint96,uint16)"], [(100, 10**19, 10**19, 50)])
        result = (
            LidoLendMarketController(CONTROLLER).settlement_config().call(_ctx(raw))
        )
        assert result == LidoLendSettlementConfig(
            slice_floor_bps=100,
            slice_floor_assets=10**19,
            full_cut_below_collateral=10**19,
            ltv_step_bps=50,
        )

    def test_settlement_ltv_two_values(self):
        raw = encode(["uint256", "uint256"], [9 * 10**17, 77])
        result = (
            LidoLendMarketController(CONTROLLER).settlement_ltv(USER).call(_ctx(raw))
        )
        assert result == LidoLendSettlementLtv(ltv=9 * 10**17, slice_assets=77)

    @pytest.mark.parametrize("flag", [True, False])
    def test_bool_views(self, flag):
        raw = encode(["bool"], [flag])
        controller = LidoLendMarketController(CONTROLLER)
        assert controller.has_bad_debt().call(_ctx(raw)) is flag
        assert controller.has_instant_activation(USER).call(_ctx(raw)) is flag
        assert controller.is_paused().call(_ctx(raw)) is flag
        assert controller.is_frozen().call(_ctx(raw)) is flag
        assert (
            LidoLendReader(CORE).is_settlement_enabled(MARKET_ID).call(_ctx(raw))
            is flag
        )

    def test_uint_views(self):
        raw = encode(["uint256"], [42])
        controller = LidoLendMarketController(CONTROLLER)
        assert controller.get_resume_since_timestamp().call(_ctx(raw)) == 42
        assert controller.supply_cap().call(_ctx(raw)) == 42
        assert controller.max_deposit().call(_ctx(raw)) == 42
        assert controller.max_withdrawable_assets(USER).call(_ctx(raw)) == 42
        assert controller.settlement_borrowers_count().call(_ctx(raw)) == 42

    def test_market_controller_checksum(self):
        raw = encode(["address"], [CONTROLLER])
        assert (
            LidoLendReader(CORE).market_controller(MARKET_ID).call(_ctx(raw))
            == CONTROLLER
        )


def _core_handler(
    calls: list[tuple[str, str]], *, last_update=1700000000, controller=CONTROLLER
):
    """Handler answering the core and controller views of one market."""
    names = {
        _selector("position(bytes32,address)"): "position",
        _selector("market(bytes32)"): "market",
        _selector("idToMarketParams(bytes32)"): "params",
        _selector("marketController(bytes32)"): "controller",
        _selector("isSettlementEnabled(bytes32)"): "enabled",
        _selector("positionOf(address)"): "positionOf",
        _selector("isPaused()"): "paused",
        _selector("isFrozen()"): "frozen",
        _selector("hasBadDebt()"): "badDebt",
        _selector("hasInstantActivation(address)"): "instant",
    }
    answers = {
        "position": encode(["uint256", "uint128", "uint128"], [0, 1_000_000, 500]),
        "market": encode(
            ["uint128"] * 6,
            [2_000, 1_000_000, 1_000, 1_000_000, last_update, 0],
        ),
        "params": encode(
            ["address", "address", "address", "address", "uint256"],
            [LOAN, COLLATERAL, ORACLE, IRM, 95 * 10**16],
        ),
        "controller": encode(["address"], [controller]),
        "enabled": encode(["bool"], [True]),
        "positionOf": encode([POSITION_TYPE], [POSITION_VALUE]),
        "paused": encode(["bool"], [False]),
        "frozen": encode(["bool"], [True]),
        "badDebt": encode(["bool"], [False]),
        "instant": encode(["bool"], [True]),
    }

    def handler(to: str, data: bytes) -> bytes:
        name = names[bytes(data[:4])]
        calls.append((to, name))
        return answers[name]

    return handler


class TestReadPositionState:
    def test_two_batches_core_then_controller(self):
        calls: list[tuple[str, str]] = []
        ctx = MagicMock()
        ctx.call.side_effect = multicall_aware(_core_handler(calls))

        state = LidoLendReader(CORE).read_position_state(ctx, MARKET_ID, USER)

        assert calls == [
            (CORE, "position"),
            (CORE, "market"),
            (CORE, "params"),
            (CORE, "controller"),
            (CORE, "enabled"),
            (CONTROLLER, "positionOf"),
            (CONTROLLER, "paused"),
            (CONTROLLER, "frozen"),
            (CONTROLLER, "badDebt"),
            (CONTROLLER, "instant"),
        ]
        # one aggregate3 per batch, nothing else
        assert ctx.call.call_count == 2
        assert state.controller == CONTROLLER
        assert state.settlement_enabled is True
        assert state.breakdown.market_id == MARKET_ID
        assert state.breakdown.collateral == 500
        assert state.breakdown.loan_token == LOAN
        assert state.breakdown.collateral_token == COLLATERAL
        assert state.borrower is not None
        assert state.borrower.debt_assets == 5
        assert state.borrower.unlock_time == 1700000000
        assert state.paused is False
        assert state.frozen is True
        assert state.bad_debt is False
        assert state.instant_activation is True

    def test_missing_market_raises(self):
        calls: list[tuple[str, str]] = []
        ctx = MagicMock()
        ctx.chain_id = 560048
        ctx.call.side_effect = multicall_aware(_core_handler(calls, last_update=0))

        with pytest.raises(MorphoMarketNotFoundError) as exc_info:
            LidoLendReader(CORE).read_position_state(ctx, MARKET_ID, USER)

        assert MARKET_ID in str(exc_info.value)
        assert "560048" in str(exc_info.value)
        assert CORE in str(exc_info.value)
        assert all(to == CORE for to, _ in calls)

    def test_zero_controller_skips_second_batch(self):
        calls: list[tuple[str, str]] = []
        ctx = MagicMock()
        ctx.call.side_effect = multicall_aware(
            _core_handler(calls, controller=ZERO_ADDRESS)
        )

        state = LidoLendReader(CORE).read_position_state(ctx, MARKET_ID, USER)

        assert ctx.call.call_count == 1
        assert {to for to, _ in calls} == {CORE}
        assert state.controller == ZERO_ADDRESS
        assert state.borrower is None
        assert state.paused is None
        assert state.frozen is None
        assert state.bad_debt is None
        assert state.instant_activation is None
        assert state.breakdown.collateral == 500


def test_lido_lend_reader_is_a_morpho_reader():
    assert issubclass(LidoLendReader, MorphoReader)
    call = LidoLendReader(CORE).market(MARKET_ID)
    assert call.data[:4] == _selector("market(bytes32)")

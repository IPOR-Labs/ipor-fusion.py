"""Unit tests for `Multicall3` — batching view `Call`s through aggregate3."""

from unittest.mock import MagicMock

import pytest
from _multicall import decode_aggregate3, is_aggregate3, multicall_aware
from eth_abi import decode, encode
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion import MULTICALL3_ADDRESS, Multicall3
from ipor_fusion.core.contract import Call
from ipor_fusion.errors import EmptyCallResultError

TOKEN_A = Web3.to_checksum_address("0x" + "aa" * 20)
TOKEN_B = Web3.to_checksum_address("0x" + "bb" * 20)
SELECTOR = bytes.fromhex("70a08231")


def _uint_call(ctx: MagicMock, target: str, arg: int) -> Call[int]:
    return Call(
        to=target,  # type: ignore[arg-type]
        data=SELECTOR + encode(["uint256"], [arg]),
        output_types=["uint256"],
        ctx=ctx,
    )


def _echo(_to: str, data: bytes) -> bytes:
    """Answer each call with its own uint256 argument doubled."""
    (arg,) = decode(["uint256"], data[4:])
    return encode(["uint256"], [arg * 2])


def _ctx(handler=_echo) -> MagicMock:
    ctx = MagicMock()
    ctx.call.side_effect = multicall_aware(handler)
    return ctx


class TestAggregate3Encoding:
    def test_packs_every_call_with_allow_failure(self):
        ctx = _ctx()
        calls = [_uint_call(ctx, TOKEN_A, 1), _uint_call(ctx, TOKEN_B, 2)]

        raw = Multicall3(ctx).aggregate3(calls)

        assert raw.to == MULTICALL3_ADDRESS
        (packed,) = decode(["(address,bool,bytes)[]"], raw.data[4:])
        assert [
            (Web3.to_checksum_address(t), allow, bytes(d)) for t, allow, d in packed
        ] == [(call.to, True, call.data) for call in calls]


class TestAggregate:
    def test_decodes_results_in_order_in_one_round_trip(self):
        ctx = _ctx()
        calls = [_uint_call(ctx, TOKEN_A, n) for n in (1, 2, 3)]

        assert Multicall3(ctx).aggregate(calls) == [2, 4, 6]
        ctx.call.assert_called_once()
        (to, data), _ = ctx.call.call_args
        assert is_aggregate3(to, data)

    def test_applies_each_calls_decoder(self):
        ctx = _ctx()
        call = Call(
            to=TOKEN_A,
            data=SELECTOR + encode(["uint256"], [21]),
            output_types=["uint256"],
            decoder=str,
            ctx=ctx,
        )

        assert Multicall3(ctx).aggregate([call]) == ["42"]

    def test_failed_call_raises_its_own_revert(self):
        def handler(to: str, data: bytes) -> bytes:
            if to == TOKEN_B:
                raise ContractLogicError("execution reverted: NotAllowed")
            return _echo(to, data)

        ctx = _ctx(handler)
        calls = [_uint_call(ctx, TOKEN_A, 1), _uint_call(ctx, TOKEN_B, 2)]

        with pytest.raises(ContractLogicError, match="NotAllowed"):
            Multicall3(ctx).aggregate(calls)

    def test_empty_return_raises_empty_call_result(self):
        ctx = _ctx(lambda _to, _data: b"")

        with pytest.raises(EmptyCallResultError):
            Multicall3(ctx).aggregate([_uint_call(ctx, TOKEN_A, 1)])

    def test_no_calls_skip_the_rpc(self):
        ctx = _ctx()

        assert Multicall3(ctx).aggregate([]) == []
        ctx.call.assert_not_called()


class TestTryAggregate:
    def test_failures_become_none_without_extra_calls(self):
        def handler(to: str, data: bytes) -> bytes:
            (arg,) = decode(["uint256"], data[4:])
            if arg == 1:
                raise ContractLogicError("revert")
            if arg == 2:
                return b""
            if arg == 3:
                return b"\x01"  # too short to decode
            return _echo(to, data)

        ctx = _ctx(handler)
        calls = [_uint_call(ctx, TOKEN_A, n) for n in (1, 2, 3, 4)]

        assert Multicall3(ctx).try_aggregate(calls) == [None, None, None, 8]
        ctx.call.assert_called_once()


class TestBatching:
    def test_splits_into_batches_and_keeps_order(self):
        ctx = _ctx()
        calls = [_uint_call(ctx, TOKEN_A, n) for n in range(5)]

        results = Multicall3(ctx, batch_size=2).aggregate(calls)

        assert results == [0, 2, 4, 6, 8]
        batch_sizes = [
            len(decode_aggregate3(c.args[1])) for c in ctx.call.call_args_list
        ]
        assert batch_sizes == [2, 2, 1]

    def test_rejects_non_positive_batch_size(self):
        with pytest.raises(ValueError, match="batch_size"):
            Multicall3(MagicMock(), batch_size=0)


class TestWithoutMulticall3:
    """No Multicall3 at the address (other chain, or a block before its
    deployment): aggregate3 answers with empty data, so each call runs alone."""

    @staticmethod
    def _ctx_without_multicall(handler) -> MagicMock:
        def call(to: str, data: bytes, block=None):
            return b"" if to == MULTICALL3_ADDRESS else handler(to, data)

        ctx = MagicMock()
        ctx.call.side_effect = call
        return ctx

    def test_aggregate_falls_back_to_individual_calls(self):
        ctx = self._ctx_without_multicall(_echo)
        calls = [_uint_call(ctx, TOKEN_A, 1), _uint_call(ctx, TOKEN_B, 2)]

        assert Multicall3(ctx).aggregate(calls) == [2, 4]
        targets = [c.args[0] for c in ctx.call.call_args_list]
        assert targets == [MULTICALL3_ADDRESS, TOKEN_A, TOKEN_B]

    def test_try_aggregate_falls_back_with_the_same_failure_semantics(self):
        def handler(to: str, data: bytes) -> bytes:
            if to == TOKEN_B:
                raise ContractLogicError("revert")
            return _echo(to, data)

        ctx = self._ctx_without_multicall(handler)
        calls = [_uint_call(ctx, TOKEN_A, 1), _uint_call(ctx, TOKEN_B, 2)]

        assert Multicall3(ctx).try_aggregate(calls) == [2, None]

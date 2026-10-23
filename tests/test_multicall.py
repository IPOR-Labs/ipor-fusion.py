"""Unit tests for `Multicall3` — batching view `Call`s through aggregate3."""

from unittest.mock import MagicMock

import pytest
from _multicall import decode_aggregate3, is_aggregate3, multicall_aware
from eth_abi import decode, encode
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion import MULTICALL3_ADDRESS, ChainId, Multicall3, Web3Context
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


class TestPinnedContext:
    """Multicall3 owns its context: the batch, the re-run of a failed
    sub-call and the per-call fallback all read at the pinned block."""

    BLOCK = 123

    def _pinned(self, *, multicall: bool, failing: str | None = None):
        web3 = MagicMock()
        seen: list[tuple[str, object]] = []

        def eth_call(tx, block_identifier=None):
            to, data = tx["to"], tx["data"]
            seen.append((to, block_identifier))
            if to == MULTICALL3_ADDRESS:
                if not multicall:
                    return b""
                return multicall_aware(_answer)(to, data)
            return _answer(to, data)

        def _answer(to: str, data: bytes) -> bytes:
            if to == failing:
                raise ContractLogicError("execution reverted: NotAllowed")
            return _echo(to, data)

        web3.eth.call.side_effect = eth_call
        ctx = Web3Context(web3, ChainId(1)).at_block(self.BLOCK)
        return ctx, seen

    def test_fallback_without_multicall_stays_at_the_pinned_block(self):
        ctx, seen = self._pinned(multicall=False)
        calls = [_uint_call(ctx, TOKEN_A, 1), _uint_call(ctx, TOKEN_B, 2)]

        assert Multicall3(ctx).aggregate(calls) == [2, 4]
        assert Multicall3(ctx).try_aggregate(calls) == [2, 4]
        assert [to for to, _ in seen] == [MULTICALL3_ADDRESS, TOKEN_A, TOKEN_B] * 2
        assert {block for _, block in seen} == {self.BLOCK}

    def test_rerun_of_a_failed_subcall_stays_at_the_pinned_block(self):
        ctx, seen = self._pinned(multicall=True, failing=TOKEN_B)
        calls = [_uint_call(ctx, TOKEN_A, 1), _uint_call(ctx, TOKEN_B, 2)]

        with pytest.raises(ContractLogicError, match="NotAllowed"):
            Multicall3(ctx).aggregate(calls)
        assert [to for to, _ in seen] == [MULTICALL3_ADDRESS, TOKEN_B]
        assert {block for _, block in seen} == {self.BLOCK}


def _str_call(target: str, arg: int) -> Call[str]:
    return Call(
        to=target,  # type: ignore[arg-type]
        data=SELECTOR + encode(["uint256"], [arg]),
        output_types=["uint256"],
        decoder=str,
    )


def _reverting_b(to: str, data: bytes) -> bytes:
    if to == TOKEN_B:
        raise ContractLogicError("execution reverted: NotAllowed")
    return _echo(to, data)


class TestBatch:
    """Reads of different types in one round trip, each keeping its type."""

    def test_mixed_types_decode_in_one_round_trip(self):
        ctx = _ctx()
        batch = Multicall3(ctx).batch()
        amount = batch.add(_uint_call(ctx, TOKEN_A, 1))
        label = batch.add(_str_call(TOKEN_A, 2))
        optional = batch.try_add(_uint_call(ctx, TOKEN_A, 3))

        assert len(batch) == 3
        batch.run()

        assert (amount.value, label.value, optional.value) == (2, "4", 6)
        ctx.call.assert_called_once()
        (to, data), _ = ctx.call.call_args
        assert is_aggregate3(to, data)

    def test_a_failed_try_add_is_none_without_an_extra_call(self):
        ctx = _ctx(_reverting_b)
        batch = Multicall3(ctx).batch()
        kept = batch.add(_uint_call(ctx, TOKEN_A, 1))
        dropped = batch.try_add(_uint_call(ctx, TOKEN_B, 2))

        batch.run()

        assert (kept.value, dropped.value) == (2, None)
        ctx.call.assert_called_once()

    def test_try_add_maps_empty_and_undecodable_data_to_none(self):
        def handler(to: str, data: bytes) -> bytes:
            (arg,) = decode(["uint256"], data[4:])
            if arg == 1:
                return b""
            if arg == 2:
                return b"\x01"  # too short to decode
            return _echo(to, data)

        ctx = _ctx(handler)
        batch = Multicall3(ctx).batch()
        reads = [batch.try_add(_uint_call(ctx, TOKEN_A, n)) for n in (1, 2, 3)]

        batch.run()

        assert [read.value for read in reads] == [None, None, 6]

    def test_a_failed_add_raises_its_own_revert(self):
        ctx = _ctx(_reverting_b)
        batch = Multicall3(ctx).batch()
        batch.try_add(_uint_call(ctx, TOKEN_A, 1))
        batch.add(_uint_call(ctx, TOKEN_B, 2))

        with pytest.raises(ContractLogicError, match="NotAllowed"):
            batch.run()

    def test_a_failed_add_with_empty_data_raises_empty_call_result(self):
        ctx = _ctx(lambda _to, _data: b"")
        batch = Multicall3(ctx).batch()
        batch.add(_uint_call(ctx, TOKEN_A, 1))

        with pytest.raises(EmptyCallResultError):
            batch.run()

    def test_value_before_run_raises(self):
        batch = Multicall3(_ctx()).batch()
        read = batch.add(_uint_call(MagicMock(), TOKEN_A, 1))

        with pytest.raises(RuntimeError, match=r"Multicall3Batch\.run\(\)"):
            _ = read.value

    def test_a_batch_runs_once(self):
        ctx = _ctx()
        batch = Multicall3(ctx).batch()
        batch.add(_uint_call(ctx, TOKEN_A, 1))
        batch.run()

        with pytest.raises(RuntimeError, match="already ran"):
            batch.run()
        with pytest.raises(RuntimeError, match="already ran"):
            batch.add(_uint_call(ctx, TOKEN_A, 2))
        ctx.call.assert_called_once()

    def test_a_failed_run_does_not_run_again(self):
        ctx = _ctx(_reverting_b)
        batch = Multicall3(ctx).batch()
        batch.add(_uint_call(ctx, TOKEN_B, 1))
        with pytest.raises(ContractLogicError):
            batch.run()

        with pytest.raises(RuntimeError, match="already ran"):
            batch.run()

    def test_an_empty_batch_skips_the_rpc(self):
        ctx = _ctx()
        Multicall3(ctx).batch().run()

        ctx.call.assert_not_called()

    def test_splits_into_chunks_and_keeps_order(self):
        ctx = _ctx()
        batch = Multicall3(ctx, batch_size=2).batch()
        reads = [batch.add(_uint_call(ctx, TOKEN_A, n)) for n in range(5)]

        batch.run()

        assert [read.value for read in reads] == [0, 2, 4, 6, 8]
        batch_sizes = [
            len(decode_aggregate3(c.args[1])) for c in ctx.call.call_args_list
        ]
        assert batch_sizes == [2, 2, 1]

    def test_falls_back_to_one_call_per_read_without_multicall3(self):
        ctx = TestWithoutMulticall3._ctx_without_multicall(_reverting_b)
        batch = Multicall3(ctx).batch()
        kept = batch.add(_uint_call(ctx, TOKEN_A, 1))
        dropped = batch.try_add(_uint_call(ctx, TOKEN_B, 2))

        batch.run()

        assert (kept.value, dropped.value) == (2, None)
        targets = [c.args[0] for c in ctx.call.call_args_list]
        assert targets == [MULTICALL3_ADDRESS, TOKEN_A, TOKEN_B]

    def test_a_failed_add_raises_in_the_fallback(self):
        ctx = TestWithoutMulticall3._ctx_without_multicall(_reverting_b)
        batch = Multicall3(ctx).batch()
        batch.add(_uint_call(ctx, TOKEN_B, 1))

        with pytest.raises(ContractLogicError, match="NotAllowed"):
            batch.run()

    def test_the_rerun_of_a_failed_add_stays_at_the_pinned_block(self):
        ctx, seen = TestPinnedContext()._pinned(multicall=True, failing=TOKEN_B)
        batch = Multicall3(ctx).batch()
        batch.add(_uint_call(ctx, TOKEN_A, 1))
        batch.add(_uint_call(ctx, TOKEN_B, 2))

        with pytest.raises(ContractLogicError, match="NotAllowed"):
            batch.run()
        assert [to for to, _ in seen] == [MULTICALL3_ADDRESS, TOKEN_B]
        assert {block for _, block in seen} == {TestPinnedContext.BLOCK}


class TestNamedDecodeErrors:
    """A Call whose decode_error names an undecodable answer (an earlier
    dispatcher's six-word observation): required reads raise the named error,
    optional reads are None, with Multicall3 and without it."""

    DISPATCHER = Web3.to_checksum_address("0x" + "cc" * 20)
    SIX_WORDS = encode(["uint256"] * 6, [1, 2, 3, 4, 5, 6])

    def _observation(self) -> Call[object]:
        from ipor_fusion.crosschain import CcipCrosschainDispatcher

        return CcipCrosschainDispatcher(self.DISPATCHER).observation()

    def _answer(self, _to: str, _data: bytes) -> bytes:
        return self.SIX_WORDS

    def _contexts(self) -> list[MagicMock]:
        return [
            _ctx(self._answer),
            TestWithoutMulticall3._ctx_without_multicall(self._answer),
        ]

    def test_required_reads_raise_the_named_error(self):
        from ipor_fusion.errors import UnsupportedContractVersionError

        for ctx in self._contexts():
            with pytest.raises(
                UnsupportedContractVersionError, match="released 8-field"
            ):
                self._observation().call(ctx)
            with pytest.raises(UnsupportedContractVersionError):
                Multicall3(ctx).aggregate([self._observation()])
            batch = Multicall3(ctx).batch()
            batch.add(self._observation())
            with pytest.raises(UnsupportedContractVersionError):
                batch.run()

    def test_optional_reads_are_none(self):
        for ctx in self._contexts():
            assert Multicall3(ctx).try_aggregate([self._observation()]) == [None]
            batch = Multicall3(ctx).batch()
            read = batch.try_add(self._observation())
            mapped = batch.try_add(self._observation().map(lambda o: o))
            batch.run()
            assert (read.value, mapped.value) == (None, None)

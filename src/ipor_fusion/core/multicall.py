from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Generic, TypeVar

from eth_abi.exceptions import DecodingError
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper, _AbiDecodeFailure
from ipor_fusion.errors import EmptyCallResultError

T = TypeVar("T")

# Deterministic deployment, same address on every chain that has Multicall3.
MULTICALL3_ADDRESS: ChecksumAddress = Web3.to_checksum_address(
    "0xcA11bde05977b3631167028862bE2a173976CA11"
)

# Bounds calldata, return size and the gas the node spends on one eth_call.
DEFAULT_BATCH_SIZE = 100

# A sub-call that reverts, returns nothing or returns undecodable data.
_CALL_FAILURES = (ContractLogicError, EmptyCallResultError, DecodingError)


class _Multicall3Contract(ContractWrapper):
    """The Multicall3 contract as an offline Call builder. `Multicall3` runs it."""

    def aggregate3(
        self, calls: Sequence[Call[object]]
    ) -> Call[list[tuple[bool, bytes]]]:
        """Raw `aggregate3((address,bool,bytes)[])` with `allowFailure` set on
        every sub-call; decodes to one ``(success, return_data)`` per call."""
        return self._view(
            "aggregate3((address,bool,bytes)[])",
            [(call.to, True, call.data) for call in calls],
            output_types=["(bool,bytes)[]"],
            decoder=lambda results: [(ok, bytes(data)) for ok, data in results],
        )


class Multicall3:
    """Batch many view `Call`s into one `eth_call` via Multicall3 `aggregate3`.

    An executor that owns its context: every batch, every re-run of a failed
    sub-call and the per-call fallback run on ``ctx`` at its default block
    (so a context from `Web3Context.at_block` keeps every read at that block),
    in one round trip per `batch_size` calls. Each sub-call runs with
    `allowFailure`, so one failing read never takes the batch down:

    - `try_aggregate` maps a failed or undecodable sub-call to ``None``.
    - `aggregate` re-runs each failed sub-call on its own, so it raises exactly
      what `Call.call(ctx)` would (revert reason, `EmptyCallResultError`).
    - `batch` collects reads of different types, each keeping its own:
      ``batch.add`` fails as `aggregate` does, ``batch.try_add`` as
      `try_aggregate` does, and one ``batch.run()`` executes them all.

    Where Multicall3 is not deployed (a chain without it, or a block before
    its deployment) both fall back to one `eth_call` per sub-call.
    """

    def __init__(
        self,
        ctx: Web3Context,
        *,
        address: ChecksumAddress = MULTICALL3_ADDRESS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ):
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self._ctx = ctx
        self._contract = _Multicall3Contract(address)
        self._batch_size = batch_size

    @property
    def address(self) -> ChecksumAddress:
        return self._contract.address

    def aggregate3(
        self, calls: Sequence[Call[object]]
    ) -> Call[list[tuple[bool, bytes]]]:
        """The raw `aggregate3` Call, unexecuted: run it with `.call(ctx)`."""
        return self._contract.aggregate3(calls)

    def batch(self) -> Multicall3Batch:
        """An empty typed batch that runs on this executor's context."""
        return Multicall3Batch(self)

    def aggregate(self, calls: Sequence[Call[T]]) -> list[T]:
        """Execute all `calls`; raise as `Call.call(ctx)` would on any failure."""
        raw = self._fetch(calls)
        if raw is None:
            return [call.call(self._ctx) for call in calls]
        return [
            call.decode(data) if ok and data else call.call(self._ctx)
            for call, (ok, data) in zip(calls, raw, strict=True)
        ]

    def try_aggregate(self, calls: Sequence[Call[T]]) -> list[T | None]:
        """Execute all `calls`; ``None`` for each that reverts, returns no data
        or returns data its `Call` cannot decode."""
        raw = self._fetch(calls)
        if raw is None:
            return [_try_call(call, self._ctx) for call in calls]
        return [
            _try_decode(call, ok, data)
            for call, (ok, data) in zip(calls, raw, strict=True)
        ]

    def _fetch(self, calls: Sequence[Call[object]]) -> list[tuple[bool, bytes]] | None:
        """Raw per-call results in order, or ``None`` without Multicall3."""
        results: list[tuple[bool, bytes]] = []
        for start in range(0, len(calls), self._batch_size):
            chunk = calls[start : start + self._batch_size]
            try:
                results.extend(self._contract.aggregate3(chunk).call(self._ctx))
            except EmptyCallResultError:
                return None
        return results


class BatchValue(Generic[T]):
    """One read of a `Multicall3Batch`: its decoded ``value`` once the batch ran."""

    __slots__ = ("_ready", "_value")

    def __init__(self) -> None:
        self._ready = False

    @property
    def value(self) -> T:
        if not self._ready:
            raise RuntimeError(
                "BatchValue read before its batch ran: call Multicall3Batch.run() first"
            )
        return self._value

    def _resolve(self, value: T) -> None:
        self._value = value
        self._ready = True


# Settles one read from its raw `aggregate3` result, ``None`` without Multicall3.
_Settle = Callable[[tuple[bool, bytes] | None], None]


class Multicall3Batch:
    """Reads of any mix of types, executed together by one `run()`.

    ``add(call)`` returns a `BatchValue[T]` for a `Call[T]` and fails the run as
    `Multicall3.aggregate` would; ``try_add(call)`` returns a
    `BatchValue[T | None]`, ``None`` when the read fails, as
    `Multicall3.try_aggregate`. Failure handling is chosen per read, so each
    value's type says whether it can be ``None``. ``run()`` costs what
    `aggregate` costs for the same calls: one ``eth_call`` per `batch_size`
    reads, or one per read without Multicall3. A batch runs once.

        batch = Multicall3(ctx).batch()
        assets = batch.add(vault.total_assets())      # BatchValue[Amount]
        name = batch.try_add(vault.name())            # BatchValue[str | None]
        batch.run()
        assets.value, name.value
    """

    def __init__(self, multicall: Multicall3) -> None:
        self._multicall = multicall
        self._calls: list[Call[object]] = []
        self._settles: list[_Settle] = []
        self._ran = False

    def __len__(self) -> int:
        return len(self._calls)

    def add(self, call: Call[T]) -> BatchValue[T]:
        """Queue a read whose failure raises from `run()` exactly as
        `Call.call(ctx)` would."""
        handle = BatchValue[T]()
        ctx = self._multicall._ctx

        def settle(raw: tuple[bool, bytes] | None) -> None:
            if raw is not None and raw[0] and raw[1]:
                handle._resolve(call.decode(raw[1]))
            else:
                handle._resolve(call.call(ctx))

        return self._queue(call, settle, handle)

    def try_add(self, call: Call[T]) -> BatchValue[T | None]:
        """Queue a read that is ``None`` when it reverts, returns no data or
        returns data its `Call` cannot decode."""
        handle = BatchValue[T | None]()
        ctx = self._multicall._ctx

        def settle(raw: tuple[bool, bytes] | None) -> None:
            if raw is None:
                handle._resolve(_try_call(call, ctx))
            else:
                handle._resolve(_try_decode(call, *raw))

        return self._queue(call, settle, handle)

    def _queue(
        self, call: Call[object], settle: _Settle, handle: BatchValue[T]
    ) -> BatchValue[T]:
        if self._ran:
            raise RuntimeError("Multicall3Batch already ran: start a new batch")
        self._calls.append(call)
        self._settles.append(settle)
        return handle

    def run(self) -> None:
        """Execute every queued read and resolve its `BatchValue`; raises the
        first failing ``add`` read's error, in queue order."""
        if self._ran:
            raise RuntimeError("Multicall3Batch already ran: start a new batch")
        self._ran = True
        raw = self._multicall._fetch(self._calls)
        if raw is None:
            for settle in self._settles:
                settle(None)
            return
        for settle, result in zip(self._settles, raw, strict=True):
            settle(result)


def _translated_decode_failure(exc: Exception) -> bool:
    """Whether ``exc`` is a `Call.decode_error` translation of an undecodable
    answer: it names the failure for required reads, while an optional read of
    the same answer is ``None``."""
    return isinstance(exc.__cause__, _AbiDecodeFailure)


def _try_decode(call: Call[T], ok: bool, data: bytes) -> T | None:
    if not ok or not data:
        return None
    try:
        return call.decode(data)
    except DecodingError:
        return None
    except Exception as exc:
        if _translated_decode_failure(exc):
            return None
        raise


def _try_call(call: Call[T], ctx: Web3Context) -> T | None:
    try:
        return call.call(ctx)
    except _CALL_FAILURES:
        return None
    except Exception as exc:
        if _translated_decode_failure(exc):
            return None
        raise

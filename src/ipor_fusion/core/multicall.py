from __future__ import annotations

from collections.abc import Sequence
from typing import TypeVar, cast

from eth_abi.exceptions import DecodingError
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.errors import EmptyCallResultError

T = TypeVar("T")

# Deterministic deployment, same address on every chain that has Multicall3.
MULTICALL3_ADDRESS: ChecksumAddress = Web3.to_checksum_address(
    "0xcA11bde05977b3631167028862bE2a173976CA11"
)

# Bounds calldata, return size and the gas the node spends on one eth_call.
DEFAULT_BATCH_SIZE = 100

# A sub-call that reverts, returns nothing or returns undecodable data.
_CALL_FAILURES = (ContractLogicError, DecodingError)


class Multicall3(ContractWrapper):
    """Batch many view `Call`s into one `eth_call` via Multicall3 `aggregate3`.

    All sub-calls execute at the same block (`ctx.default_block`), in one round
    trip per `batch_size` calls. Each sub-call runs with `allowFailure`, so one
    failing read never takes the batch down:

    - `try_aggregate` maps a failed or undecodable sub-call to ``None``.
    - `aggregate` re-runs each failed sub-call on its own, so it raises exactly
      what `Call.call()` would (revert reason, `EmptyCallResultError`).

    Where Multicall3 is not deployed (a chain without it, or a block before
    its deployment) both fall back to one `eth_call` per sub-call.
    """

    def __init__(
        self,
        ctx: Web3Context,
        address: ChecksumAddress = MULTICALL3_ADDRESS,
        batch_size: int = DEFAULT_BATCH_SIZE,
    ):
        super().__init__(ctx, address)
        if batch_size < 1:
            raise ValueError("batch_size must be >= 1")
        self._batch_size = batch_size

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

    def aggregate(self, calls: Sequence[Call[T]]) -> list[T]:
        """Execute all `calls`; raise as `Call.call()` would on any failure."""
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

    def _fetch(self, calls: Sequence[Call[T]]) -> list[tuple[bool, bytes]] | None:
        """Raw per-call results in order, or ``None`` without Multicall3."""
        results: list[tuple[bool, bytes]] = []
        for start in range(0, len(calls), self._batch_size):
            chunk = cast(
                Sequence[Call[object]], calls[start : start + self._batch_size]
            )
            try:
                results.extend(self.aggregate3(chunk).call())
            except EmptyCallResultError:
                return None
        return results


def _try_decode(call: Call[T], ok: bool, data: bytes) -> T | None:
    if not ok or not data:
        return None
    try:
        return call.decode(data)
    except DecodingError:
        return None


def _try_call(call: Call[T], ctx: Web3Context) -> T | None:
    try:
        return call.call(ctx)
    except _CALL_FAILURES:
        return None

"""Test doubles for Multicall3-batched reads.

`multicall_aware(handler)` builds a `Web3Context.call` side effect: a plain
eth_call goes straight to ``handler(to, data)``; an `aggregate3` call to
Multicall3 is unpacked, each sub-call dispatched to the same handler, and the
results re-encoded the way the contract returns them. A handler raising
`ContractLogicError` models a reverting sub-call (``success = false``).

`SequentialMulticall` stands in for `Multicall3` where a test mocks contract
wrappers (MagicMock `Call`s with `.call.return_value`): it runs each call on
its own, exactly like the production fallback on chains without Multicall3.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from typing import Any

from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion.core.multicall import MULTICALL3_ADDRESS

AGGREGATE3_SELECTOR = function_signature_to_4byte_selector(
    "aggregate3((address,bool,bytes)[])"
)

Handler = Callable[[str, bytes], bytes]


def is_aggregate3(to: str, data: bytes) -> bool:
    return to == MULTICALL3_ADDRESS and bytes(data[:4]) == AGGREGATE3_SELECTOR


def decode_aggregate3(data: bytes) -> list[tuple[str, bytes]]:
    """Sub-calls ``(target, calldata)`` packed into an aggregate3 payload."""
    (calls,) = decode(["(address,bool,bytes)[]"], bytes(data[4:]))
    return [(Web3.to_checksum_address(target), bytes(cd)) for target, _, cd in calls]


def encode_aggregate3_result(results: Sequence[tuple[bool, bytes]]) -> bytes:
    return encode(["(bool,bytes)[]"], [list(results)])


def multicall_aware(handler: Handler) -> Callable[..., HexBytes]:
    def call(to: str, data: bytes, block: Any = None) -> HexBytes:
        if not is_aggregate3(to, data):
            return HexBytes(handler(to, bytes(data)))
        results: list[tuple[bool, bytes]] = []
        for target, calldata in decode_aggregate3(data):
            try:
                results.append((True, handler(target, calldata)))
            except ContractLogicError:
                results.append((False, b""))
        return HexBytes(encode_aggregate3_result(results))

    return call


def sequenced(responses: Sequence[bytes | BaseException]) -> Callable[..., HexBytes]:
    """`multicall_aware` over an ordered queue: every eth_call, batched or not,
    takes the next response; an exception in the queue is raised in its place."""
    queue = list(responses)

    def handler(_to: str, _data: bytes) -> bytes:
        item = queue.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item

    return multicall_aware(handler)


class SequentialMulticall:
    """`Multicall3` stand-in that executes every call individually."""

    def __init__(self, ctx: Any, *args: Any, **kwargs: Any):
        self._ctx = ctx

    def aggregate(self, calls: Sequence[Any]) -> list[Any]:
        return [call.call() for call in calls]

    def try_aggregate(self, calls: Sequence[Any]) -> list[Any]:
        results = []
        for call in calls:
            try:
                results.append(call.call())
            except (ContractLogicError, DecodingError):
                results.append(None)
        return results

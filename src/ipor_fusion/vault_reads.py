"""Batched-read helpers shared by the vault fetchers: one Multicall3 round trip
with required and optional reads, and the error envelopes optional data degrades
through."""

from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from typing import Any, TypeVar

import requests
from web3.exceptions import ContractLogicError, TimeExhausted, Web3RPCError

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.errors import EmptyCallResultError, LogScanError

T = TypeVar("T")

_logger = logging.getLogger(__name__)


# Failure modes of the event scans behind optional data (role holders, pending
# withdraw requests): a log scan the provider would not let complete
# (LogScanError), plus JSON-RPC and transport errors from the reads that follow.
_ROLE_SCAN_ERRORS = (
    LogScanError,
    ContractLogicError,
    Web3RPCError,
    TimeExhausted,
    requests.RequestException,
)


def _scan_or_none(func: Callable[[], T]) -> T | None:
    """An event scan behind optional data; None (never a partial result) when
    it cannot complete."""
    try:
        return func()
    except _ROLE_SCAN_ERRORS as exc:
        _logger.debug("scan unavailable: %s: %s", type(exc).__name__, exc)
        return None


def _safe_call(func: Callable[[], T]) -> T | None:
    try:
        return func()
    except (ContractLogicError, Web3RPCError, TimeExhausted) as exc:
        _logger.debug("_safe_call suppressed %s: %s", type(exc).__name__, exc)
        return None


def _unless_unimplemented(ctx: Web3Context, value: T | None, call: Call[T]) -> T | None:
    """An optional batch read, re-run when it failed: ``None`` only when the
    contract returned no data (it predates the getter); a revert or RPC error
    raises exactly what `Call.call(ctx)` would, so real failures stay loud."""
    if value is not None:
        return value
    try:
        return call.call(ctx)
    except EmptyCallResultError:
        return None


def _read_batch(
    ctx: Web3Context,
    required: Sequence[Call[Any]],
    optional: Sequence[Call[Any]] = (),
) -> tuple[list[Any], list[Any]]:
    """Read `required` + `optional` in one Multicall3 round trip.

    A failing optional read is ``None``. A failing required read is re-run on
    its own on ``ctx``, so it raises exactly what `Call.call(ctx)` would.
    """
    results = Multicall3(ctx).try_aggregate([*required, *optional])
    head = [
        value if value is not None else call.call(ctx)
        for value, call in zip(results, required, strict=False)
    ]
    return head, results[len(required) :]

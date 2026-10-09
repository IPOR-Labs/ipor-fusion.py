"""Adaptive `eth_getLogs` over a contract's whole history.

Providers cap `eth_getLogs` in different ways: by block range (10,000 blocks
on some chains), by result count, by response size, or by a server-side query
timeout, and some answer a too-wide query with HTTP 400/413 rather than a
JSON-RPC error. `get_logs_adaptive` pages the range in chunks that shrink on
any rejection, jumps straight to the provider's suggested range when the error
names one, and either returns every log in the range or raises
`LogScanError`. It never returns a partial result.
"""

from __future__ import annotations

import logging
import re
import threading
import time
from concurrent.futures import ThreadPoolExecutor

import requests
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.exceptions import Web3Exception
from web3.types import BlockIdentifier, FilterParams, LogReceipt

from ipor_fusion.errors import LogScanError

_logger = logging.getLogger(__name__)

DEFAULT_LOG_SCAN_TIMEOUT_S = 90.0
DEFAULT_LOG_SCAN_WORKERS = 16
# eth_getLogs requests one scan may issue. On a chain whose providers cap the
# range at 10,000 blocks a year-old contract already needs thousands; past this
# budget the scan raises LogScanError up front instead of issuing them.
DEFAULT_LOG_SCAN_MAX_REQUESTS = 1_000
# Largest page once paging starts: a proxy that splits a wide range itself
# (or a slow node) can otherwise turn one request into minutes.
MAX_LOG_PAGE_BLOCKS = 1_000_000

# A rate limit or a dropped connection is retried on the same range before
# the range is treated as rejected and split.
_TRANSIENT_RETRIES = 3
_TRANSIENT_BACKOFF_S = 0.5

_PROVIDER_ERRORS = (Web3Exception, requests.RequestException, OSError)

# "this block range should work: [0x0, 0x270f]" (Alchemy, Infura)
_HEX_RANGE = re.compile(r"\[\s*(0x[0-9a-f]+)\s*,\s*(0x[0-9a-f]+)\s*\]", re.I)
# "retry with the range 3600741-3620740"
_DECIMAL_RANGE = re.compile(r"range\s+(\d+)\s*-\s*(\d+)", re.I)
# "max block range 10000", "up to a 10000 block range", "maxAllowedRange': 30000"
_RANGE_CAP = re.compile(
    r"(?:max(?:imum)?\s+block\s+range|up\s+to\s+an?|maxAllowedRange\W*)\s*(\d+)\b",
    re.I,
)
_TRANSIENT_TEXT = re.compile(
    r"rate.?limit|too many requests|compute units|temporar|try again later", re.I
)
_TRANSIENT_STATUS = frozenset({429, 502, 503})
_URL = re.compile(r"\w+://\S+")

_creation_blocks: dict[tuple[int, str], int] = {}
_creation_locks: dict[tuple[int, str], threading.Lock] = {}
_creation_locks_guard = threading.Lock()


def find_creation_block(web3: Web3, address: ChecksumAddress, block: int) -> int | None:
    """First block at which ``address`` has code, by binary search over
    `eth_getCode` (about log2(block) calls); None when it has no code at
    ``block``. Needs a provider that serves historical state."""
    if not _has_code(web3, address, block):
        return None
    low, high = 0, block
    while low < high:
        middle = (low + high) // 2
        if _has_code(web3, address, middle):
            high = middle
        else:
            low = middle + 1
    return low


def get_logs_adaptive(
    web3: Web3,
    address: ChecksumAddress,
    topics: list[str | list[str]],
    from_block: BlockIdentifier | None = None,
    to_block: BlockIdentifier = "latest",
    *,
    chain_id: int | None = None,
    chunk_hint: int | None = None,
    timeout_s: float = DEFAULT_LOG_SCAN_TIMEOUT_S,
    max_workers: int = DEFAULT_LOG_SCAN_WORKERS,
    max_requests: int | None = DEFAULT_LOG_SCAN_MAX_REQUESTS,
) -> list[LogReceipt]:
    """Every log ``address`` emitted with ``topics`` in the block range, in
    block order, or `LogScanError`.

    ``to_block`` is resolved to a block number first, so every request names
    a concrete range. ``from_block=None`` scans the contract's whole history:
    one request over ``[0, to_block]`` first (enough wherever the provider
    serves wide queries), then, if the provider rejects it, pages from the
    contract's creation block (`find_creation_block`, cached per ``chain_id``
    and address). ``chunk_hint`` is the first page size to try; a provider
    known to cap the range should get its cap here, which skips the
    whole-range attempt. Pages hold at most `MAX_LOG_PAGE_BLOCKS` blocks; a
    rejected page shrinks to the size the provider suggests, or half; once one
    page succeeds, the rest of the range is fetched in pages of that size on
    ``max_workers`` threads. ``timeout_s`` bounds the scan's wall clock and
    ``max_requests`` (None: unbounded) its request count.
    """
    scan = _Scan(web3, address, topics, timeout_s, max_requests)
    end = _block_number(web3, to_block)
    size = chunk_hint
    if from_block is None and size is None:
        try:
            return scan.fetch(0, end)
        except _Rejected as rejected:
            size = rejected.size
    if from_block is None:
        start = _creation_block_or_zero(web3, address, end, chain_id)
    else:
        start = _block_number(web3, from_block)
    if start > end:
        return []
    size = min(size or end - start + 1, MAX_LOG_PAGE_BLOCKS)
    return scan.run(start, end, size, max_workers)


class _Rejected(Exception):
    """The provider rejected one page; ``size`` is its suggested page size."""

    def __init__(self, cause: BaseException, size: int | None):
        super().__init__(_describe(cause))
        self.size = size


class _Scan:
    def __init__(
        self,
        web3: Web3,
        address: ChecksumAddress,
        topics: list[str | list[str]],
        timeout_s: float,
        max_requests: int | None,
    ):
        self._web3 = web3
        self._address = address
        self._topics = topics
        self._timeout_s = timeout_s
        self._deadline = time.monotonic() + timeout_s
        self._stopped = threading.Event()
        self._error: LogScanError | None = None
        self._error_lock = threading.Lock()
        self._max_requests = max_requests
        self._requests = 0

    def run(
        self, start: int, end: int, size: int, max_workers: int
    ) -> list[LogReceipt]:
        # Probe from `start` until one page succeeds: that page size is one the
        # provider accepts, so the rest is split into pages of it up front.
        head, last, size = self._first_page(start, end, size)
        windows = [
            (low, min(low + size - 1, end)) for low in range(last + 1, end + 1, size)
        ]
        if not windows:
            return head
        if self._max_requests is not None:
            needed = self._requests + len(windows)
            if needed > self._max_requests:
                raise self._fail(
                    f"needs at least {needed} requests in pages of {size} blocks, "
                    f"over the budget of {self._max_requests}",
                    last + 1,
                    end,
                )
        with ThreadPoolExecutor(min(max_workers, len(windows))) as pool:
            futures = [
                pool.submit(self._pages, low, high, size) for low, high in windows
            ]
            try:
                pages = [future.result() for future in futures]
            except Exception:
                self._stopped.set()
                if self._error is not None:
                    raise self._error from None
                raise
        return head + [log for page in pages for log in page]

    def fetch(self, low: int, high: int) -> list[LogReceipt]:
        """One `eth_getLogs` over ``[low, high]``; transient failures are
        retried here, anything else raises `_Rejected`."""
        attempt = 0
        while True:
            self._check(low, high)
            params: FilterParams = {
                "address": self._address,
                "topics": self._topics,  # type: ignore[typeddict-item]
                "fromBlock": low,
                "toBlock": high,
            }
            try:
                return list(self._web3.eth.get_logs(params))
            except _PROVIDER_ERRORS as exc:
                if attempt < _TRANSIENT_RETRIES and _is_transient(exc):
                    time.sleep(_TRANSIENT_BACKOFF_S * 2**attempt)
                    attempt += 1
                    continue
                raise _Rejected(exc, _suggested_size(exc)) from exc

    def _pages(self, low: int, high: int, size: int) -> list[LogReceipt]:
        logs: list[LogReceipt] = []
        while low <= high:
            page, last, size = self._first_page(low, high, size)
            logs.extend(page)
            low = last + 1
        return logs

    def _first_page(
        self, low: int, high: int, size: int
    ) -> tuple[list[LogReceipt], int, int]:
        """The first page from ``low`` the provider accepts, shrinking ``size``
        on each rejection: ``(logs, last block, accepted size)``."""
        while True:
            last = min(low + size - 1, high)
            try:
                return self.fetch(low, last), last, size
            except _Rejected as rejected:
                span = last - low + 1
                if span == 1:
                    raise self._fail(
                        f"provider rejected block {low} on its own: {rejected}",
                        low,
                        high,
                    ) from rejected.__cause__
                size = (
                    rejected.size
                    if rejected.size and rejected.size < span
                    else span // 2
                )

    def _check(self, low: int, high: int) -> None:
        if self._stopped.is_set():
            raise LogScanError(
                f"eth_getLogs scan of {self._address} aborted",
                address=self._address,
                from_block=low,
                to_block=high,
            )
        if time.monotonic() > self._deadline:
            raise self._fail(
                f"timed out after {self._timeout_s:g}s with blocks {low}-{high} "
                "still unscanned",
                low,
                high,
            )
        with self._error_lock:
            self._requests += 1
            over = (
                self._max_requests is not None and self._requests > self._max_requests
            )
        if over:
            raise self._fail(
                f"request budget of {self._max_requests} spent with blocks "
                f"{low}-{high} still unscanned",
                low,
                high,
            )

    def _fail(self, reason: str, low: int, high: int) -> LogScanError:
        error = LogScanError(
            f"eth_getLogs scan of {self._address} incomplete: {reason}",
            address=self._address,
            from_block=low,
            to_block=high,
        )
        with self._error_lock:
            if self._error is None:
                self._error = error
        self._stopped.set()
        return error


def _creation_block_or_zero(
    web3: Web3, address: ChecksumAddress, block: int, chain_id: int | None
) -> int:
    if chain_id is None:
        return _search_creation_block(web3, address, block)
    key = (chain_id, address.lower())
    with _creation_locks_guard:
        lock = _creation_locks.setdefault(key, threading.Lock())
    with lock:
        if key not in _creation_blocks:
            found = _search_creation_block(web3, address, block)
            if found == 0:
                return 0
            _creation_blocks[key] = found
        return _creation_blocks[key]


def _search_creation_block(web3: Web3, address: ChecksumAddress, block: int) -> int:
    # Without historical state (a pruned node) or code at the head, the scan
    # starts at genesis: slower, never wrong.
    try:
        return find_creation_block(web3, address, block) or 0
    except _PROVIDER_ERRORS as exc:
        _logger.debug("creation block of %s unavailable: %s", address, _describe(exc))
        return 0


def _has_code(web3: Web3, address: ChecksumAddress, block: int) -> bool:
    return len(web3.eth.get_code(address, block_identifier=block)) > 0


def _block_number(web3: Web3, block: BlockIdentifier) -> int:
    if isinstance(block, int):
        return block
    if block == "latest":
        return web3.eth.block_number
    return web3.eth.get_block(block)["number"]


def _status_code(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    return getattr(response, "status_code", None)


def _describe(exc: BaseException) -> str:
    """Error text with the response body, provider URLs (they embed API keys)
    removed, truncated."""
    text = f"{type(exc).__name__}: {exc}"
    body = getattr(getattr(exc, "response", None), "text", None)
    if isinstance(body, str) and body:
        text = f"{text} {body}"
    return _URL.sub("<provider>", text)[:400]


def _is_transient(exc: BaseException) -> bool:
    if isinstance(exc, requests.ConnectionError) and not isinstance(
        exc, requests.Timeout
    ):
        return True
    if _status_code(exc) in _TRANSIENT_STATUS:
        return True
    return _suggested_size(exc) is None and bool(_TRANSIENT_TEXT.search(_describe(exc)))


def _suggested_size(exc: BaseException) -> int | None:
    """Page size the provider's error suggests, if it names one."""
    text = _describe(exc)
    for pattern, base in ((_HEX_RANGE, 16), (_DECIMAL_RANGE, 10)):
        match = pattern.search(text)
        if match:
            low, high = (int(group, base) for group in match.groups())
            if high >= low:
                return high - low + 1
    match = _RANGE_CAP.search(text)
    return int(match.group(1)) if match else None

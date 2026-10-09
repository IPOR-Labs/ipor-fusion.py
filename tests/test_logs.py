"""Adaptive eth_getLogs paging (`core.logs`) against a fake provider."""

import threading
from collections.abc import Callable
from typing import Any
from unittest.mock import MagicMock, patch

import pytest
import requests
from web3 import Web3
from web3.exceptions import Web3RPCError

from ipor_fusion.chains import GET_LOGS_RANGE_HINTS
from ipor_fusion.core import logs as logs_mod
from ipor_fusion.core.context import _RETRY_EXCEPT_GET_LOGS, Web3Context
from ipor_fusion.core.logs import (
    DEFAULT_LOG_SCAN_MAX_REQUESTS,
    find_creation_block,
    get_logs_adaptive,
)
from ipor_fusion.errors import IporFusionError, LogScanError

ADDRESS = Web3.to_checksum_address("0x" + "11" * 20)
TOPICS: list[str | list[str]] = ["0x" + "aa" * 32]
HEAD = 1_000
CREATED = 600
# Blocks the fake contract emitted a log in.
EVENT_BLOCKS = [600, 601, 650, 777, 899, 900, 999, 1_000]

Rejection = Callable[[int, int], Exception | None]


class _FakeEth:
    """`web3.eth` stand-in: code from CREATED on, one log per EVENT_BLOCKS
    entry, and a pluggable rejection rule per requested range."""

    def __init__(self, reject: Rejection | None = None, created: int = CREATED):
        self.block_number = HEAD
        self.created = created
        self.reject = reject or (lambda _low, _high: None)
        self.ranges: list[tuple[int, int]] = []
        self.code_reads = 0
        self._lock = threading.Lock()

    def get_logs(self, params: dict[str, Any]) -> list[dict[str, Any]]:
        low, high = params["fromBlock"], params["toBlock"]
        if high == "latest":
            high = self.block_number
        assert params["address"] == ADDRESS
        assert params["topics"] == TOPICS
        with self._lock:
            self.ranges.append((low, high))
        error = self.reject(low, high)
        if error is not None:
            raise error
        return [{"blockNumber": b} for b in EVENT_BLOCKS if low <= b <= high]

    def get_code(self, address: str, block_identifier: int) -> bytes:
        self.code_reads += 1
        return b"\x60\x80" if block_identifier >= self.created else b""

    def get_block(self, block: Any) -> dict[str, int]:
        return {"number": HEAD - 5}


def _web3(eth: _FakeEth) -> Any:
    web3 = MagicMock()
    web3.eth = eth
    return web3


def _cap(size: int, message: str) -> Rejection:
    def reject(low: int, high: int) -> Exception | None:
        return Web3RPCError(message) if high - low + 1 > size else None

    return reject


def _blocks(result: list[Any]) -> list[int]:
    return [log["blockNumber"] for log in result]


def _assert_tiles(ranges: list[tuple[int, int]], start: int, end: int) -> None:
    """The successful ranges cover [start, end] exactly once."""
    covered = sorted(ranges)
    assert covered[0][0] == start
    assert covered[-1][1] == end
    for (_, prev_high), (low, _) in zip(covered, covered[1:], strict=False):
        assert low == prev_high + 1


@pytest.fixture(autouse=True)
def _clear_creation_cache():
    logs_mod._creation_blocks.clear()
    yield
    logs_mod._creation_blocks.clear()


@pytest.fixture(autouse=True)
def _no_sleep():
    with patch.object(logs_mod.time, "sleep"):
        yield


class TestWholeRange:
    def test_one_request_when_the_provider_serves_the_whole_range(self):
        eth = _FakeEth()

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS)

        assert _blocks(result) == EVENT_BLOCKS
        assert eth.ranges == [(0, HEAD)]
        assert eth.code_reads == 0

    def test_explicit_range_is_used_as_given(self):
        eth = _FakeEth()

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, 650, 900)

        assert _blocks(result) == [650, 777, 899, 900]
        assert eth.ranges == [(650, 900)]
        assert eth.code_reads == 0

    def test_block_tag_resolves_through_get_block(self):
        eth = _FakeEth()

        get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, 0, "finalized")

        assert eth.ranges == [(0, HEAD - 5)]

    def test_cached_creation_after_the_range_is_empty(self):
        eth = _FakeEth()
        web3 = _web3(eth)
        get_logs_adaptive(web3, ADDRESS, TOPICS, chain_id=1, chunk_hint=1_000)
        calls = len(eth.ranges)

        assert (
            get_logs_adaptive(
                web3, ADDRESS, TOPICS, None, 500, chain_id=1, chunk_hint=10
            )
            == []
        )
        assert len(eth.ranges) == calls

    def test_empty_explicit_range(self):
        eth = _FakeEth()

        assert get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, 700, 600) == []
        assert eth.ranges == []


class TestRejectedRanges:
    def test_range_cap_pages_from_creation(self):
        eth = _FakeEth(reject=_cap(100, "query exceeds max block range 100"))

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, max_workers=4)

        assert _blocks(result) == EVENT_BLOCKS
        accepted = [r for r in eth.ranges if r[1] - r[0] + 1 <= 100]
        assert all(low >= CREATED for low, _ in accepted)
        _assert_tiles(accepted, CREATED, HEAD)
        # whole range, then the cap read off the message: no halving steps
        assert len(eth.ranges) - len(accepted) == 1

    def test_alchemy_suggested_hex_range(self):
        message = (
            "You can make eth_getLogs requests with up to a 2K block range. "
            "Based on your parameters, this block range should work: [0x258, 0x2bb]"
        )
        eth = _FakeEth(reject=_cap(100, message))

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS)

        assert _blocks(result) == EVENT_BLOCKS
        assert max(high - low + 1 for low, high in eth.ranges[1:]) == 100

    def test_suggested_decimal_range(self):
        message = "query exceeds max results 20000, retry with the range 600-649"
        eth = _FakeEth(reject=_cap(50, message))

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS)

        assert _blocks(result) == EVENT_BLOCKS
        assert max(high - low + 1 for low, high in eth.ranges[1:]) == 50

    def test_proxy_reported_range_cap(self):
        message = str(
            {
                "code": -32012,
                "message": "getLogs request exceeded max allowed range",
                "data": {"details": {"requestRange": 401, "maxAllowedRange": 40}},
            }
        )
        eth = _FakeEth(reject=_cap(40, message))

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, 600, 1_000)

        assert _blocks(result) == EVENT_BLOCKS
        assert eth.ranges[1] == (600, 639)

    def test_unparsed_rejection_halves(self):
        eth = _FakeEth(reject=_cap(30, "Response is too big"))

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS)

        assert _blocks(result) == EVENT_BLOCKS
        sizes = [high - low + 1 for low, high in eth.ranges]
        assert sizes[:3] == [HEAD + 1, HEAD - CREATED + 1, (HEAD - CREATED + 1) // 2]

    def test_suggestion_not_smaller_than_the_page_halves_instead(self):
        eth = _FakeEth(reject=_cap(100, "max block range 5000"))

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, 600, 1_000)

        assert _blocks(result) == EVENT_BLOCKS
        assert eth.ranges[1] == (600, 799)

    def test_http_error_body_is_parsed(self):
        def reject(low: int, high: int) -> Exception | None:
            if high - low + 1 <= 100:
                return None
            response = requests.Response()
            response.status_code = 400
            response._content = (
                b'{"error":{"code":-32600,"message":"You can make eth_getLogs '
                b'requests with up to a 100 block range."}}'
            )
            return requests.HTTPError("400 Client Error for url: x", response=response)

        eth = _FakeEth(reject=reject)

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS)

        assert _blocks(result) == EVENT_BLOCKS
        assert len([r for r in eth.ranges if r[1] - r[0] + 1 > 100]) == 1

    def test_read_timeout_splits_the_range(self):
        def reject(low: int, high: int) -> Exception | None:
            if high - low + 1 > 200:
                return requests.ReadTimeout("read timed out")
            return None

        eth = _FakeEth(reject=reject)

        assert _blocks(get_logs_adaptive(_web3(eth), ADDRESS, TOPICS)) == EVENT_BLOCKS


class TestChunkHint:
    def test_hint_skips_the_whole_range_attempt(self):
        eth = _FakeEth()

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, chunk_hint=100)

        assert _blocks(result) == EVENT_BLOCKS
        assert eth.ranges[0] == (CREATED, CREATED + 99)
        assert all(high - low + 1 <= 100 for low, high in eth.ranges)
        _assert_tiles(eth.ranges, CREATED, HEAD)


class TestCreationBlock:
    def test_binary_search(self):
        eth = _FakeEth(created=437)

        assert find_creation_block(_web3(eth), ADDRESS, HEAD) == 437

    def test_no_code_at_the_bound(self):
        assert find_creation_block(_web3(_FakeEth()), ADDRESS, 10) is None

    def test_cached_per_chain_and_address(self):
        eth = _FakeEth()
        web3 = _web3(eth)

        get_logs_adaptive(web3, ADDRESS, TOPICS, chain_id=1, chunk_hint=1_000)
        reads = eth.code_reads
        get_logs_adaptive(web3, ADDRESS, TOPICS, chain_id=1, chunk_hint=1_000)

        assert reads > 0
        assert eth.code_reads == reads

    def test_no_code_is_not_cached(self):
        eth = _FakeEth(created=HEAD + 1)
        web3 = _web3(eth)

        get_logs_adaptive(web3, ADDRESS, TOPICS, chain_id=1, chunk_hint=1_001)

        assert eth.ranges[0] == (0, HEAD)
        assert logs_mod._creation_blocks == {}

    def test_unavailable_history_scans_from_genesis(self):
        eth = _FakeEth()

        def no_history(address: str, block_identifier: int) -> bytes:
            raise Web3RPCError("missing trie node")

        eth.get_code = no_history  # type: ignore[method-assign]

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, chunk_hint=500)

        assert _blocks(result) == EVENT_BLOCKS
        assert eth.ranges[0] == (0, 499)


class TestNoPartialResults:
    def test_single_block_rejection_raises(self):
        def reject(low: int, high: int) -> Exception | None:
            return Web3RPCError("boom") if low <= 777 <= high else None

        eth = _FakeEth(reject=reject)

        with pytest.raises(LogScanError, match="rejected block 777") as info:
            get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, chunk_hint=50)

        assert info.value.address == ADDRESS
        assert info.value.from_block == 777

    def test_failure_on_the_first_page_raises(self):
        eth = _FakeEth(reject=lambda _low, _high: Web3RPCError("nope"))

        with pytest.raises(LogScanError):
            get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, 990, 1_000)

    def test_time_budget_raises(self):
        eth = _FakeEth()

        with pytest.raises(LogScanError, match="timed out after 0s"):
            get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, timeout_s=0)

    def test_request_budget_refuses_up_front(self):
        eth = _FakeEth(reject=_cap(10, "query exceeds max block range 10"))

        with pytest.raises(LogScanError, match="over the budget of 20"):
            get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, max_requests=20)

        # whole range, then the first page; the other 40 pages never sent
        assert len(eth.ranges) == 2

    def test_request_budget_counts_retries_and_splits(self):
        eth = _FakeEth(reject=_cap(10, "Response is too big"))

        with pytest.raises(LogScanError, match="budget of 5 spent"):
            get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, max_requests=5)

        assert len(eth.ranges) == 5

    def test_unbounded_budget(self):
        eth = _FakeEth(reject=_cap(1, "max block range 1"))

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, max_requests=None)

        assert _blocks(result) == EVENT_BLOCKS

    def test_pages_are_capped(self):
        eth = _FakeEth(reject=lambda _l, _h: None)
        eth.block_number = 3_500_000
        with patch.object(logs_mod, "MAX_LOG_PAGE_BLOCKS", 1_000_000):
            get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, 0, 3_500_000)

        assert max(high - low + 1 for low, high in eth.ranges) == 1_000_000

    def test_unexpected_errors_propagate(self):
        def reject(low: int, high: int) -> Exception | None:
            return KeyError("bug") if low > CREATED else None

        eth = _FakeEth(reject=reject)

        with pytest.raises(KeyError):
            get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, chunk_hint=100)

    def test_error_names_no_provider_url(self):
        provider_url = "https://rpc.example/v2/SECRETKEY"
        eth = _FakeEth(
            reject=lambda _l, _h: Web3RPCError(f"bad for url: {provider_url}")
        )

        with pytest.raises(LogScanError) as info:
            get_logs_adaptive(_web3(eth), ADDRESS, TOPICS, 999, 1_000)

        assert "SECRETKEY" not in str(info.value)

    def test_is_an_ipor_fusion_error(self):
        assert issubclass(LogScanError, IporFusionError)


class TestTransientErrors:
    def test_rate_limit_retries_the_same_range(self):
        attempts = {"n": 0}

        def reject(low: int, high: int) -> Exception | None:
            attempts["n"] += 1
            return Web3RPCError("429 Too Many Requests") if attempts["n"] <= 2 else None

        eth = _FakeEth(reject=reject)

        result = get_logs_adaptive(_web3(eth), ADDRESS, TOPICS)

        assert _blocks(result) == EVENT_BLOCKS
        assert eth.ranges == [(0, HEAD)] * 3

    def test_connection_drop_retries(self):
        attempts = {"n": 0}

        def reject(low: int, high: int) -> Exception | None:
            attempts["n"] += 1
            return requests.ConnectionError("reset") if attempts["n"] == 1 else None

        eth = _FakeEth(reject=reject)

        assert _blocks(get_logs_adaptive(_web3(eth), ADDRESS, TOPICS)) == EVENT_BLOCKS
        assert eth.ranges == [(0, HEAD)] * 2

    def test_http_503_retries(self):
        attempts = {"n": 0}

        def reject(low: int, high: int) -> Exception | None:
            attempts["n"] += 1
            if attempts["n"] > 1:
                return None
            response = requests.Response()
            response.status_code = 503
            return requests.HTTPError("503", response=response)

        eth = _FakeEth(reject=reject)

        assert _blocks(get_logs_adaptive(_web3(eth), ADDRESS, TOPICS)) == EVENT_BLOCKS


class TestWeb3Context:
    def test_get_logs_passes_chain_hint_and_budget(self):
        web3 = MagicMock()
        ctx = Web3Context(web3, chain_id=130, log_scan_timeout_s=12.0)  # type: ignore[arg-type]

        with patch(
            "ipor_fusion.core.context.get_logs_adaptive", return_value=[]
        ) as scan:
            ctx.get_logs(ADDRESS, TOPICS, to_block=900)

        scan.assert_called_once_with(
            web3,
            ADDRESS,
            TOPICS,
            None,
            900,
            chain_id=130,
            chunk_hint=GET_LOGS_RANGE_HINTS[130],
            timeout_s=12.0,
            max_requests=DEFAULT_LOG_SCAN_MAX_REQUESTS,
        )

    def test_no_hint_on_chains_serving_wide_ranges(self):
        ctx = Web3Context(MagicMock(), chain_id=1)  # type: ignore[arg-type]

        with patch(
            "ipor_fusion.core.context.get_logs_adaptive", return_value=[]
        ) as scan:
            ctx.get_logs(ADDRESS, TOPICS)

        assert scan.call_args.kwargs["chunk_hint"] is None

    def test_http_retries_skip_get_logs(self):
        assert "eth_getLogs" not in _RETRY_EXCEPT_GET_LOGS.method_allowlist
        assert "eth_call" in _RETRY_EXCEPT_GET_LOGS.method_allowlist

    def test_from_url_installs_the_retry_policy(self):
        with (
            patch.object(Web3, "HTTPProvider") as provider,
            patch("ipor_fusion.core.context.Web3") as web3_cls,
        ):
            web3_cls.HTTPProvider = provider
            web3_cls.return_value.eth.chain_id = 1
            ctx = Web3Context.from_url("https://rpc.example", log_scan_timeout_s=5)

        assert provider.call_args.kwargs["exception_retry_configuration"] is (
            _RETRY_EXCEPT_GET_LOGS
        )
        assert ctx.log_scan_timeout_s == 5

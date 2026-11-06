"""The test-suite RPC cache stores only reads at finalized blocks."""

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast

import _rpc_cache
import pytest
import requests
from _live import archive_context
from _rpc_cache import (
    CachingProvider,
    cache_dir,
    cache_root,
    clear_cache,
    clear_if_requested,
    endpoint_key,
    http_provider,
    pinned_block,
    with_cache,
)
from hexbytes import HexBytes
from web3 import HTTPProvider, Web3
from web3.providers import BaseProvider
from web3.types import RPCEndpoint, RPCResponse

FINALIZED = 100
CALL = [{"to": "0xabc"}, hex(FINALIZED)]
ERROR = object()
URL = "https://rpc.example/v2/key-1234"


class _Network(BaseProvider):
    """The provider `CachingProvider` wraps, counting what reaches it.

    `finalized` is the `finalized` lookup's answer, or a list of answers in
    order (the last repeats): an int is that block's number, `ERROR` an error
    response, anything else the literal result. `chain_id` is an int, `ERROR`
    or a literal result. A call whose first param is `"revert"` gets an error,
    one whose first param is a key of `RESULTS` gets that literal result.
    Wherever a result goes, an exception is raised and a `_Raw` is returned
    as it is, with no envelope.
    """

    RESULTS = {
        "null": None,
        "empty-hex": "0x",
        "empty-list": [],
        "timeout": requests.Timeout("read timed out"),
    }

    def __init__(
        self,
        *,
        finalized: Any = FINALIZED,
        chain_id: Any = 1,
        endpoint_uri: str | None = URL,
    ) -> None:
        super().__init__()
        self._finality = finalized if isinstance(finalized, list) else [finalized]
        self.chain_id = chain_id
        self.endpoint_uri = endpoint_uri
        self.calls: list[str] = []

    def is_connected(self, show_traceback: bool = False) -> bool:
        return show_traceback

    def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        self.calls.append(method)
        if method == "eth_chainId":
            chain_id = self.chain_id
            return _reply(hex(chain_id) if isinstance(chain_id, int) else chain_id)
        if method == "eth_getBlockByNumber" and params[0] == "finalized":
            answer = (
                self._finality.pop(0) if len(self._finality) > 1 else self._finality[0]
            )
            return _reply(
                {"number": hex(answer)} if isinstance(answer, int) else answer
            )
        if params and params[0] == "revert":
            return _reply(ERROR)
        if params and isinstance(params[0], str) and params[0] in self.RESULTS:
            return _reply(self.RESULTS[params[0]])
        return _reply(f"{method}:{params}")

    def make_batch_request(
        self, requests: list[tuple[RPCEndpoint, Any]]
    ) -> list[RPCResponse]:
        self.calls.append("<batch>")
        return [_reply(f"{method}:{params}") for method, params in requests]

    def count(self, method: str) -> int:
        return self.calls.count(method)


class _Raw:
    """A decoded answer that is not a JSON-RPC envelope (`null`, a string)."""

    def __init__(self, value: Any) -> None:
        self.value = value


#: Lookup failures beside error answers: the transport's, and answers that do
#: not decode or are not a JSON object.
LOOKUP_FAILURES = [
    pytest.param(requests.Timeout("lookup timed out"), id="timeout"),
    pytest.param(json.JSONDecodeError("Expecting value", "<html>", 0), id="not-json"),
    pytest.param(
        UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte"),
        id="not-utf-8",
    ),
    pytest.param(_Raw(None), id="not-an-object"),
]


def _reply(result: Any) -> RPCResponse:
    if isinstance(result, Exception):
        raise result
    if isinstance(result, _Raw):
        return result.value
    if result is ERROR:
        return {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "x"}}
    return {"jsonrpc": "2.0", "id": 1, "result": result}


def _entries(cache_dir: Path) -> list[Path]:
    """Every stored response under `cache_dir`, whatever its chain and endpoint."""
    return sorted(cache_dir.rglob("*.json"))


def _files(cache_dir: Path) -> list[Path]:
    return [p for p in cache_dir.rglob("*") if p.is_file()]


@pytest.fixture
def network() -> _Network:
    return _Network()


@pytest.fixture
def provider(network: _Network, tmp_path: Path) -> CachingProvider:
    return CachingProvider(network, cache_dir=tmp_path)


def _request(provider: CachingProvider, method: str, params: Any) -> RPCResponse:
    return provider.make_request(RPCEndpoint(method), params)


def _twice(provider: CachingProvider, method: str, params: Any) -> RPCResponse:
    first = _request(provider, method, params)
    assert _request(provider, method, params) == first
    return first


def test_read_at_finalized_block_is_served_from_disk(network, provider, tmp_path):
    _twice(provider, "eth_call", CALL)
    _twice(provider, "eth_getBlockByNumber", [hex(FINALIZED), False])

    assert network.count("eth_call") == 1
    assert network.count("eth_getBlockByNumber") == 2  # finality lookup + the read
    assert len(_entries(tmp_path)) == 2


def test_cache_survives_a_new_provider(network, provider, tmp_path):
    _request(provider, "eth_call", CALL)
    _request(CachingProvider(network, cache_dir=tmp_path), "eth_call", CALL)

    assert network.count("eth_call") == 1


def test_entries_are_kept_per_chain(tmp_path):
    mainnet, base = _Network(chain_id=1), _Network(chain_id=8453)
    for network in (mainnet, base):
        _request(CachingProvider(network, cache_dir=tmp_path), "eth_call", CALL)

    assert (mainnet.count("eth_call"), base.count("eth_call")) == (1, 1)
    assert sorted(p.name for p in tmp_path.iterdir()) == ["1", "8453"]


def test_entries_are_kept_per_host(tmp_path):
    node = _Network(endpoint_uri="https://rpc.example/v2/key")
    fork = _Network(endpoint_uri="https://fork.example/v2/key")
    for network in (node, fork):
        _request(CachingProvider(network, cache_dir=tmp_path), "eth_call", CALL)

    assert (node.count("eth_call"), fork.count("eth_call")) == (1, 1)
    assert len(list((tmp_path / "1").iterdir())) == 2


def test_keys_of_one_host_share_entries_and_never_reach_the_disk(tmp_path):
    first = _Network(endpoint_uri="https://rpc.example/v2/key-1234")
    second = _Network(endpoint_uri="https://rpc.example/?token=key-5678")
    for network in (first, second):
        _request(CachingProvider(network, cache_dir=tmp_path), "eth_call", CALL)

    assert (first.count("eth_call"), second.count("eth_call")) == (1, 0)
    (entry,) = _entries(tmp_path)
    assert entry.parent.name == endpoint_key("https://rpc.example")
    assert "key-" not in str(entry)
    assert "key-" not in entry.read_text()


@pytest.mark.parametrize(
    "endpoint_uri",
    [
        "http://localhost:8545",
        "http://anvil.localhost:8545",
        "http://127.0.0.1:8545",
        "http://[::1]:8545",
        "http://10.0.0.5:8545",
        "http://192.168.1.2",
        "http://169.254.0.1",
        "http://100.64.0.1:8545",
        "http://anvil:8545",
        "ws://rpc.example",
        "https://rpc.example:notaport",
        None,
    ],
)
def test_local_or_unknown_endpoints_are_never_cached(tmp_path, endpoint_uri):
    network = _Network(endpoint_uri=endpoint_uri)
    provider = CachingProvider(network, cache_dir=tmp_path)

    _twice(provider, "eth_call", CALL)

    assert network.calls == ["eth_call", "eth_call"]  # no chain-id or finality lookup
    assert _files(tmp_path) == []


@pytest.mark.parametrize(
    "endpoint_uri", ["http://8.8.8.8:8545", "https://[2001:4860::8888]"]
)
def test_global_address_endpoints_get_a_cache_key(endpoint_uri):
    assert endpoint_key(endpoint_uri) is not None


def test_the_endpoint_key_ignores_the_default_port():
    assert endpoint_key("https://rpc.example") == endpoint_key(
        "https://rpc.example:443"
    )
    assert endpoint_key("http://rpc.example") != endpoint_key("https://rpc.example")


@pytest.mark.parametrize(
    "method, params",
    [
        ("eth_call", [{"to": "0xabc"}, hex(FINALIZED + 1)]),
        ("eth_getLogs", [{"fromBlock": "0x0", "toBlock": hex(FINALIZED + 1)}]),
        ("eth_call", [{"to": "0xabc"}, "latest"]),
        ("eth_blockNumber", []),
        ("eth_call", ["revert", hex(FINALIZED)]),
        ("eth_call", ["null", hex(FINALIZED)]),
        ("eth_call", ["empty-hex", hex(FINALIZED)]),
        ("eth_call", ["empty-list", hex(FINALIZED)]),
        ("eth_getLogs", [{"fromBlock": "0x0", "toBlock": "latest"}]),
        ("eth_call", [{1: "a", "b": 2}, hex(FINALIZED)]),
    ],
    ids=[
        "past-finality",
        "logs-past-finality",
        "latest",
        "no-block",
        "error",
        "null",
        "empty-hex",
        "empty-list",
        "open-logs",
        "unkeyable",
    ],
)
def test_moving_failed_or_unkeyable_reads_always_reach_the_network(
    network, provider, method, params
):
    _twice(provider, method, params)

    assert network.count(method) == 2


def test_a_chain_without_a_finalized_block_caches_nothing(tmp_path):
    network = _Network(finalized=None)
    provider = CachingProvider(network, cache_dir=tmp_path)

    for _ in range(3):
        _request(provider, "eth_call", [{"to": "0xabc"}, "0x1"])

    assert network.count("eth_call") == 3
    assert network.count("eth_getBlockByNumber") == 1


@pytest.mark.parametrize("failure", [pytest.param(ERROR, id="error"), *LOOKUP_FAILURES])
def test_a_failed_finality_lookup_is_retried(tmp_path, failure):
    network = _Network(finalized=[failure, FINALIZED])
    provider = CachingProvider(network, cache_dir=tmp_path)

    for _ in range(3):
        _request(provider, "eth_call", CALL)

    assert network.count("eth_getBlockByNumber") == 2
    assert network.count("eth_call") == 2  # uncached, then stored, then served
    assert len(_entries(tmp_path)) == 1


@pytest.mark.parametrize(
    "answer",
    [
        pytest.param(ERROR, id="error"),
        pytest.param({}, id="no-number"),
        pytest.param({"number": "0xzz"}, id="bad-number"),
        pytest.param("0x64", id="not-a-block"),
        *LOOKUP_FAILURES,
    ],
)
def test_finality_lookups_stop_after_repeated_failures(tmp_path, answer):
    network = _Network(finalized=answer)
    provider = CachingProvider(network, cache_dir=tmp_path)

    for _ in range(5):
        _request(provider, "eth_call", CALL)

    assert network.count("eth_getBlockByNumber") == _rpc_cache._LOOKUP_ATTEMPTS
    assert network.count("eth_call") == 5


def test_a_finalized_genesis_block_is_cached(tmp_path):
    network = _Network(finalized=0)
    provider = CachingProvider(network, cache_dir=tmp_path)

    _twice(provider, "eth_call", [{"to": "0xabc"}, "0x0"])

    assert network.count("eth_call") == 1


@pytest.mark.parametrize(
    "chain_id",
    [
        pytest.param(ERROR, id="error"),
        pytest.param(None, id="null"),
        pytest.param("0xzz", id="bad"),
        *LOOKUP_FAILURES,
    ],
)
def test_failed_chain_id_read_skips_the_cache_and_is_retried(tmp_path, chain_id):
    network = _Network(chain_id=chain_id)
    provider = CachingProvider(network, cache_dir=tmp_path)

    _twice(provider, "eth_call", CALL)

    assert network.count("eth_call") == 2
    assert network.count("eth_chainId") == 2


@pytest.mark.parametrize(
    "chain_id", [pytest.param(ERROR, id="error"), *LOOKUP_FAILURES]
)
def test_chain_id_lookups_stop_after_repeated_failures(tmp_path, chain_id):
    network = _Network(chain_id=chain_id)
    provider = CachingProvider(network, cache_dir=tmp_path)

    for _ in range(5):
        _request(provider, "eth_call", CALL)

    assert network.count("eth_chainId") == _rpc_cache._LOOKUP_ATTEMPTS
    assert network.count("eth_call") == 5


def test_a_failing_read_still_raises(provider):
    with pytest.raises(requests.Timeout, match="read timed out"):
        _request(provider, "eth_call", ["timeout", hex(FINALIZED)])


def test_finality_and_chain_id_are_read_once(network, provider):
    for block in range(3):
        _request(provider, "eth_call", [{"to": "0xabc"}, hex(block)])

    assert network.count("eth_call") == 3
    assert network.count("eth_getBlockByNumber") == 1
    assert network.count("eth_chainId") == 1


def test_requests_differing_in_any_param_get_their_own_entries(
    network, provider, tmp_path
):
    overrides = {"0xabc": {"balance": "0x1"}}
    _request(provider, "eth_call", [*CALL, overrides])
    _request(provider, "eth_call", [*CALL, {"0xabc": {"balance": "0x2"}}])
    _request(provider, "eth_call", CALL)

    assert network.count("eth_call") == 3
    assert len(_entries(tmp_path)) == 3


def test_key_ignores_dict_order_and_bytes_representation(network, provider):
    _request(provider, "eth_call", [{"to": "0xabc", "data": "0x12ab"}, hex(FINALIZED)])
    _request(
        provider,
        "eth_call",
        [{"data": HexBytes("0x12ab"), "to": "0xabc"}, hex(FINALIZED)],
    )

    assert network.count("eth_call") == 1


@pytest.mark.parametrize(
    "content",
    ["{trunc", "null", "[]", "{}", '{"error": {"code": -32000}}'],
    ids=["truncated", "null", "list", "empty", "no-result"],
)
def test_unusable_entry_is_refetched_and_replaced(network, provider, tmp_path, content):
    response = _request(provider, "eth_call", CALL)
    (entry,) = _entries(tmp_path)
    entry.write_text(content)

    assert _request(provider, "eth_call", CALL) == response
    assert network.count("eth_call") == 2
    assert _request(provider, "eth_call", CALL) == response
    assert network.count("eth_call") == 2


def test_unreadable_entry_path_still_answers(network, provider, tmp_path):
    response = _request(provider, "eth_call", CALL)
    (entry,) = _entries(tmp_path)
    entry.unlink()
    entry.mkdir()

    assert _twice(provider, "eth_call", CALL) == response
    assert network.count("eth_call") == 3


def test_failed_write_still_answers_and_leaves_no_file(
    monkeypatch, network, provider, tmp_path
):
    def fail(*_args):
        raise OSError("disk full")

    monkeypatch.setattr(_rpc_cache.os, "replace", fail)

    assert _request(provider, "eth_call", CALL)["result"]
    assert _files(tmp_path) == []


def test_unwritable_cache_dir_still_answers(network, tmp_path):
    blocker = tmp_path / "file"
    blocker.write_text("")
    provider = CachingProvider(network, cache_dir=blocker)

    assert _twice(provider, "eth_call", CALL)["result"]
    assert network.count("eth_call") == 2


def test_batch_requests_pass_through_uncached(network, provider, tmp_path):
    batch = [(RPCEndpoint("eth_call"), CALL)]

    assert provider.make_batch_request(batch) == provider.make_batch_request(batch)
    assert network.count("<batch>") == 2
    assert _files(tmp_path) == []


def test_web3_batch_requests_reach_the_wrapped_provider(provider, network, monkeypatch):
    def reply(requests):
        assert len(requests) == 1
        assert requests[0][0] == "eth_chainId"
        return [{"jsonrpc": "2.0", "id": 1, "result": "0x1"}]

    monkeypatch.setattr(network, "make_batch_request", reply)
    w3 = Web3(provider)
    with w3.batch_requests() as batch:
        batch.add(w3.eth.chain_id)
        assert batch.execute() == [1]


def test_connection_checks_go_to_the_wrapped_provider(provider):
    assert provider.is_connected(show_traceback=True) is True
    assert provider.is_connected() is False


@pytest.mark.parametrize(
    "method, params, block",
    [
        ("eth_call", [{}, "0x10"], 16),
        ("eth_call", [{}, 16], 16),
        ("eth_simulateV1", [{}, "0x10"], 16),
        ("eth_getBalance", ["0xabc", "0x10"], 16),
        ("eth_getCode", ["0xabc", "0x10"], 16),
        ("eth_getTransactionCount", ["0xabc", "0x10"], 16),
        ("eth_getStorageAt", ["0xabc", "0x0", "0x10"], 16),
        ("eth_getBlockByNumber", ["0x10", False], 16),
        ("eth_getLogs", [{"fromBlock": "0x0", "toBlock": "0x10"}], 16),
        ("eth_getLogs", [{"fromBlock": "latest", "toBlock": "0x10"}], None),
        ("eth_getLogs", [{"blockHash": "0x" + "ab" * 32}], None),
        ("eth_getLogs", [{"toBlock": "0x10"}], None),
        ("eth_getLogs", [{"fromBlock": "0x0"}], None),
        ("eth_getLogs", [], None),
        ("eth_getLogs", ["0x10"], None),
        ("eth_call", [{}, {"blockNumber": "0x10"}], None),
        ("eth_call", [{}, "0xzz"], None),
        ("eth_call", [{}, "safe"], None),
        ("eth_call", [{}, False], None),
        ("eth_call", [{}], None),
        ("eth_call", {"block": "0x10"}, None),
        ("eth_sendRawTransaction", ["0x10"], None),
    ],
)
def test_pinned_block(method, params, block):
    assert pinned_block(method, params) == block


def _stored_call(monkeypatch, tmp_path) -> Path:
    """Point the cache at `tmp_path`, store one finalized-block `eth_call` in it
    and return the file it went to."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.delenv("IPOR_FUSION_TEST_RPC_CACHE", raising=False)
    _request(with_cache(_Network()), "eth_call", CALL)
    (entry,) = _entries(tmp_path)
    return entry


def test_the_cache_lives_under_xdg_cache_home(monkeypatch, tmp_path):
    entry = _stored_call(monkeypatch, tmp_path)

    assert entry.is_relative_to(tmp_path / "ipor-fusion" / "tests" / "rpc" / "v1" / "1")


def test_a_new_format_ignores_the_old_entries(monkeypatch, tmp_path):
    _stored_call(monkeypatch, tmp_path)
    monkeypatch.setattr(_rpc_cache, "_FORMAT_VERSION", "v2")
    network = _Network()

    _request(with_cache(network), "eth_call", CALL)

    assert network.count("eth_call") == 1


def test_cache_can_be_turned_off(monkeypatch):
    monkeypatch.setenv("IPOR_FUSION_TEST_RPC_CACHE", "0")
    network = _Network()
    assert with_cache(network) is network
    assert type(http_provider(URL)) is HTTPProvider

    monkeypatch.delenv("IPOR_FUSION_TEST_RPC_CACHE")
    assert isinstance(http_provider(URL), CachingProvider)


@pytest.mark.parametrize("enabled", [False, True])
def test_wrapping_keeps_the_ccip_read_guard(tmp_path, enabled):
    network = _Network()
    network.global_ccip_read_enabled = enabled

    provider = CachingProvider(network, cache_dir=tmp_path)

    assert provider.global_ccip_read_enabled is enabled


def test_clear_cache_removes_every_entry(monkeypatch, tmp_path):
    entry = _stored_call(monkeypatch, tmp_path)
    assert entry.is_relative_to(cache_dir())
    # Outside `rpc/`, so a clear of `rpc/` alone fails the test.
    unversioned_entry = cache_root() / "1" / "abc" / "entry.json"
    unversioned_entry.parent.mkdir(parents=True)
    unversioned_entry.write_text("{}")

    clear_cache()

    assert not cache_root().exists()
    clear_cache()  # nothing left to remove is no error


def test_clear_cache_raises_when_it_cannot_remove(monkeypatch, tmp_path):
    _stored_call(monkeypatch, tmp_path)

    def fail(path):
        raise PermissionError(path)

    monkeypatch.setattr(_rpc_cache.shutil, "rmtree", fail)

    with pytest.raises(PermissionError):
        clear_cache()


def test_the_clear_option_is_registered(pytestconfig):
    # An unregistered dest raises ValueError; either value is fine, so the test
    # passes with or without the flag on the command line.
    assert isinstance(pytestconfig.getoption("rpc_cache_clear"), bool)


@pytest.mark.parametrize(
    ("requested", "worker", "cleared"),
    [(False, False, False), (True, False, True), (True, True, False)],
    ids=["not-requested", "controller", "xdist-worker"],
)
def test_clear_if_requested(monkeypatch, tmp_path, requested, worker, cleared):
    entry = _stored_call(monkeypatch, tmp_path)
    config = SimpleNamespace(getoption={"rpc_cache_clear": requested}.__getitem__)
    if worker:
        config.workerinput = {"workerid": "gw0"}

    clear_if_requested(cast("pytest.Config", config))

    assert entry.exists() is not cleared


CONTRACT = "0x" + "11" * 20
EXTRA_DATA = "0x" + "ab" * 66


class _ArchiveNode(_Network):
    """A `_Network` whose `eth_getCode` answers `code` (`ERROR` raises a
    connection error) and whose block 16 carries `EXTRA_DATA`."""

    code: Any = "0x6080"

    def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        if method == "eth_getCode":
            self.calls.append(method)
            if self.code is ERROR:
                raise requests.ConnectionError
            return _reply(self.code)
        if method == "eth_getBlockByNumber" and params[0] == "0x10":
            self.calls.append(method)
            return _reply({"number": "0x10", "extraData": EXTRA_DATA})
        return super().make_request(method, params)


@pytest.fixture
def archive_node(monkeypatch, tmp_path) -> _ArchiveNode:
    """An `_ArchiveNode` behind every `HTTPProvider`, at `URL` in the env var
    `TEST_PROVIDER_URL`."""
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
    monkeypatch.setenv("TEST_PROVIDER_URL", URL)
    monkeypatch.delenv("IPOR_FUSION_TEST_RPC_CACHE", raising=False)
    monkeypatch.delenv("IPOR_FUSION_REQUIRE_LIVE", raising=False)
    network = _ArchiveNode()
    monkeypatch.setattr(
        HTTPProvider,
        "make_request",
        lambda _self, method, params: network.make_request(method, params),
    )
    return network


def test_archive_context_probes_the_node_and_caches_its_reads(archive_node, tmp_path):
    for _ in range(2):
        ctx = archive_context("TEST_PROVIDER_URL", 16, CONTRACT)
        # Over 32 bytes of extraData fail web3's block formatter unless
        # `from_url`'s ExtraDataToPOAMiddleware still runs, from the cache too.
        block = ctx.web3.eth.get_block(16)
        assert block["proofOfAuthorityData"] == HexBytes(EXTRA_DATA)
        assert "in_flight_limit" in ctx.web3.middleware_onion
        assert "validation" not in ctx.web3.middleware_onion
        assert ctx.web3.provider.global_ccip_read_enabled is False

    assert archive_node.count("eth_getCode") == 2
    # A finality lookup per provider and one block read; the second is cached.
    assert archive_node.count("eth_getBlockByNumber") == 3
    assert len(_entries(tmp_path)) == 1


@pytest.mark.parametrize("code", ["0x", ERROR])
def test_archive_context_skips_a_provider_that_cannot_serve_the_block(
    archive_node, code
):
    archive_node.code = code

    with pytest.raises(pytest.skip.Exception):
        archive_context("TEST_PROVIDER_URL", 16, CONTRACT)


def test_archive_context_can_skip_the_cache(archive_node, monkeypatch):
    monkeypatch.setenv("IPOR_FUSION_TEST_RPC_CACHE", "0")

    ctx = archive_context("TEST_PROVIDER_URL", 16, CONTRACT)

    assert type(ctx.web3.provider) is HTTPProvider

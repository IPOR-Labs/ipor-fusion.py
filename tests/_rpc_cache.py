"""On-disk cache of RPC responses that can no longer change.

A read at a block at or below the chain's finalized block answers the same
forever, so `CachingProvider` stores its response under
`$XDG_CACHE_HOME/ipor-fusion/tests/rpc/<format>/<chain id>/<host hash>/`
(`~/.cache/...` when unset) and serves it from there on later runs;
`<format>` is `_FORMAT_VERSION`, so a change to it starts an empty directory.
It wraps a provider rather than replacing one, so it also goes around the one
`Web3Context.from_url` builds. Reads at `latest` or past finality (tests that
pin the head at run time), error responses, empty results and batch requests
always go to the network.

The host hash covers only the endpoint's scheme, host and port, so an API key
in its path or query never reaches the disk, hashed or not, and a fork on
another host never shares the node's entries. An endpoint on an address that
is not global (loopback, private, link-local, shared `100.64.0.0/10`) or on a
single-label host (`localhost`, a compose service such as `anvil`), where
Anvil, Hardhat or a local proxy run, is never cached: a fork restarted at the
same URL serves different state. A DNS name that resolves to such an address
is not detected.

Assumptions: a public endpoint keeps serving the same chain's state; its logs
at a finalized block are complete (an empty answer is not stored, so a scan's
empty pages are refetched on every run, but a lagging backend's truncated
list would be); the params of reads at finalized blocks are deterministic
(a wall-clock time or a random salt adds an entry on every run); and only the
tests write the cache directory: an entry is served as it is, never checked
against the chain, so whoever can write there decides what the tests see. The
cache only ever speeds tests up: a failed cache read or write, a failed lookup
of its own (chain id, finality), or a request it cannot key, falls back to the
network.
`IPOR_FUSION_TEST_RPC_CACHE=0` turns it off; `pytest --rpc-cache-clear` empties
`$XDG_CACHE_HOME/ipor-fusion/tests/`, every format's entries, before the run.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import os
import shutil
import tempfile
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

import pytest
from web3 import HTTPProvider, Web3
from web3.exceptions import Web3Exception
from web3.providers import BaseProvider, JSONBaseProvider
from web3.types import RPCEndpoint, RPCResponse

from ipor_fusion.cache import default_cache_path

#: Position of the block parameter, per cacheable method (`eth_getLogs`, whose
#: blocks sit in a filter object, is handled in `pinned_block`).
_BLOCK_PARAM_INDEX = {
    "eth_call": 1,
    "eth_simulateV1": 1,
    "eth_getBalance": 1,
    "eth_getCode": 1,
    "eth_getTransactionCount": 1,
    "eth_getStorageAt": 2,
    "eth_getBlockByNumber": 0,
}

#: Results a lagging or pruned backend can return instead of an error; often
#: legitimate too, but never worth the risk of storing a wrong one for good.
_UNCACHED_RESULTS: tuple[Any, ...] = (None, "0x", [])

#: The finalized block of a chain whose finality is unknown: no block is at or
#: below it, so nothing is cached.
_NO_FINALITY = -1

#: Failed lookups of the finalized block, and separately of the chain id (an
#: error or malformed answer, or one of `_LOOKUP_ERRORS`), before a provider
#: stops caching; a transient one must not turn the cache off for the session.
_LOOKUP_ATTEMPTS = 3

#: A provider that cannot be reached, refuses the key or fails the request (a
#: pruned state included); `requests`' exceptions are `OSError`s.
TRANSPORT_ERRORS = (Web3Exception, OSError)

#: Failures of the cache's own lookups: the transport's and an answer that
#: does not decode (not UTF-8, not JSON).
_LOOKUP_ERRORS = (*TRANSPORT_ERRORS, UnicodeDecodeError, json.JSONDecodeError)

#: Format of the stored entries, a directory under `<cache_root>/rpc`. Bump it
#: whenever an old entry would be read wrongly: the request key
#: (`CachingProvider._entry`), the path layout (`_chain_cache_dir`,
#: `endpoint_key`) or the stored JSON (`_read_entry`). `--rpc-cache-clear`
#: removes every format's directory.
_FORMAT_VERSION = "v1"


def http_provider(url: str) -> BaseProvider:
    """An `HTTPProvider` for `url`, wrapped by `with_cache`."""
    return with_cache(HTTPProvider(url))


def with_cache(provider: BaseProvider) -> BaseProvider:
    """`provider` behind a `CachingProvider`, unless `IPOR_FUSION_TEST_RPC_CACHE=0`."""
    if os.environ.get("IPOR_FUSION_TEST_RPC_CACHE") == "0":
        return provider
    return CachingProvider(provider, cache_dir=cache_dir())


def cache_root() -> Path:
    """`$XDG_CACHE_HOME/ipor-fusion/tests`, the test suite's caches."""
    return default_cache_path().parent / "tests"


def cache_dir() -> Path:
    """`<cache_root>/rpc/<_FORMAT_VERSION>`, the directory `with_cache` stores in."""
    return cache_root() / "rpc" / _FORMAT_VERSION


def clear_cache() -> None:
    """Remove `cache_root`, every format's stored responses included; a failure
    other than a missing directory raises, so a run never goes on believing a
    stale cache was cleared."""
    with contextlib.suppress(FileNotFoundError):
        shutil.rmtree(cache_root())


def clear_if_requested(config: pytest.Config) -> None:
    """`clear_cache` under `pytest --rpc-cache-clear`.

    Under pytest-xdist only the controller clears, before its workers start; a
    worker clearing would delete what the others already stored.
    """
    if config.getoption("rpc_cache_clear") and not hasattr(config, "workerinput"):
        clear_cache()


def _quantity(value: Any) -> int | None:
    """A JSON-RPC quantity (an int or a `0x` hex string) as an int, else None."""
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str) and value.startswith("0x"):
        with contextlib.suppress(ValueError):
            return int(value, 16)
    return None


def pinned_block(method: str, params: Any) -> int | None:
    """The block a cacheable request reads at, or None if it reads a moving target."""
    if not isinstance(params, list | tuple):
        return None
    if method == "eth_getLogs":
        log_filter = params[0] if params else None
        if (
            not isinstance(log_filter, dict)
            or "blockHash" in log_filter
            or _quantity(log_filter.get("fromBlock", "latest")) is None
        ):
            return None
        return _quantity(log_filter.get("toBlock", "latest"))
    index = _BLOCK_PARAM_INDEX.get(method)
    if index is None or len(params) <= index:
        return None
    return _quantity(params[index])


def endpoint_key(endpoint_uri: Any) -> str | None:
    """The cache directory name of an endpoint: a hash of its scheme, host and
    port; None for an endpoint that must not be cached (no URL, a local host
    as the module docstring defines it)."""
    if endpoint_uri is None:
        return None
    url = urlsplit(str(endpoint_uri))
    host = url.hostname
    if url.scheme not in ("http", "https") or not host or _is_local(host):
        return None
    try:
        port = url.port or (443 if url.scheme == "https" else 80)
    except ValueError:
        return None
    origin = f"{url.scheme}://{host}:{port}"
    return hashlib.sha256(origin.encode()).hexdigest()[:16]


def _is_local(host: str) -> bool:
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        # Checked after the address: an IPv6 literal has no dot either.
        return "." not in host or host.endswith(".localhost")
    return not address.is_global


class CachingProvider(JSONBaseProvider):
    """`inner`, with its reads at finalized blocks served from `cache_dir`.

    A `JSONBaseProvider` because web3 batches (`Web3.batch_requests`) only
    through one; what the cache does not serve, batches included, goes to
    `inner`."""

    def __init__(self, inner: BaseProvider, *, cache_dir: Path) -> None:
        super().__init__()
        # web3 reads the EIP-3668 guard off `w3.provider`, this wrapper, so it
        # takes the one `Web3Context` set on `inner` (`archive_context` wraps
        # after that). A copy, taken once: IF ANYTHING EVER WRITES THE FLAG
        # AFTER WRAPPING, MAKE THIS A PROPERTY DELEGATING TO `inner`.
        self.global_ccip_read_enabled = inner.global_ccip_read_enabled
        self._inner = inner
        self._cache_dir = cache_dir
        self._endpoint = endpoint_key(getattr(inner, "endpoint_uri", None))
        self._chain_dir: Path | None = None
        self._finalized: int | None = None
        self._finality_failures = 0
        self._chain_id_failures = 0

    def is_connected(self, show_traceback: bool = False) -> bool:
        return self._inner.is_connected(show_traceback)

    def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        entry = self._entry(method, params)
        if entry is None:
            return self._inner.make_request(method, params)
        cached = _read_entry(entry)
        if cached is not None:
            return cached
        response = self._inner.make_request(method, params)
        if response.get("result") not in _UNCACHED_RESULTS:
            with contextlib.suppress(OSError):
                _write_atomically(entry, json.dumps(response))
        return response

    def make_batch_request(self, requests: list[tuple[RPCEndpoint, Any]]) -> Any:
        # Forwarded uncached: no SDK code sends batches.
        return self._inner.make_batch_request(requests)  # pyright: ignore[reportAttributeAccessIssue]

    def _entry(self, method: str, params: Any) -> Path | None:
        if self._endpoint is None:
            return None
        block = pinned_block(method, params)
        if block is None or block > self._finalized_block():
            return None
        chain_dir = self._chain_cache_dir()
        if chain_dir is None:
            return None
        try:
            # `Web3.to_json` applies the encoder `HTTPProvider.encode_rpc_request`
            # uses, so a key sees the params as they go on the wire (`HexBytes`
            # and hex strings alike); the round trip adds the `sort_keys` it
            # does not take.
            wire_params = json.loads(Web3.to_json({"params": params}))["params"]
            request = json.dumps(
                [method, wire_params], sort_keys=True, separators=(",", ":")
            )
        except (TypeError, ValueError):
            return None
        return chain_dir / f"{hashlib.sha256(request.encode()).hexdigest()}.json"

    def _chain_cache_dir(self) -> Path | None:
        """`<cache_dir>/<chain id>/<endpoint key>`, or None if the chain id
        cannot be read: retried, up to `_LOOKUP_ATTEMPTS` lookups, after which
        the provider caches nothing."""
        if self._chain_dir is None:
            if self._endpoint is None:
                return None
            response = self._lookup("eth_chainId", [])
            chain_id = _quantity(response.get("result"))
            if chain_id is None:
                self._chain_id_failures += 1
                if self._chain_id_failures >= _LOOKUP_ATTEMPTS:
                    self._endpoint = None  # no key: nothing is cached
                return None
            self._chain_dir = self._cache_dir / str(chain_id) / self._endpoint
        return self._chain_dir

    def _finalized_block(self) -> int:
        """The chain's finalized block, or `_NO_FINALITY`; `self._finalized`
        stays None until a lookup settles it."""
        if self._finalized is None:
            self._finalized = self._read_finalized()
        return _NO_FINALITY if self._finalized is None else self._finalized

    def _read_finalized(self) -> int | None:
        """The finalized block, `_NO_FINALITY` for good, or None to ask again.

        A `null` answer is final: the chain has no finalized block, and asking
        again would add a round trip to every read. An error, a malformed
        answer or one of `_LOOKUP_ERRORS` is retried, up to `_LOOKUP_ATTEMPTS`
        lookups.
        """
        response = self._lookup("eth_getBlockByNumber", ["finalized", False])
        block = response.get("result")
        number = _quantity(block.get("number")) if isinstance(block, dict) else None
        if number is not None:
            return number
        if "result" in response and block is None:
            return _NO_FINALITY
        self._finality_failures += 1
        return _NO_FINALITY if self._finality_failures >= _LOOKUP_ATTEMPTS else None

    def _lookup(self, method: str, params: Any) -> RPCResponse:
        """`inner`'s answer to a request the cache makes for itself, or an
        empty response on one of `_LOOKUP_ERRORS` or an answer that is not a
        JSON object: such a failure leaves the read it was made for uncached,
        and the read itself still goes to `inner`."""
        try:
            response = self._inner.make_request(RPCEndpoint(method), params)
        except _LOOKUP_ERRORS:
            return cast("RPCResponse", {})
        # web3 checks no shape: a `null` or a string decodes too.
        return response if isinstance(response, dict) else cast("RPCResponse", {})


def _read_entry(entry: Path) -> RPCResponse | None:
    """The response stored at `entry`; None (unreadable ones removed) if none."""
    try:
        response = json.loads(entry.read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except (OSError, ValueError):
        response = None
    if isinstance(response, dict) and "result" in response:
        return cast("RPCResponse", response)
    with contextlib.suppress(OSError):
        entry.unlink(missing_ok=True)
    return None


def _write_atomically(path: Path, text: str) -> None:
    # Concurrent writers (pytest-xdist workers) each replace the file whole.
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp_name = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as tmp:
            tmp.write(text)
        os.replace(tmp_name, path)
    except BaseException:
        Path(tmp_name).unlink(missing_ok=True)
        raise

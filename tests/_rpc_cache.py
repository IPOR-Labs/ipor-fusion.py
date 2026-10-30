"""On-disk cache of RPC responses that can no longer change.

A read at a block at or below the chain's finalized block answers the same
forever, so `CachingProvider` stores its response under
`$XDG_CACHE_HOME/ipor-fusion/tests/<chain id>/<host hash>/` (`~/.cache/...`
when unset) and serves it from there on later runs. It wraps a provider rather
than replacing one, so it also goes around the one `Web3Context.from_url`
builds. Reads at `latest` or past finality (tests that pin the head at run
time), error responses, empty results and batch requests always go to the
network.

The host hash covers only the endpoint's scheme, host and port, so an API key
in its path or query never reaches the disk, hashed or not, and a fork on
another host never shares the node's entries. An endpoint on a loopback or
private address (Anvil, Hardhat, a local proxy) is never cached: a fork
restarted at the same URL serves different state.

Assumptions: a public endpoint keeps serving the same chain's state; its logs
at a finalized block are complete (an empty answer is not stored, so a scan's
empty pages are refetched on every run, but a lagging backend's truncated
list would be); and the params of reads at finalized blocks are deterministic
(a wall-clock time or a random salt adds an entry on every run). The cache
only ever speeds tests up: a failed cache read or write, or a request it
cannot key, falls back to the network.
`IPOR_FUSION_TEST_RPC_CACHE=0` turns it off; deleting the directory resets it.
"""

from __future__ import annotations

import contextlib
import hashlib
import ipaddress
import json
import os
import tempfile
from pathlib import Path
from typing import Any, cast
from urllib.parse import urlsplit

from web3 import HTTPProvider

# web3's private `Web3JsonEncoder`, the one `HTTPProvider.encode_rpc_request`
# uses, so a key sees the params exactly as they go on the wire (`HexBytes` and
# hex strings alike); a web3 release that moves it fails this import.
from web3._utils.encoding import Web3JsonEncoder
from web3.providers import BaseProvider
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

#: Failed `finalized` lookups (errors, malformed answers) before a provider
#: stops caching; a transient one must not turn the cache off for the session.
_FINALITY_ATTEMPTS = 3


def http_provider(url: str) -> BaseProvider:
    """An `HTTPProvider` for `url`, wrapped by `with_cache`."""
    return with_cache(HTTPProvider(url))


def with_cache(provider: BaseProvider) -> BaseProvider:
    """`provider` behind a `CachingProvider`, unless `IPOR_FUSION_TEST_RPC_CACHE=0`."""
    if os.environ.get("IPOR_FUSION_TEST_RPC_CACHE") == "0":
        return provider
    return CachingProvider(provider, cache_dir=default_cache_path().parent / "tests")


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
    port; None for an endpoint that must not be cached (no URL, loopback or
    private host)."""
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
    if host == "localhost" or host.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        return False
    return address.is_loopback or address.is_private or address.is_link_local


class CachingProvider(BaseProvider):
    """`inner`, with its reads at finalized blocks served from `cache_dir`."""

    def __init__(self, inner: BaseProvider, *, cache_dir: Path) -> None:
        super().__init__()
        self._inner = inner
        self._cache_dir = cache_dir
        self._endpoint = endpoint_key(getattr(inner, "endpoint_uri", None))
        self._chain_dir: Path | None = None
        self._finalized: int | None = None
        self._finality_failures = 0

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
        # `BaseProvider` lacks it (`HTTPProvider` inherits it from
        # `JSONBaseProvider`), so without it a batch on a wrapped `Web3`
        # raises. Forwarded uncached: no SDK code sends batches.
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
            request = json.dumps(
                [method, params],
                cls=Web3JsonEncoder,
                sort_keys=True,
                separators=(",", ":"),
            )
        except (TypeError, ValueError):
            return None
        return chain_dir / f"{hashlib.sha256(request.encode()).hexdigest()}.json"

    def _chain_cache_dir(self) -> Path | None:
        """`<cache_dir>/<chain id>/<endpoint key>`; None (retried next time) if
        the chain id cannot be read."""
        if self._chain_dir is None:
            response = self._inner.make_request(RPCEndpoint("eth_chainId"), [])
            chain_id = _quantity(response.get("result"))
            if chain_id is None or self._endpoint is None:
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
        again would add a round trip to every read. An error or a malformed
        answer is retried, up to `_FINALITY_ATTEMPTS` lookups.
        """
        response = self._inner.make_request(
            RPCEndpoint("eth_getBlockByNumber"), ["finalized", False]
        )
        block = response.get("result")
        number = _quantity(block.get("number")) if isinstance(block, dict) else None
        if number is not None:
            return number
        if "result" in response and block is None:
            return _NO_FINALITY
        self._finality_failures += 1
        return _NO_FINALITY if self._finality_failures >= _FINALITY_ATTEMPTS else None


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

"""Caches of immutable per-contract chain facts: contract names, token symbols
and decimals, deployment blocks. A `ContractCache` is injected through
`Web3Context(cache=...)` (and `get_contract_name(cache=...)`); the default is
`FileContractCache` at ``$XDG_CACHE_HOME/ipor-fusion/contract_cache.json``,
and `NullCache` disables caching.

A cache is an optimization, never a source of failure: the SDK reads and
writes it through `cache_get` / `cache_put`, which log and swallow any error a
cache raises, and `FileContractCache` itself degrades to an in-memory map when
its file is corrupt, unreadable or unwritable."""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from collections.abc import Iterable, Mapping
from pathlib import Path
from typing import Literal, NamedTuple, Protocol, cast, get_args

from eth_typing import HexAddress

from ipor_fusion.types import ChainId, parse_hex_address

_logger = logging.getLogger(__name__)

CacheField = Literal[
    "name", "symbol", "decimals", "deployment_block", "deployment_timestamp"
]
_FIELDS: frozenset[str] = frozenset(get_args(CacheField))


class CacheKey(NamedTuple):
    """One fact about the contract at ``address`` on ``chain_id``. Build it
    with `cache_key`, which normalizes the address to lowercase."""

    chain_id: ChainId
    address: HexAddress
    field: CacheField


def cache_key(chain_id: ChainId, address: HexAddress, field: CacheField) -> CacheKey:
    return CacheKey(chain_id, parse_hex_address(address), field)


class ContractCache(Protocol):
    """Immutable facts keyed by chain, address and field, as strings. Store
    only what cannot change for that key; absence (no code, no answer) is
    never stored."""

    def get(self, keys: Iterable[CacheKey]) -> dict[CacheKey, str]:
        """The cached values of ``keys``; a miss is absent from the result."""
        ...

    def put(self, entries: Mapping[CacheKey, str]) -> None: ...


class NullCache:
    """A `ContractCache` that stores nothing: every read goes to the chain or
    the explorer."""

    def get(self, keys: Iterable[CacheKey]) -> dict[CacheKey, str]:
        return {}

    def put(self, entries: Mapping[CacheKey, str]) -> None:
        return None


def _encode(key: CacheKey) -> str:
    return f"{key.chain_id}:{key.address}:{key.field}"


def _decode(raw: str) -> CacheKey | None:
    """The key of one stored entry, ``None`` for an entry that cannot be
    attributed to a chain, an address and a field. A ``<chain>:<address>`` entry is a contract
    name (the 3.x layout); 3.x token entries (``symbol:<address>``) carry no
    chain and are dropped."""
    parts = raw.split(":")
    if len(parts) == 2:
        parts.append("name")
    if len(parts) != 3 or not parts[0].isdigit() or parts[2] not in _FIELDS:
        return None
    chain, address, field = parts
    try:
        return cache_key(
            ChainId(int(chain)), parse_hex_address(address), cast(CacheField, field)
        )
    except ValueError:
        return None


class FileContractCache:
    """A `ContractCache` in one JSON file, loaded once per instance.

    A write merges into the file as it is on disk (another process may have
    added entries) and replaces it atomically (a temporary file in the same
    directory, then `os.replace`), so a crash or a concurrent writer never
    leaves a torn file; concurrent writers may drop each other's latest entry,
    which is refetched later. A corrupt or unreadable file reads as empty and
    is rewritten by the next write; a failed write is logged and the entry
    kept in memory only."""

    def __init__(self, path: Path) -> None:
        self.path = path
        self._lock = threading.Lock()
        self._entries: dict[CacheKey, str] | None = None

    def get(self, keys: Iterable[CacheKey]) -> dict[CacheKey, str]:
        with self._lock:
            entries = self._loaded()
            return {key: entries[key] for key in keys if key in entries}

    def put(self, entries: Mapping[CacheKey, str]) -> None:
        if not entries:
            return
        with self._lock:
            merged = self._read() | (self._entries or {}) | dict(entries)
            self._entries = merged
            try:
                self._write(merged)
            except OSError as exc:
                _logger.warning("contract cache %s not written: %s", self.path, exc)

    def _loaded(self) -> dict[CacheKey, str]:
        if self._entries is None:
            self._entries = self._read()
        return self._entries

    def _read(self) -> dict[CacheKey, str]:
        try:
            raw = json.loads(self.path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return {}
        except (OSError, ValueError) as exc:
            _logger.warning("contract cache %s unreadable, ignored: %s", self.path, exc)
            return {}
        if not isinstance(raw, dict):
            _logger.warning(
                "contract cache %s is not a JSON object, ignored", self.path
            )
            return {}
        entries: dict[CacheKey, str] = {}
        for raw_key, value in raw.items():
            key = _decode(raw_key)
            if key is not None and isinstance(value, str):
                # A canonical entry wins over a 3.x one spelled otherwise.
                if key not in entries or _encode(key) == raw_key:
                    entries[key] = value
        return entries

    def _write(self, entries: dict[CacheKey, str]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        payload = json.dumps(
            {_encode(key): entries[key] for key in sorted(entries)}, indent=2
        )
        fd, tmp = tempfile.mkstemp(
            dir=self.path.parent, prefix=f".{self.path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(payload)
            os.replace(tmp, self.path)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise


def default_cache_path() -> Path:
    """``$XDG_CACHE_HOME/ipor-fusion/contract_cache.json``
    (``~/.cache/...`` when unset), resolved at call time."""
    home = Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))
    return home / "ipor-fusion" / "contract_cache.json"


_defaults: dict[Path, FileContractCache] = {}
_defaults_lock = threading.Lock()


def default_contract_cache() -> FileContractCache:
    """The process-wide `FileContractCache` at `default_cache_path`."""
    path = default_cache_path()
    with _defaults_lock:
        if (cache := _defaults.get(path)) is None:
            cache = _defaults[path] = FileContractCache(path)
        return cache


def cache_get(cache: ContractCache, keys: Iterable[CacheKey]) -> dict[CacheKey, str]:
    """``cache.get(keys)``; a cache that raises reads as all misses."""
    try:
        return cache.get(keys)
    except Exception as exc:
        _logger.warning("contract cache read failed, ignored: %s", exc)
        return {}


def cache_put(cache: ContractCache, entries: Mapping[CacheKey, str]) -> None:
    """``cache.put(entries)``; a cache that raises loses the entries."""
    try:
        cache.put(entries)
    except Exception as exc:
        _logger.warning("contract cache write failed, ignored: %s", exc)

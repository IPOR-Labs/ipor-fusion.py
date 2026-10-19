"""On-disk caches of immutable chain facts: contract names, token symbols and
decimals (``contract_cache.json``) and contract deployment blocks
(``deployment_cache.json``), under ``$XDG_CACHE_HOME/ipor-fusion/``."""

from __future__ import annotations

import json
import os
import threading
from pathlib import Path

from eth_typing import HexAddress


def _xdg_cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


CACHE_DIR = _xdg_cache_home() / "ipor-fusion"
CACHE_FILE = CACHE_DIR / "contract_cache.json"
DEPLOYMENT_CACHE_FILE = CACHE_DIR / "deployment_cache.json"

_cache_lock = threading.Lock()


def cache_key(scope: object, address: HexAddress) -> str:
    """``<scope>:<address>`` with the address lowercased: one entry per address
    whatever spelling the caller holds. ``scope`` is a chain id or a field name
    (``symbol``, ``decimals``)."""
    return f"{scope}:{address.lower()}"


def _canonical(entries: dict) -> dict:
    """Entries keyed by `cache_key` spelling. Caches written before 4.0 keyed
    the address as the caller spelled it; an entry already canonical wins over
    a differently spelled duplicate."""
    out: dict = {}
    for key, value in entries.items():
        scope, sep, address = key.rpartition(":")
        canonical = cache_key(scope, address) if sep else key
        if canonical == key or canonical not in out:
            out[canonical] = value
    return out


def _read(path: Path) -> dict:
    if not path.exists():
        return {}
    return _canonical(json.loads(path.read_text(encoding="utf-8")))


def load_contract_cache() -> dict[str, str]:
    with _cache_lock:
        return _read(CACHE_FILE)


def save_contract_cache(cache: dict[str, str]) -> None:
    with _cache_lock:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def update_contract_cache(key: str, value: str) -> None:
    with _cache_lock:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        existing: dict[str, str] = _read(CACHE_FILE)
        existing[key] = value
        CACHE_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")


_deployment_cache_lock = threading.Lock()


def load_deployment_cache() -> dict[str, dict[str, int]]:
    with _deployment_cache_lock:
        return _read(DEPLOYMENT_CACHE_FILE)


def update_deployment_cache(key: str, block: int, timestamp: int) -> None:
    with _deployment_cache_lock:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        existing: dict[str, dict[str, int]] = _read(DEPLOYMENT_CACHE_FILE)
        existing[key] = {"block": block, "timestamp": timestamp}
        DEPLOYMENT_CACHE_FILE.write_text(
            json.dumps(existing, indent=2), encoding="utf-8"
        )

from __future__ import annotations

import json
import os
import threading
from dataclasses import asdict, dataclass, field
from pathlib import Path


def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def _xdg_cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


CONFIG_DIR = _xdg_config_home() / "ipor-fusion"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_DIR = _xdg_cache_home() / "ipor-fusion"
CACHE_FILE = CACHE_DIR / "contract_cache.json"
DEPLOYMENT_CACHE_FILE = CACHE_DIR / "deployment_cache.json"


@dataclass
class VaultEntry:
    address: str
    label: str
    chain_id: int


@dataclass
class FusionConfig:
    providers: dict[str, str] = field(default_factory=dict)
    etherscan_api_key: str | None = None
    default_vault: str | None = None
    vaults: list[VaultEntry] = field(default_factory=list)


def load_config() -> FusionConfig:
    if not CONFIG_FILE.exists():
        return FusionConfig()
    data = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
    vaults = [VaultEntry(**v) for v in data.get("vaults", [])]
    return FusionConfig(
        providers=data.get("providers", {}),
        etherscan_api_key=data.get("etherscan_api_key"),
        default_vault=data.get("default_vault"),
        vaults=vaults,
    )


def save_config(config: FusionConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    data = asdict(config)
    CONFIG_FILE.write_text(json.dumps(data, indent=2), encoding="utf-8")


_cache_lock = threading.Lock()


def load_contract_cache() -> dict[str, str]:
    with _cache_lock:
        if not CACHE_FILE.exists():
            return {}
        return json.loads(CACHE_FILE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def save_contract_cache(cache: dict[str, str]) -> None:
    with _cache_lock:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


def update_contract_cache(key: str, value: str) -> None:
    with _cache_lock:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        existing: dict[str, str] = {}
        if CACHE_FILE.exists():
            existing = json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        existing[key] = value
        CACHE_FILE.write_text(json.dumps(existing, indent=2), encoding="utf-8")


_deployment_cache_lock = threading.Lock()


def load_deployment_cache() -> dict[str, dict[str, int]]:
    with _deployment_cache_lock:
        if not DEPLOYMENT_CACHE_FILE.exists():
            return {}
        return json.loads(DEPLOYMENT_CACHE_FILE.read_text(encoding="utf-8"))  # type: ignore[no-any-return]


def update_deployment_cache(key: str, block: int, timestamp: int) -> None:
    with _deployment_cache_lock:
        CACHE_DIR.mkdir(parents=True, exist_ok=True)
        existing: dict[str, dict[str, int]] = {}
        if DEPLOYMENT_CACHE_FILE.exists():
            existing = json.loads(DEPLOYMENT_CACHE_FILE.read_text(encoding="utf-8"))
        existing[key] = {"block": block, "timestamp": timestamp}
        DEPLOYMENT_CACHE_FILE.write_text(
            json.dumps(existing, indent=2), encoding="utf-8"
        )

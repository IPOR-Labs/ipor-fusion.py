from __future__ import annotations

import json
import os
import threading
from pathlib import Path

import click
from pydantic import BaseModel, Field, ValidationError, model_validator


def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


def _xdg_cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME", Path.home() / ".cache"))


CONFIG_DIR = _xdg_config_home() / "ipor-fusion"
CONFIG_FILE = CONFIG_DIR / "config.json"
CACHE_DIR = _xdg_cache_home() / "ipor-fusion"
CACHE_FILE = CACHE_DIR / "contract_cache.json"
DEPLOYMENT_CACHE_FILE = CACHE_DIR / "deployment_cache.json"

CONFIG_VERSION = 1


class VaultEntry(BaseModel):
    address: str
    label: str
    chain_id: int


class FusionConfig(BaseModel):
    providers: dict[str, str] = Field(default_factory=dict)
    etherscan_api_key: str | None = None
    vaults: list[VaultEntry] = Field(default_factory=list)
    version: int = CONFIG_VERSION

    @model_validator(mode="before")
    @classmethod
    def _migrate_legacy(cls, data: object) -> object:
        # Drops fields removed in earlier migrations; missing keys fall back to
        # the field defaults declared above.
        if isinstance(data, dict):
            data.pop("default_vault", None)
        return data


def load_config() -> FusionConfig:
    if not CONFIG_FILE.exists():
        return FusionConfig()
    try:
        return FusionConfig.model_validate_json(CONFIG_FILE.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise click.ClickException(
            f"Config {CONFIG_FILE} is invalid:\n{exc}\n"
            "Fix the file manually or delete it to start fresh."
        ) from exc
    except json.JSONDecodeError as exc:
        raise click.ClickException(
            f"Config {CONFIG_FILE} is not valid JSON: {exc}. "
            "Fix the file manually or delete it to start fresh."
        ) from exc


def save_config(config: FusionConfig) -> None:
    CONFIG_DIR.mkdir(parents=True, exist_ok=True)
    CONFIG_FILE.write_text(config.model_dump_json(indent=2), encoding="utf-8")


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

from __future__ import annotations

import json
import os
from pathlib import Path
from urllib.parse import urlsplit

import click
from pydantic import BaseModel, Field, ValidationError, model_validator

from ipor_fusion.types import ChainId


def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


CONFIG_DIR = _xdg_config_home() / "ipor-fusion"
CONFIG_FILE = CONFIG_DIR / "config.json"

CONFIG_VERSION = 1


class VaultEntry(BaseModel):
    address: str
    label: str
    chain_id: ChainId


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


def mask_provider_url(url: str) -> str:
    """Render a provider URL as scheme and host only.

    RPC provider URLs carry API keys in the path, the query string or the
    userinfo, so nothing but the scheme and the host (with port) may be
    displayed. An unparsable value is rendered as ``***``.
    """
    parts = urlsplit(url)
    host = parts.netloc.rpartition("@")[2]
    if not parts.scheme or not host:
        return "***"
    return f"{parts.scheme}://{host}"


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

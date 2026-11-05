from __future__ import annotations

import json
import os
from pathlib import Path

import click
from eth_typing import ChecksumAddress
from pydantic import BaseModel, Field, ValidationError, field_validator, model_validator
from web3 import Web3

from ipor_fusion.types import ChainId


def _xdg_config_home() -> Path:
    return Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))


CONFIG_DIR = _xdg_config_home() / "ipor-fusion"
CONFIG_FILE = CONFIG_DIR / "config.json"

CONFIG_VERSION = 1


class VaultEntry(BaseModel):
    address: ChecksumAddress
    label: str
    chain_id: ChainId

    @field_validator("address", mode="before")
    @classmethod
    def _checksum(cls, value: object) -> object:
        # Configs written before 4.0 may hold any spelling; a value that is not
        # an address is left for the commands to report, not for every load to fail.
        if isinstance(value, str) and Web3.is_address(value):
            return Web3.to_checksum_address(value)
        return value


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


def _describe_invalid_fields(exc: ValidationError) -> str:
    """Which fields failed and why, never their values: the file holds API keys."""
    return "\n".join(
        f"  {'.'.join(str(part) for part in error['loc'])}: {error['msg']}"
        for error in exc.errors(
            include_input=False, include_url=False, include_context=False
        )
    )


def load_config() -> FusionConfig:
    if not CONFIG_FILE.exists():
        return FusionConfig()
    try:
        return FusionConfig.model_validate_json(CONFIG_FILE.read_text(encoding="utf-8"))
    except ValidationError as exc:
        raise click.ClickException(
            f"Config {CONFIG_FILE} is invalid:\n{_describe_invalid_fields(exc)}\n"
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


def resolve_chain_id(
    cfg: FusionConfig, vault_address: str, chain_id: ChainId | None
) -> ChainId | None:
    """The chain a command or tool runs on: the one given, else the saved
    vault's, else the chain of the only configured provider; None when none
    applies. The CLI and the MCP server share it so both resolve alike."""
    if chain_id is not None:
        return chain_id
    if entry := next(
        (v for v in cfg.vaults if v.address.lower() == vault_address.lower()),
        None,
    ):
        return entry.chain_id
    if len(cfg.providers) == 1:
        return ChainId(int(next(iter(cfg.providers))))
    return None

from __future__ import annotations

import click
from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.chains import CHAIN_NAME_TO_ID, ensure_supported_chain
from ipor_fusion.cli.config_store import (
    FusionConfig,
    resolve_chain_id,
)
from ipor_fusion.core.context import Web3Context
from ipor_fusion.errors import (
    UnsupportedChainError,
)
from ipor_fusion.types import ChainId, PlasmaVaultAddress


# click < 8.4 does not make ParamType generic, so it cannot be subscripted.
class AddressType(click.ParamType):  # pyright: ignore[reportMissingTypeArgument]
    name = "address"

    def convert(
        self, value: object, param: click.Parameter | None, ctx: click.Context | None
    ) -> ChecksumAddress:
        if not isinstance(value, str):
            self.fail(f"expected string, got {type(value).__name__}", param, ctx)

        value = value.strip()
        if value[:2] in ("0x", "0X"):
            value = value[2:]
        value = "0x" + value

        raw = value[2:]
        if len(raw) != 40 or not all(c in "0123456789abcdefABCDEF" for c in raw):
            self.fail(f"invalid Ethereum address: {value}", param, ctx)

        if not raw.islower() and not raw.isupper():
            try:
                checksum = Web3.to_checksum_address(value)
            except Exception:
                self.fail(f"invalid address checksum: {value}", param, ctx)
            if checksum != value:
                self.fail(
                    f"bad checksum — expected {checksum}, got {value}",
                    param,
                    ctx,
                )

        return Web3.to_checksum_address(value)


ADDRESS = AddressType()


class VaultAddressType(AddressType):
    """An address the command treats as a Plasma Vault; the command's probe
    (`resolve_access_manager`) is what checks it on chain."""

    def convert(
        self, value: object, param: click.Parameter | None, ctx: click.Context | None
    ) -> PlasmaVaultAddress:
        return PlasmaVaultAddress(super().convert(value, param, ctx))


VAULT_ADDRESS = VaultAddressType()


# click < 8.4 does not make ParamType generic, so it cannot be subscripted.
class ChainType(click.ParamType):  # pyright: ignore[reportMissingTypeArgument]
    """Accepts chain ID as int or chain name (ethereum, base, arbitrum, ...)."""

    name = "chain"

    def convert(
        self, value: object, param: click.Parameter | None, ctx: click.Context | None
    ) -> ChainId:
        if isinstance(value, int):
            return self._positive(value, param, ctx)
        if not isinstance(value, str):
            self.fail(f"expected string, got {type(value).__name__}", param, ctx)
        try:
            return self._positive(int(value), param, ctx)
        except ValueError:
            pass
        lower = value.lower().strip()
        if lower in CHAIN_NAME_TO_ID:
            return ChainId(CHAIN_NAME_TO_ID[lower])
        self.fail(
            f"Unknown chain: {value}. Use a numeric ID or one of: "
            f"{', '.join(sorted(CHAIN_NAME_TO_ID))}",
            param,
            ctx,
        )

    def _positive(
        self, value: int, param: click.Parameter | None, ctx: click.Context | None
    ) -> ChainId:
        if value <= 0:
            self.fail(
                f"chain ID must be positive, got {value}; omit --chain-id to "
                "auto-detect it",
                param,
                ctx,
            )
        return ChainId(value)


CHAIN = ChainType()


class BlockNumberType(click.IntRange):
    """A block number, 0 meaning the latest block like an omitted option:
    callers fill optional integers with 0, and genesis never holds a vault."""

    name = "block"

    def __init__(self) -> None:
        super().__init__(min=0)

    def convert(
        self, value: object, param: click.Parameter | None, ctx: click.Context | None
    ) -> int | None:
        return super().convert(value, param, ctx) or None


BLOCK_NUMBER = BlockNumberType()


def _resolve_chain_id(
    cfg: FusionConfig, vault_address: PlasmaVaultAddress, chain_id: ChainId | None
) -> ChainId:
    if (resolved := resolve_chain_id(cfg, vault_address, chain_id)) is not None:
        return resolved
    raise click.UsageError(
        "Cannot tell the chain: the vault is not saved and there is not exactly one "
        "configured provider — use --chain-id (e.g. --chain-id ethereum, --chain-id 8453)"
    )


def _resolve_provider(cfg: FusionConfig, chain_id: ChainId) -> str:
    if provider_url := cfg.providers.get(str(chain_id)):
        return provider_url
    raise click.UsageError(
        f"No provider for chain {chain_id}. "
        f"Use 'fusion config set-provider {chain_id} <url>'"
    )


def _build_ctx(
    cfg: FusionConfig,
    vault_address: PlasmaVaultAddress,
    chain_id: ChainId | None,
    block_number: int | None,
) -> tuple[ChainId, Web3Context]:
    """Shared command preamble: resolve chain + provider, build the context."""
    chain_id = _resolve_chain_id(cfg, vault_address, chain_id)
    # Every _build_ctx command does heavy on-chain reads (vault fetch,
    # RoleGranted scan, asset-source event replay from block 0) — gate to
    # validated chains before even resolving a provider.
    _require_supported_chain(chain_id)
    provider_url = _resolve_provider(cfg, chain_id)
    ctx = Web3Context.from_url(provider_url)
    if block_number is not None:
        ctx = ctx.at_block(block_number)
    return chain_id, ctx


def _require_supported_chain(chain_id: ChainId) -> None:
    """UnsupportedChainError → UsageError, so the CLI prints a clean message
    instead of a traceback before any heavy on-chain work starts."""
    try:
        ensure_supported_chain(chain_id)
    except UnsupportedChainError as exc:
        raise click.UsageError(str(exc)) from exc

from __future__ import annotations

import json
import sys
from functools import partial
from typing import Any

import click
from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.chains import CHAIN_NAMES
from ipor_fusion.cli.config_store import (
    FusionConfig,
    VaultEntry,
    load_config,
    save_config,
)
from ipor_fusion.cli.options import ADDRESS, CHAIN, _build_ctx, _resolve_provider
from ipor_fusion.cli.oracle_mapping_view import _print_oracle_mapping
from ipor_fusion.cli.vault_health import (
    _print_validation,
)
from ipor_fusion.cli.vault_info_view import (
    _print_role_accounts_table,
    _print_vault_info,
)
from ipor_fusion.cli.vault_rendering import (
    _print_table,
)
from ipor_fusion.config.roles import Roles
from ipor_fusion.core.access import (
    resolve_access_manager,
    role_account_sort_key,
)
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.errors import (
    ContractNotFoundError,
    NotPlasmaVaultError,
    UnsupportedVaultVersionError,
)
from ipor_fusion.readers.oracle_mapping import (
    build_oracle_mapping,
)
from ipor_fusion.types import ChainId
from ipor_fusion.validation import (
    VaultValidation,
    validate_vault,
)
from ipor_fusion.vault_data import (
    _ROLE_SCAN_ERRORS,
    fetch_vault_data,
    resolve_token_symbol,
)
from ipor_fusion.vault_info import (
    _erc20_balances_json,
    _lending_health_json,
    _reconciliation_json,
    _unix_to_iso,
)


def _auto_save_vault(
    cfg: FusionConfig,
    vault_address: ChecksumAddress,
    chain_id: ChainId,
    plasma_vault: PlasmaVault,
) -> None:
    """Save vault to config if not already present and it looks like a Plasma Vault."""
    if any(v.address.lower() == vault_address.lower() for v in cfg.vaults):
        return
    try:
        label = plasma_vault.name().call()
    except Exception:
        return
    cfg.vaults.append(VaultEntry(address=vault_address, label=label, chain_id=chain_id))
    save_config(cfg)
    click.echo(f"Vault saved: {label} ({vault_address})", err=True)


@click.group()
def vault() -> None:
    """Inspect Plasma Vaults."""


@vault.command("add")
@click.argument("address", type=ADDRESS)
@click.option(
    "--label", default=None, help="Label (default: fetched from on-chain name())."
)
@click.option(
    "--chain-id",
    type=CHAIN,
    default=None,
    help="Chain ID or name (auto-detected when only one provider is configured).",
)
def add(address: str, label: str | None, chain_id: ChainId | None) -> None:
    """Save a vault to the config."""
    cfg = load_config()

    if chain_id is None:
        if len(cfg.providers) == 1:
            chain_id = ChainId(int(next(iter(cfg.providers))))
        else:
            raise click.UsageError(
                "Cannot auto-detect chain ID — multiple providers configured. "
                "Use --chain-id to specify."
            )

    if label is None:
        provider_url = _resolve_provider(cfg, chain_id)
        ctx = Web3Context.from_url(provider_url)
        checksum = Web3.to_checksum_address(address)
        try:
            label = PlasmaVault(ctx, checksum).name().call()
        except Exception:
            label = checksum

    for vault_entry in cfg.vaults:
        if vault_entry.address.lower() == address.lower():
            click.echo("Vault already exists, updating.")
            vault_entry.label = label
            vault_entry.chain_id = chain_id
            save_config(cfg)
            return
    cfg.vaults.append(VaultEntry(address=address, label=label, chain_id=chain_id))
    save_config(cfg)
    click.echo(f"Vault {label} ({address}) added.")


@vault.command("remove")
@click.argument("address", type=ADDRESS)
def remove(address: str) -> None:
    """Remove a saved vault."""
    cfg = load_config()
    before = len(cfg.vaults)
    cfg.vaults = [v for v in cfg.vaults if v.address.lower() != address.lower()]
    if len(cfg.vaults) == before:
        click.echo("Vault not found.")
        return
    save_config(cfg)
    click.echo("Vault removed.")


@vault.command("list")
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output as JSON."
)
def list_vaults(json_output: bool) -> None:
    """List saved vaults. (alias: ls)"""
    cfg = load_config()
    if not cfg.vaults:
        if json_output:
            click.echo("[]")
        else:
            click.echo("(no saved vaults)")
        return
    if json_output:
        entries = []
        for v in cfg.vaults:
            entries.append(
                {
                    "address": v.address,
                    "label": v.label,
                    "chain": CHAIN_NAMES.get(v.chain_id, str(v.chain_id)),
                    "chain_id": v.chain_id,
                }
            )
        click.echo(json.dumps(entries, indent=2))
        return
    rows: list[tuple[str, ...]] = []
    for v in cfg.vaults:
        chain = CHAIN_NAMES.get(v.chain_id, str(v.chain_id))
        rows.append((chain, v.label, v.address))
    _print_table(("Chain", "Label", "Address"), rows)


vault.add_command(list_vaults, "ls")


@vault.command("info")
@click.argument("vault_address", type=ADDRESS)
@click.option(
    "--chain-id", type=CHAIN, default=None, help="Chain ID or name (e.g. 1, ethereum)."
)
@click.option(
    "--block-number",
    type=int,
    default=None,
    help="Block number (default: latest).",
)
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output as JSON."
)
def info(
    vault_address: ChecksumAddress,
    chain_id: ChainId | None,
    block_number: int | None,
    json_output: bool,
) -> None:
    """Display full on-chain vault state.

    The comprehensive vault summary — includes all role accounts on the
    vault's AccessManager.
    """
    cfg = load_config()
    chain_id, ctx = _build_ctx(cfg, vault_address, chain_id, block_number)
    checksum_address = Web3.to_checksum_address(vault_address)

    # Cheap single-call probe: friendly errors for "nothing deployed here" and
    # "not a Plasma Vault" (revert and empty-return flavors alike) before the
    # expensive fetch — and before auto-save can store a non-vault.
    try:
        resolve_access_manager(ctx, vault_address)
    except (ContractNotFoundError, NotPlasmaVaultError) as exc:
        raise click.UsageError(str(exc)) from exc

    plasma_vault = PlasmaVault(ctx, checksum_address)

    _auto_save_vault(cfg, vault_address, chain_id, plasma_vault)

    try:
        data = fetch_vault_data(ctx, plasma_vault, block_number, chain_id=chain_id)
    except UnsupportedVaultVersionError as exc:
        raise click.ClickException(str(exc)) from exc
    _print_vault_info(
        ctx, plasma_vault, cfg, data, vault_address, chain_id, json_output
    )


@vault.command("validate")
@click.argument("vault_address", type=ADDRESS)
@click.option(
    "--chain-id", type=CHAIN, default=None, help="Chain ID or name (e.g. 1, ethereum)."
)
@click.option(
    "--block-number",
    type=int,
    default=None,
    help="Block number (default: latest).",
)
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output as JSON."
)
def validate(
    vault_address: ChecksumAddress,
    chain_id: ChainId | None,
    block_number: int | None,
    json_output: bool,
) -> None:
    """Validate a vault: health findings, totalAssets reconciliation and
    lending health, with an ok / warning / critical verdict.

    The same checks as the Health Check of `vault info`. Exits with status 1
    when the verdict is critical.
    """
    cfg = load_config()
    chain_id, ctx = _build_ctx(cfg, vault_address, chain_id, block_number)
    try:
        resolve_access_manager(ctx, vault_address)
    except (ContractNotFoundError, NotPlasmaVaultError) as exc:
        raise click.UsageError(str(exc)) from exc

    try:
        validation = validate_vault(
            ctx,
            vault_address,
            block_number,
            resolve_symbol=partial(resolve_token_symbol, ctx),
        )
    except UnsupportedVaultVersionError as exc:
        raise click.ClickException(str(exc)) from exc

    chain_label = CHAIN_NAMES.get(chain_id, str(chain_id))
    if json_output:
        click.echo(json.dumps(_validation_json(validation, chain_label), indent=2))
    else:
        click.echo(f"Vault:   {validation.vault}")
        click.echo(f"Chain:   {chain_label} (chain-id={chain_id})")
        block_iso = _unix_to_iso(validation.block_timestamp)
        click.echo(f"Block:   {validation.block_number} ({block_iso})")
        _print_validation(validation)
    if validation.verdict == "critical":
        sys.exit(1)


def _validation_json(validation: VaultValidation, chain_label: str) -> dict[str, Any]:
    return {
        "vault": validation.vault,
        "chain": chain_label,
        "chain_id": validation.chain_id,
        "block": validation.block_number,
        "block_timestamp": validation.block_timestamp,
        "block_timestamp_utc": _unix_to_iso(validation.block_timestamp),
        "asset": {
            "address": validation.asset,
            "symbol": validation.asset_symbol,
            "decimals": validation.asset_decimals,
            "price_usd": validation.asset_price_usd,
        },
        "verdict": validation.verdict,
        "criticals": list(validation.criticals),
        "warnings": list(validation.warnings),
        "ok": list(validation.ok),
        "findings": [
            {"check": f.check, "severity": f.severity, "message": f.message}
            for f in validation.findings
        ],
        "reconciliation": _reconciliation_json(
            validation.reconciliation, validation.asset_decimals
        ),
        "erc20_balances": _erc20_balances_json(validation),
        "lending_health": _lending_health_json(validation.lending_health),
    }


@vault.command("role-accounts")
@click.argument("vault_address", type=ADDRESS)
@click.option(
    "--role",
    default="",
    help="Filter to one role (case-insensitive, '_ROLE' suffix optional); "
    f"omit to list all. Valid: {Roles.names_str()}.",
)
@click.option(
    "--chain-id", type=CHAIN, default=None, help="Chain ID or name (e.g. 1, ethereum)."
)
@click.option(
    "--block-number",
    type=int,
    default=None,
    help="Block number (default: latest).",
)
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output as JSON."
)
def role_accounts(
    vault_address: ChecksumAddress,
    role: str,
    chain_id: ChainId | None,
    block_number: int | None,
    json_output: bool,
) -> None:
    """List confirmed role holders on the vault's AccessManager."""
    # Pure input validation first — no RPC needed to reject a bad --role.
    try:
        role_id = None if not role.strip() else Roles.resolve(role)
    except ValueError as exc:
        raise click.UsageError(str(exc)) from exc

    cfg = load_config()
    chain_id, ctx = _build_ctx(cfg, vault_address, chain_id, block_number)

    try:
        manager = resolve_access_manager(ctx, vault_address)
    except (ContractNotFoundError, NotPlasmaVaultError) as exc:
        raise click.UsageError(str(exc)) from exc

    try:
        accounts = (
            manager.get_all_role_accounts()
            if role_id is None
            else manager.get_accounts_with_role(role_id)
        )
    except _ROLE_SCAN_ERRORS as exc:
        raise click.ClickException(
            f"RoleGranted log scan failed ({type(exc).__name__}: {exc})."
        ) from exc
    rows = [ra.to_dict() for ra in sorted(accounts, key=role_account_sort_key)]

    if json_output:
        payload = {
            "vault": Web3.to_checksum_address(vault_address),
            "access_manager": manager.address,
            "chain_id": chain_id,
            "role_filter": Roles.get_name(role_id) if role_id is not None else None,
            "accounts": rows,
        }
        click.echo(json.dumps(payload, indent=2))
        return

    click.echo(f"Access Manager: {manager.address}")
    _print_role_accounts_table(rows)


@vault.command("oracle-mapping")
@click.argument("vault_address", type=ADDRESS)
@click.option(
    "--chain-id", type=CHAIN, default=None, help="Chain ID or name (e.g. 1, ethereum)."
)
@click.option(
    "--block-number",
    type=click.IntRange(min=0),
    default=None,
    help="Block number (default: latest).",
)
@click.option(
    "--max-depth",
    type=click.IntRange(min=0),
    default=6,
    show_default=True,
    help="Max recursion depth for feeds derived from other assets.",
)
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output as JSON."
)
def oracle_mapping(
    vault_address: ChecksumAddress,
    chain_id: ChainId | None,
    block_number: int | None,
    max_depth: int,
    json_output: bool,
) -> None:
    """Map how the vault prices every configured asset at a block.

    For each asset the vault's price oracle knows about, resolves the source
    price feed, classifies its type by interface probing (dual cross-reference
    / Chainlink / ERC4626 / Morpho collateral / unknown) and recursively
    follows feeds that derive their price from another asset. Zero-source
    assets priced through the oracle's underlying middleware are reported as
    middleware fallback, not unresolved. Unknown feeds are reported as
    partial, never dropped. Chainlink leaves are graded: ChainlinkAggregator
    when the full aggregator interface answers with sane metadata,
    chainlink_style when the feed merely quacks like one — verify the
    address yourself if identity matters. Classification is heuristic: it
    grades on-chain evidence, it cannot prove a contract's identity.

    Aggregator-compatible feeds report description() and the full
    latestRoundData round in source_detail (see --json); raw values only,
    no staleness judgment. The human output shows the description — the
    feed's own statement of its units — when the feed provides one.

    Node status:

    \b
    - resolved: own feed explained and every dependency resolved
    - partially_resolved: some descendant is not resolved
    - partial: the node's own resolution is incomplete (see the reason)

    The unresolved summary lists partial nodes only; the mapping-level
    status rolls up the roots (resolved / partially_resolved / unresolved).
    Historical blocks require an archive node.
    """
    cfg = load_config()
    chain_id, ctx = _build_ctx(cfg, vault_address, chain_id, block_number)

    # The event-replay fallback and the output both need a concrete block
    # number, so resolve "latest" up front and pin the context to it.
    effective_block = (
        block_number if block_number is not None else ctx.web3.eth.block_number
    )
    ctx.default_block = effective_block

    try:
        resolve_access_manager(ctx, vault_address)
    except (ContractNotFoundError, NotPlasmaVaultError) as exc:
        raise click.UsageError(str(exc)) from exc

    try:
        mapping = build_oracle_mapping(
            ctx, Web3.to_checksum_address(vault_address), effective_block, max_depth
        )
    except UnsupportedVaultVersionError as exc:
        raise click.ClickException(str(exc)) from exc

    if json_output:
        click.echo(json.dumps(mapping.to_dict(), indent=2))
        return
    _print_oracle_mapping(mapping)

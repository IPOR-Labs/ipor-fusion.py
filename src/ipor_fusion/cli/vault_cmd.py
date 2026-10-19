from __future__ import annotations

import json
import sys
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial
from typing import Any

import click
from web3 import Web3

from ipor_fusion.chains import CHAIN_NAME_TO_ID, CHAIN_NAMES, ensure_supported_chain
from ipor_fusion.cli.config_store import (
    FusionConfig,
    VaultEntry,
    load_config,
    save_config,
)
from ipor_fusion.cli.vault_health import (
    _print_erc20_balances,
    _print_health_check,
    _print_reconciliation,
    _print_validation,
    _read_erc20_holdings,
)
from ipor_fusion.cli.vault_rendering import (
    _format_age,
    _format_amount,
    _format_remaining,
    _format_usd,
    _print_table,
    _substrate_details,
)
from ipor_fusion.config.roles import Roles
from ipor_fusion.core.access import (
    resolve_access_manager,
    role_account_sort_key,
)
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.fee_manager import RecipientFee
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.errors import (
    ContractNotFoundError,
    NotPlasmaVaultError,
    UnsupportedChainError,
    UnsupportedVaultVersionError,
)
from ipor_fusion.explorer import get_contract_name
from ipor_fusion.readers.hypercore import HyperCorePendingState
from ipor_fusion.readers.oracle_mapping import (
    TYPE_CHAINLINK,
    TYPE_CHAINLINK_STYLE,
    TYPE_DUAL_XREF,
    WAD_DECIMALS,
    OracleMapping,
    OracleNode,
    OraclePrice,
    build_oracle_mapping,
)
from ipor_fusion.substrates import (
    decode_substrate,
    format_market_label,
)
from ipor_fusion.validation import (
    VaultValidation,
    validate_vault,
    validate_vault_state,
)
from ipor_fusion.vault_data import (
    _ROLE_SCAN_ERRORS,
    _ZERO_ADDRESS,
    GET_MARKET_SUBSTRATES,
    VaultData,
    _safe_call,
    fetch_deployment_info,
    fetch_vault_data,
    resolve_token_decimals,
    resolve_token_symbol,
)
from ipor_fusion.vault_info import (
    _CAPABILITY_BALANCE_FUSES,
    BLOCK_EXPLORER_URLS,
    IPOR_APP_URL,
    UINT256_MAX,
    _bps_to_percent,
    _erc20_balances_json,
    _lending_health_json,
    _reconciliation_json,
    _role_accounts_json,
    _unix_to_iso,
    _wad_to_percent,
    _wad_to_usd,
    build_vault_info_json,
)


class AddressType(click.ParamType):
    name = "address"

    def convert(self, value, param, ctx):
        if not isinstance(value, str):
            self.fail(f"expected string, got {type(value).__name__}", param, ctx)

        value = value.strip()
        if not value.startswith("0x"):
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


class ChainType(click.ParamType):
    """Accepts chain ID as int or chain name (ethereum, base, arbitrum, ...)."""

    name = "chain"

    def convert(self, value, param, ctx):  # type: ignore[override]
        if isinstance(value, int):
            return value
        try:
            return int(value)
        except ValueError:
            pass
        lower = value.lower().strip()
        if lower in CHAIN_NAME_TO_ID:
            return CHAIN_NAME_TO_ID[lower]
        self.fail(
            f"Unknown chain: {value}. Use a numeric ID or one of: "
            f"{', '.join(sorted(CHAIN_NAME_TO_ID))}",
            param,
            ctx,
        )
        return None  # unreachable, keeps mypy happy


CHAIN = ChainType()


def _resolve_chain_id(
    cfg: FusionConfig, vault_address: str, chain_id: int | None
) -> int:
    if chain_id is not None:
        return chain_id
    if entry := next(
        (v for v in cfg.vaults if v.address.lower() == vault_address.lower()),
        None,
    ):
        return entry.chain_id
    raise click.UsageError(
        "Unknown vault — use --chain-id (e.g. --chain-id ethereum, --chain-id 8453)"
    )


def _resolve_provider(cfg: FusionConfig, chain_id: int) -> str:
    if provider_url := cfg.providers.get(str(chain_id)):
        return provider_url
    raise click.UsageError(
        f"No provider for chain {chain_id}. "
        f"Use 'fusion config set-provider {chain_id} <url>'"
    )


def _build_ctx(
    cfg: FusionConfig,
    vault_address: str,
    chain_id: int | None,
    block_number: int | None,
) -> tuple[int, Web3Context]:
    """Shared command preamble: resolve chain + provider, build the context."""
    chain_id = _resolve_chain_id(cfg, vault_address, chain_id)
    # Every _build_ctx command does heavy on-chain reads (vault fetch,
    # RoleGranted scan, asset-source event replay from block 0) — gate to
    # validated chains before even resolving a provider.
    _require_supported_chain(chain_id)
    provider_url = _resolve_provider(cfg, chain_id)
    ctx = Web3Context.from_url(provider_url)
    if block_number is not None:
        ctx.default_block = block_number
    return chain_id, ctx


def _require_supported_chain(chain_id: int) -> None:
    """UnsupportedChainError → UsageError, so the CLI prints a clean message
    instead of a traceback before any heavy on-chain work starts."""
    try:
        ensure_supported_chain(chain_id)
    except UnsupportedChainError as exc:
        raise click.UsageError(str(exc)) from exc


def _auto_save_vault(
    cfg: FusionConfig, vault_address: str, chain_id: int, plasma_vault: PlasmaVault
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
def add(address: str, label: str | None, chain_id: int | None) -> None:
    """Save a vault to the config."""
    cfg = load_config()

    if chain_id is None:
        if len(cfg.providers) == 1:
            chain_id = int(next(iter(cfg.providers)))
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
    vault_address: str,
    chain_id: int | None,
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
    vault_address: str,
    chain_id: int | None,
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
    vault_address: str,
    role: str,
    chain_id: int | None,
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
    vault_address: str,
    chain_id: int | None,
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


def _format_wad_price(price: OraclePrice | None) -> str:
    if price is None:
        return "N/A"
    return _format_amount(int(price.normalized_wad), WAD_DECIMALS)


def _print_feed_line(node: OracleNode) -> None:
    """The feed's self-declared units — key for confirming what a value means."""
    detail = node.source_detail or {}
    if node.source_type in (TYPE_CHAINLINK, TYPE_CHAINLINK_STYLE):
        if detail.get("description"):
            click.echo(f"    Feed:   {detail['description']}")
        else:
            # only chainlink_style can land here — the confirmed tier
            # requires a non-empty description
            click.secho("    Feed:   (no description)", fg="yellow")
    elif node.source_type == TYPE_DUAL_XREF:
        rendered = [
            # quoted — the descriptions themselves contain " / "
            f'"{d}"' if d else click.style("(no description)", fg="yellow")
            for d in (
                (detail.get(key) or {}).get("description")
                for key in ("asset_x_asset_y_feed", "asset_y_usd_feed")
            )
        ]
        click.echo(f"    Feed:   {' × '.join(rendered)}")


def _print_oracle_node(node: OracleNode) -> None:
    click.echo(f"  {node.symbol or '?'} ({node.asset})")
    click.echo(f"    Path:   {' → '.join(node.path)}")
    _print_feed_line(node)
    click.echo(f"    Price:  {_format_wad_price(node.price)}")
    if node.status == "resolved":
        click.secho("    Status: resolved", fg="green")
    elif node.status == "partially_resolved":
        click.secho("    Status: partially_resolved", fg="yellow")
    else:
        click.secho(f"    Status: partial ({node.reason})", fg="yellow")


def _print_oracle_mapping(mapping: OracleMapping) -> None:
    name_suffix = f" ({mapping.vault_name})" if mapping.vault_name else ""
    click.echo(f"Vault:        {mapping.vault}{name_suffix}")
    click.echo(f"Underlying:   {mapping.asset.symbol or '?'} ({mapping.asset.address})")
    click.echo(f"Price Oracle: {mapping.price_oracle}")
    click.echo(f"Block:        {mapping.block_number}")
    click.echo(f"Enumerated:   {mapping.asset_source}")
    status_color = {"resolved": "green", "partially_resolved": "yellow"}.get(
        mapping.status, "red"
    )
    click.secho(f"Status:       {mapping.status}", fg=status_color)
    click.echo()
    click.echo(f"Configured assets ({len(mapping.configured_assets)}):")
    for node in mapping.configured_assets:
        _print_oracle_node(node)
    click.echo()
    if not mapping.unresolved:
        click.echo("Unresolved: 0")
        return
    click.secho(f"Unresolved: {len(mapping.unresolved)}", fg="yellow")
    for node in mapping.unresolved:
        click.secho(
            f"  {node.symbol or '?'} ({node.asset}): {node.reason}", fg="yellow"
        )


def _print_lending_health(  # noqa: C901
    ctx: Web3Context, data: VaultData
) -> None:
    lh = data.lending_health
    has_health = lh is not None and lh.has_lending_positions
    has_breakdown = bool(data.morpho_positions) or bool(data.aave_positions)
    if not has_health and not has_breakdown:
        click.echo("Position Breakdown: (no lending positions)")
        return

    click.echo("Position Breakdown:")
    morpho_health, aave_health = _index_lending_health(lh)
    consumed_morpho_subs: set[str] = set()
    consumed_aave_mids: set[int | str] = set()
    prices = data.token_prices_usd or {}

    for ipor_mid, positions in (data.morpho_positions or {}).items():
        click.echo(f"  {format_market_label(ipor_mid)}:")
        for pb in positions:
            coll_sym = resolve_token_symbol(ctx, pb.collateral_token) or "?"
            loan_sym = resolve_token_symbol(ctx, pb.loan_token) or "?"
            click.echo(f"    morpho market 0x{pb.market_id} ({coll_sym}/{loan_sym}):")
            click.echo(
                f"      Collateral:    {_format_token_amount(ctx, int(pb.collateral), pb.collateral_token, prices)}"
            )
            click.echo(
                f"      Borrow:        {_format_token_amount(ctx, int(pb.borrow_assets), pb.loan_token, prices)}"
            )
            click.echo(
                f"      Supply:        {_format_token_amount(ctx, int(pb.supply_assets), pb.loan_token, prices)}"
            )
            sid = str(pb.market_id).lower().removeprefix("0x")
            consumed_morpho_subs.add(sid)
            if m := morpho_health.get(sid):
                _print_health_lines(m, indent="      ")

    for ipor_mid, aave_positions in (data.aave_positions or {}).items():
        click.echo(f"  {format_market_label(ipor_mid)}:")
        for ab in aave_positions:
            asset_symbol = resolve_token_symbol(ctx, ab.asset) or "?"
            click.echo(f"    asset {ab.asset} ({asset_symbol}):")
            click.echo(
                f"      Supply:        {_format_token_amount(ctx, int(ab.supply), ab.asset, prices)}"
            )
            click.echo(
                f"      Variable Debt: {_format_token_amount(ctx, int(ab.variable_debt), ab.asset, prices)}"
            )
            if ab.stable_debt > 0:
                click.echo(
                    f"      Stable Debt:   {_format_token_amount(ctx, int(ab.stable_debt), ab.asset, prices)}"
                )
        consumed_aave_mids.add(ipor_mid)
        if m := aave_health.get(ipor_mid):
            _print_health_lines(m, indent="    ")

    # Orphan health rows (no breakdown matched — e.g. read failure or supply-only)
    for sid, m in morpho_health.items():
        if sid not in consumed_morpho_subs:
            click.echo(f"  {format_market_label(m.market_id)}:")
            click.echo(f"    morpho market 0x{sid}:")
            _print_health_lines(m, indent="      ")
    for key, m in aave_health.items():
        if key not in consumed_aave_mids:
            click.echo(f"  {format_market_label(m.market_id)}:")
            if m.protocol == "aave_v4":
                click.echo(f"    spoke {m.substrate_id}:")
            _print_health_lines(m, indent="    ")


def _index_lending_health(
    lh: Any,
) -> tuple[dict[str, Any], dict[int | str, Any]]:
    """Split lending health rows by protocol for fast lookup during rendering.

    Morpho rows are keyed by morpho substrate id (one row per substrate).
    Aave V3 rows are keyed by IPOR market id (account-aggregated, one row per
    market); Aave V4 rows by Spoke address (account-aggregated per Spoke).
    """
    morpho: dict[str, Any] = {}
    aave: dict[int | str, Any] = {}
    if lh is None:
        return morpho, aave
    for m in lh.markets:
        if m.protocol == "morpho" and m.substrate_id:
            morpho[str(m.substrate_id).lower().removeprefix("0x")] = m
        elif m.protocol == "aave_v3":
            aave[m.market_id] = m
        elif m.protocol == "aave_v4" and m.substrate_id:
            aave[m.substrate_id] = m
    return morpho, aave


def _print_health_lines(market: Any, indent: str) -> None:
    """Render LTV / Health Factor / Status lines for a lending market.

    Status colors: green when safe, yellow at warning (HF < 1.10), red+bold
    at critical (HF <= 1.05). The status line includes a short qualifier so
    "OK" is not ambiguous (e.g. "OK (no debt)" when there's nothing to
    liquidate, "OK (safe — HF > 1.10)" otherwise).
    """
    ltv_str = f"{market.current_ltv:.4f}" if market.current_ltv is not None else "N/A"
    max_str = f"{market.max_ltv:.4f}"
    usage_str = (
        f"{market.ltv_usage_percent:.1f}%"
        if market.ltv_usage_percent is not None
        else "N/A"
    )
    hf_str = (
        f"{market.health_factor:.4f}" if market.health_factor is not None else "N/A"
    )

    if market.is_critical:
        status, color, bold = "CRITICAL (near liquidation — HF ≤ 1.05)", "red", True
    elif market.is_warning:
        status, color, bold = (
            "WARNING (approaching liquidation — HF < 1.10)",
            "yellow",
            False,
        )
    elif market.health_factor is None:
        status, color, bold = "OK (no debt)", "green", False
    else:
        status, color, bold = "OK (safe — HF > 1.10)", "green", False

    click.echo(f"{indent}LTV:           {ltv_str} / {max_str} ({usage_str})")
    click.secho(f"{indent}Health Factor: {hf_str}", fg=color, bold=bold)
    click.secho(f"{indent}Status:        {status}", fg=color, bold=bold)


def _print_role_accounts_table(role_accounts: list[dict[str, Any]]) -> None:
    _print_table(
        ("Account", "Role", "Role ID", "Delay (s)"),
        [
            (
                entry["account"],
                entry["role_name"],
                str(entry["role_id"]),
                str(entry["execution_delay"]),
            )
            for entry in role_accounts
        ],
    )


def _print_role_accounts(role_accounts: list[dict[str, Any]] | None) -> None:
    click.echo("Role Accounts:")
    if role_accounts is None:
        click.echo("  (unavailable — the RoleGranted log scan did not complete)")
    else:
        _print_role_accounts_table(role_accounts)
    click.echo()


def _print_vault_info(
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    cfg: FusionConfig,
    data: VaultData,
    vault_address: str,
    chain_id: int,
    json_output: bool = False,
) -> None:
    api_key = cfg.etherscan_api_key
    chain_label = CHAIN_NAMES.get(chain_id, str(chain_id))

    (
        data.deployment_block,
        data.deployment_timestamp,
        data.deployment_error,
    ) = fetch_deployment_info(ctx, chain_id, vault_address, api_key)

    if json_output:
        result = build_vault_info_json(
            ctx,
            plasma_vault,
            data,
            vault_address,
            chain_id,
            chain_label,
            resolve_name=partial(get_contract_name, chain_id, api_key=api_key),
        )
        click.echo(json.dumps(result, indent=2))
        return

    _print_identity(data, vault_address, chain_id, chain_label)
    _print_supply(data)
    _print_managers(data, plasma_vault)
    click.echo()

    _print_fees(data)
    click.echo()

    _print_role_accounts(_role_accounts_json(data))

    market_balances = _print_markets(ctx, plasma_vault, data, chain_id, api_key)

    validation = validate_vault_state(
        ctx,
        plasma_vault,
        data,
        market_balances,
        _read_erc20_holdings(ctx, plasma_vault, data),
    )
    click.echo("ERC20 Balances (vault holdings):")
    _print_erc20_balances(data, validation.erc20)
    click.echo()

    _print_reconciliation(data, validation)
    click.echo()

    _print_lending_health(ctx, data)
    click.echo()
    _print_hypercore(data)

    _print_health_check(validation)


def _print_markets(
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    data: VaultData,
    chain_id: int,
    api_key: str | None,
) -> dict[int, int]:
    """Fuses, balance fuses, dependency graph and substrates; returns the
    per-market balances read for the balance-fuse table."""
    _print_fuse_section(
        "Fuses",
        data.fuses,
        data.fuse_markets,
        data.market_substrates,
        chain_id,
        api_key,
    )
    click.echo()

    _print_fuse_section(
        "Instant Withdrawal Fuses",
        data.instant_fuses,
        data.fuse_markets,
        data.market_substrates,
        chain_id,
        api_key,
    )
    click.echo()

    market_balances = _print_balance_fuses_table(
        plasma_vault,
        data.balance_fuses,
        data.asset_decimals,
        data.asset_symbol,
        data.asset_price_usd,
        chain_id,
        api_key,
    )
    click.echo()

    _print_dependency_graph(data)

    click.echo("Substrates per Market:")
    _print_market_substrates(ctx, data, chain_id, api_key)
    click.echo()
    return market_balances


def _print_identity(
    data: VaultData, vault_address: str, chain_id: int, chain_label: str
) -> None:
    name_suffix = f" ({data.vault_name})" if data.vault_name else ""
    click.echo(f"Vault:            {vault_address}{name_suffix}")
    if explorer_base := BLOCK_EXPLORER_URLS.get(chain_id):
        click.echo(f"Etherscan:        {explorer_base}/address/{vault_address}")
    click.echo(f"IPOR app:         {IPOR_APP_URL}/{chain_label}/{vault_address}")
    click.echo(f"Chain:            {chain_label} (chain-id={chain_id})")
    block_suffix = " (latest)" if data.is_latest else ""
    click.echo(f"Block:            {data.block_number}{block_suffix}")
    block_iso = _unix_to_iso(data.block_timestamp)
    click.echo(f"Block time:       {data.block_timestamp} ({block_iso})")
    if data.deployment_block is not None and data.deployment_timestamp is not None:
        deploy_iso = _unix_to_iso(data.deployment_timestamp)
        age = _format_age(data.deployment_timestamp)
        click.echo(
            f"Deployed at:      block {data.deployment_block} ({deploy_iso}, {age})"
        )
    elif data.deployment_error:
        click.echo(f"Deployed at:      N/A ({data.deployment_error})")
    else:
        click.echo("Deployed at:      N/A")


def _print_supply(data: VaultData) -> None:
    total_assets_usd = _format_usd(
        data.total_assets, data.asset_decimals, data.asset_price_usd
    )
    click.echo(f"Asset:            {data.asset} ({data.asset_symbol})")
    click.echo(f"Asset decimals:   {data.asset_decimals}")
    click.echo(f"Share decimals:   {data.share_decimals}")
    if data.asset_price_usd is not None:
        click.echo(f"Asset price:      ${data.asset_price_usd:,.2f}")
    else:
        click.echo("Asset price:      N/A")
    click.echo(
        f"Total Assets:     "
        f"{_format_amount(data.total_assets, data.asset_decimals)} "
        f"{data.asset_symbol}{total_assets_usd}"
    )
    click.echo(
        f"Total Supply:     "
        f"{_format_amount(data.total_supply, data.share_decimals)} shares"
    )
    if data.total_supply > 0:
        share_price = (data.total_assets / 10**data.asset_decimals) / (
            data.total_supply / 10**data.share_decimals
        )
        share_price_usd = (
            f" (${share_price * data.asset_price_usd:,.6f})"
            if data.asset_price_usd is not None
            else ""
        )
        click.echo(
            f"Share Price:      {share_price:,.6f} {data.asset_symbol}{share_price_usd}"
        )
    if data.supply_cap == UINT256_MAX:
        click.echo("Supply Cap:       unlimited")
    else:
        click.echo(
            f"Supply Cap:       "
            f"{_format_amount(data.supply_cap, data.asset_decimals)} {data.asset_symbol}"
        )


def _print_managers(data: VaultData, plasma_vault: PlasmaVault) -> None:
    click.echo(f"Access Manager:   {data.access_manager}")
    click.echo(f"Price Oracle:     {data.price_oracle_addr}")
    click.echo(f"Rewards Manager:  {data.rewards_manager or 'N/A'}")
    click.echo(f"Withdraw Manager: {data.withdraw_manager or 'N/A'}")
    if data.withdraw_manager_data:
        wmd = data.withdraw_manager_data
        window_h = wmd.withdraw_window / 3600
        click.echo(f"  Window:         {wmd.withdraw_window}s ({window_h:.1f}h)")
        click.echo('  Fees:           see the "Fees" section below')
        release_fmt = _format_amount(wmd.shares_to_release, data.share_decimals)
        click.echo(f"  Shares to release: {release_fmt}")
        if wmd.last_release_funds_timestamp > 0:
            last_iso = _unix_to_iso(wmd.last_release_funds_timestamp)
            click.echo(f"  Last release:   {last_iso}")
        _print_pending_requests(data, plasma_vault)


def _format_fee_percent(value: float | None) -> str:
    if value is None:
        return "N/A"
    # Four decimals would render a small-but-real fee as a flat 0.0000%, which
    # reads as "free". Flag it instead; --json carries the exact raw value.
    if 0 < value < 0.0001:
        return "<0.0001%"
    return f"{value:.4f}%"


def _print_fee_with_recipients(
    label: str,
    charged_bps: int | None,
    total_bps: int | None,
    recipients: list[RecipientFee] | None,
) -> None:
    charged = _bps_to_percent(charged_bps)
    total = _bps_to_percent(total_bps)
    line = f"  {label}: {_format_fee_percent(charged)}"
    # Vault and FeeManager should agree; only the disagreement is worth
    # printing, since it means the two configurations have drifted. A missing
    # vault-side value is a failed read, not drift, so it must not flag.
    if charged is not None and total is not None and total != charged:
        line += f"   (FeeManager total: {_format_fee_percent(total)} - MISMATCH)"
    click.echo(line)
    if recipients:
        click.echo("    recipients:")
    for recipient_fee in recipients or []:
        share = _format_fee_percent(_bps_to_percent(recipient_fee.fee_value))
        click.echo(f"      {recipient_fee.recipient}  {share}")


def _print_fees(data: VaultData) -> None:
    """Human rendering of the `fees` JSON object.

    Labels follow the IPOR app's Fees And Limits table so the CLI and the app
    name the same thing the same way; the on-chain getter is shown alongside
    because the JSON keys mirror the contracts, not the app.
    """
    if (fees := data.fee_data) is None:
        return
    wmd = data.withdraw_manager_data

    click.echo("Fees:")
    click.echo(f"  Fee Manager:      {fees.fee_manager or 'N/A'}")
    dao_payee = fees.ipor_dao_fee_recipient
    if not dao_payee or dao_payee == _ZERO_ADDRESS:
        dao_payee = "N/A"
    click.echo(f"  IPOR DAO payee:   {dao_payee}")
    onboarding_fee = _format_fee_percent(_wad_to_percent(fees.deposit_fee_wad))
    instant_fee = _format_fee_percent(
        _wad_to_percent(wmd.withdraw_fee) if wmd else None
    )
    scheduled_fee = _format_fee_percent(
        _wad_to_percent(wmd.request_fee) if wmd else None
    )
    click.echo(f"  Onboarding Contribution (depositFee): {onboarding_fee}")
    click.echo("  Offboarding Contribution - charged on one path only, never both:")
    click.echo(f"    for Instant Withdrawals (withdrawFee):   {instant_fee}")
    click.echo(
        f"    for Scheduled Withdrawals (requestFee):  "
        f"{scheduled_fee}   charged at request time"
    )

    _print_fee_with_recipients(
        "Performance Fee",
        fees.performance_fee_vault_bps,
        fees.performance_fee_manager_bps,
        fees.performance_fee_recipients,
    )
    if hwm := fees.high_water_mark:
        hwm_line = f"    high-water mark: {hwm.high_water_mark}"
        if hwm.last_update > 0:
            hwm_line += f" (updated {_unix_to_iso(hwm.last_update)})"
        else:
            # 0 is the contract's "never" sentinel, common on a fresh vault.
            hwm_line += " (never updated)"
        click.echo(hwm_line)

    _print_fee_with_recipients(
        "Management Fee",
        fees.management_fee_vault_bps,
        fees.management_fee_manager_bps,
        fees.management_fee_recipients,
    )
    if fees.unrealized_management_fee is not None:
        accrued = _format_amount(fees.unrealized_management_fee, data.asset_decimals)
        click.echo(f"    accrued, uncollected: {accrued} {data.asset_symbol}")


def _format_token_amount(
    ctx: Web3Context,
    raw: int,
    token_address: str,
    prices_usd: dict[str, float] | None = None,
) -> str:
    """Format a raw on-chain amount using cached ERC-20 symbol + decimals.

    When `prices_usd` (lowercase address → USD price) contains the token, the
    output is suffixed with ` ($X.XX)` via `_format_usd`.
    """
    symbol = resolve_token_symbol(ctx, token_address) or "?"
    decimals = resolve_token_decimals(ctx, token_address)
    if decimals is None:
        return f"{raw} raw ({symbol})"
    base = f"{_format_amount(raw, decimals)} {symbol}"
    if prices_usd:
        usd_suffix = _format_usd(raw, decimals, prices_usd.get(token_address.lower()))
        return f"{base}{usd_suffix}"
    return base


def _print_dependency_graph(data: VaultData) -> None:
    if not data.dependency_graph:
        return

    from ipor_fusion.dependency_graph import (
        compute_update_reach,
    )

    click.echo("Dependency Balance Graph:")
    for market_id, deps in data.dependency_graph.items():
        label = format_market_label(market_id)
        dep_labels = ", ".join(format_market_label(d) for d in deps)
        click.echo(f"  {label} → {dep_labels}")
    click.echo()

    reach = compute_update_reach(data.dependency_graph)
    if reach:
        click.echo("  Update reach (calling updateMarketsBalances for root market):")
        for market_id, reachable in sorted(reach.items(), key=lambda kv: -len(kv[1])):
            label = format_market_label(market_id)
            targets = ", ".join(format_market_label(r) for r in sorted(reachable))
            click.echo(f"    {label} refreshes: {targets}")
        click.echo()


def _print_hypercore(data: VaultData) -> None:
    state = data.hypercore
    if state is None:
        return
    nav = state.nav
    click.echo(f"HyperCore ({format_market_label(state.market_id)}):")
    if not nav.core_user_exists:
        click.echo(
            "  Core account: none (NAV is 0 until the first EVM -> Core deposit)"
        )
    if state.balance_fuse_value_wad is None:
        fuse_text = "unavailable"
    else:
        verdict = "match" if state.nav_matches_balance_fuse else "MISMATCH"
        fuse_text = f"{_wad_to_usd(state.balance_fuse_value_wad):.6f} USD ({verdict})"
    click.echo(
        f"  NAV: {_wad_to_usd(nav.value_wad):.6f} USD | balance fuse: {fuse_text}"
    )
    for leg in nav.spot:
        price = f"{leg.price.readable():.4f} USD" if leg.price else "unpriced"
        click.echo(
            f"  Spot token {leg.token_index} ({leg.evm_asset}): {leg.total} wei"
            f" ({leg.wei_decimals} dec), hold {leg.hold}, @ {price}"
            f" = {_wad_to_usd(leg.value_wad):.6f} USD"
        )
    if nav.native_perp is not None:
        click.echo(
            f"  Native perp (dex 0): {_wad_to_usd(nav.native_perp.value_wad):.6f} USD"
        )
    for leg in nav.hip3:
        click.echo(f"  HIP-3 dex {leg.dex}: {_wad_to_usd(leg.value_wad):.6f} USD")
    for pm in state.perp_markets:
        reduce_only = "required" if pm.reduce_only_required else "optional"
        click.echo(
            f"  Perp market {pm.coin or '?'} (asset {pm.asset}, dex {pm.dex},"
            f" read index {pm.read_index}): cap {pm.max_notional_usd6 / 10**6:,.2f} USD,"
            f" reduce-only {reduce_only}"
        )
    _print_hypercore_pending(state.pending)
    click.echo()


def _print_hypercore_pending(pending: HyperCorePendingState | None) -> None:
    if pending is None:
        click.echo(
            "  Pending action: unknown (no HyperCorePendingReader known on this chain)"
        )
        return
    last = f"{pending.action_class.name} #{pending.action_nonce}"
    if pending.pending:
        click.echo(
            f"  Pending action: {last} (id {pending.action_id}) until"
            f" {_unix_to_iso(pending.pending_until)}; L1 block {pending.current_l1_block}"
        )
    else:
        click.echo(
            f"  Pending action: none (last {last} settled); L1 block {pending.current_l1_block}"
        )


def _print_pending_requests(data: VaultData, plasma_vault: PlasmaVault) -> None:
    if (wmd := data.withdraw_manager_data) is None:
        return
    if wmd.pending_requests is None:
        click.echo(
            "  Pending requests: (unavailable — the WithdrawRequestUpdated "
            "log scan did not complete)"
        )
        return
    if not (requests := wmd.pending_requests):
        click.echo("  Pending requests: (none)")
        return

    total_shares = sum((r.shares for r in requests), 0)
    total_assets: int | None = _safe_call(
        lambda ts=total_shares: plasma_vault.convert_to_assets(ts).call()  # type: ignore[misc]
    )

    click.echo(f"  Pending requests ({len(requests)}):")
    rows: list[tuple[str, ...]] = []
    for req in requests:
        assets: int | None = _safe_call(
            lambda s=req.shares: plasma_vault.convert_to_assets(s).call()  # type: ignore[misc]
        )
        assets_str = (
            f"{_format_amount(assets, data.asset_decimals)} {data.asset_symbol}"
            if assets is not None
            else "?"
        )
        usd_str = ""
        if assets is not None and data.asset_price_usd is not None:
            usd_val = (assets / 10**data.asset_decimals) * data.asset_price_usd
            usd_str = f" (${usd_val:,.2f})"
        end_iso = _unix_to_iso(req.end_withdraw_window_timestamp)
        remaining = req.end_withdraw_window_timestamp - data.block_timestamp
        remaining_str = _format_remaining(remaining)
        status = "can_withdraw" if req.can_withdraw else "waiting"
        rows.append(
            (
                req.account,
                f"{_format_amount(req.shares, data.share_decimals)} shares",
                f"{assets_str}{usd_str}",
                f"{end_iso} ({remaining_str})",
                status,
            )
        )
    _print_table(("Account", "Shares", "Assets", "Window ends", "Status"), rows)

    total_fmt = _format_amount(total_shares, data.share_decimals)
    total_assets_fmt = (
        f" = {_format_amount(total_assets, data.asset_decimals)} {data.asset_symbol}"
        if total_assets is not None
        else ""
    )
    click.echo(f"  Total pending: {total_fmt} shares{total_assets_fmt}")


def _print_fuse_section(
    title: str,
    fuses: Sequence[str],
    fuse_markets: dict[str, int] | None,
    market_substrates: dict[int, list[bytes]] | None,
    chain_id: int,
    api_key: str | None,
) -> None:
    """Render a fuse section (regular or instant-withdrawal) with a unified
    layout.

    Fuses are deduplicated by address. The ``Substrates`` column reports
    how many substrates the fuse's market has registered (read from
    ``getMarketSubstrates(fuse.MARKET_ID())``) — the actionable surface for
    the fuse. The header surfaces duplication as ``N unique / M registrations``
    whenever the same address appears multiple times (typical for
    instant-withdrawal fuses).
    """
    counts: dict[str, int] = {}
    for addr in fuses:
        counts[addr] = counts.get(addr, 0) + 1

    total = len(fuses)
    unique_count = len(counts)
    suffix = f" / {total} registrations" if total != unique_count else ""
    click.echo(f"{title} ({unique_count} unique{suffix}):")

    if not counts:
        click.echo("  (none)")
        return

    with ThreadPoolExecutor() as pool:
        name_futs = {
            addr: pool.submit(get_contract_name, chain_id, addr, api_key)
            for addr in counts
        }
        rows: list[tuple[str, ...]] = []
        for idx, addr in enumerate(counts, 1):
            market_label = "?"
            substrate_count = "?"
            if fuse_markets and (mid := fuse_markets.get(addr)) is not None:
                market_label = format_market_label(mid)
                substrate_count = str(len((market_substrates or {}).get(mid, [])))
            rows.append(
                (
                    str(idx),
                    addr,
                    name_futs[addr].result() or "?",
                    market_label,
                    substrate_count,
                )
            )
    _print_table(("#", "Address", "Contract", "Market", "Substrates"), rows)


def _print_balance_fuses_table(
    plasma_vault: PlasmaVault,
    balance_fuses: list,
    decimals: int,
    asset_symbol: str,
    asset_price_usd: float | None,
    chain_id: int,
    api_key: str | None,
) -> dict[int, int]:
    """Print the table; return each market's cached balance, once per market
    (several balance fuses can share a market's single storage slot)."""
    market_balances: dict[int, int] = {}
    with ThreadPoolExecutor() as pool:
        futures: list[tuple[int, int, str, Future, Future]] = []
        for idx, balance_fuse in enumerate(balance_fuses, 1):
            market_id_str = format_market_label(balance_fuse.market_id)
            f_balance = pool.submit(
                plasma_vault.total_assets_in_market(balance_fuse.market_id).call
            )
            f_contract = pool.submit(
                get_contract_name, chain_id, balance_fuse.fuse, api_key
            )
            futures.append(
                (idx, balance_fuse.market_id, market_id_str, f_balance, f_contract)
            )

        venue_rows: list[tuple[str, ...]] = []
        zero_rows: list[tuple[str, ...]] = []
        for idx, market_id, market_id_str, f_balance, f_contract in futures:
            assets_in_market = f_balance.result()
            market_balances.setdefault(market_id, assets_in_market)
            contract_name = f_contract.result() or "?"
            balance_str = (
                f"{_format_amount(assets_in_market, decimals)} {asset_symbol}"
                f"{_format_usd(assets_in_market, decimals, asset_price_usd)}"
                f" (cached)"
            )
            target = (
                zero_rows if contract_name in _CAPABILITY_BALANCE_FUSES else venue_rows
            )
            target.append(
                (market_id_str, balance_str, balance_fuses[idx - 1].fuse, contract_name)
            )

    # Venues (real *BalanceFuse) are liquidity markets; zero-balance fuses are
    # capabilities (swap/flash-loan/admin plumbing) shown separately so they are
    # not read as markets.
    header = ("#", "Market", "Balance", "Fuse", "Contract")
    click.echo(f"Balance Fuses ({len(venue_rows)}):")
    _print_table(header, [(str(i), *row) for i, row in enumerate(venue_rows, 1)])
    if zero_rows:
        click.echo()
        click.echo(f"Zero-Balance Fuses ({len(zero_rows)}):")
        _print_table(header, [(str(i), *row) for i, row in enumerate(zero_rows, 1)])
    return market_balances


def _print_market_substrates(
    ctx: Web3Context, data: VaultData, chain_id: int, api_key: str | None
) -> set[str]:
    if GET_MARKET_SUBSTRATES in data.unimplemented_getters:
        click.secho(
            f"  (unavailable: the vault predates {GET_MARKET_SUBSTRATES})",
            fg="yellow",
        )
        return set()
    return _print_substrates(
        ctx, data.market_substrates or {}, data.balance_fuses, chain_id, api_key
    )


def _print_substrates(  # noqa: C901
    ctx: Web3Context,
    substrates_by_market: dict[int, list[bytes]],
    balance_fuses: list,
    chain_id: int,
    api_key: str | None,
) -> set[str]:
    # Phase 1: collect each balance fuse's substrates and the addresses to resolve
    market_substrates: list[tuple[str, int, list]] = []
    all_addresses: set[str] = set()
    for balance_fuse in balance_fuses:
        mid = balance_fuse.market_id
        if not (substrates := substrates_by_market.get(mid)):
            continue
        market_substrates.append((format_market_label(mid), mid, substrates))
        for sub in substrates:
            sub_info = decode_substrate(sub, market_id=mid)
            if sub_info.address:
                all_addresses.add(sub_info.address)

    if not market_substrates:
        click.echo("  (none)")
        return {a.lower() for a in all_addresses}

    # Phase 2: resolve all symbols + contract names in parallel
    with ThreadPoolExecutor() as pool:
        symbol_futs = {
            addr: pool.submit(resolve_token_symbol, ctx, addr) for addr in all_addresses
        }
        contract_futs = {
            addr: pool.submit(get_contract_name, chain_id, addr, api_key)
            for addr in all_addresses
        }

        resolved_symbols = {addr: fut.result() for addr, fut in symbol_futs.items()}
        resolved_contracts = {addr: fut.result() for addr, fut in contract_futs.items()}

    # Phase 3: print
    for market_id_str, mid, substrates in market_substrates:
        click.echo(f"  {market_id_str}:")
        for sub in substrates:
            sub_info = decode_substrate(sub, market_id=mid)
            if sub_info.address:
                symbol = resolved_symbols.get(sub_info.address, "")
                contract = resolved_contracts.get(sub_info.address, "")
                details = _substrate_details(
                    symbol, contract, sub_info.type_label, sub_info.extra
                )
                click.echo(f"    {sub_info.address}{details}")
            elif sub_info.is_error:
                details = [sub_info.type_label] if sub_info.type_label else []
                details.extend(
                    f"{key}={value}" for key, value in sub_info.extra.items()
                )
                suffix = f": {', '.join(details)}" if details else ""
                click.secho(
                    f"    {sub_info.raw_hex} [encoding error{suffix}]", fg="red"
                )
            else:
                parts = []
                if sub_info.type_label:
                    parts.append(sub_info.type_label)
                for k, v in sub_info.extra.items():
                    parts.append(f"{k}={v}")
                label = ", ".join(parts) if parts else "bytes32"
                is_no_decoder = sub_info.type_label.startswith("no_decoder(")
                fg = "red" if is_no_decoder else None
                click.secho(f"    {sub_info.raw_hex} ({label})", fg=fg)

    return {a.lower() for a in all_addresses}

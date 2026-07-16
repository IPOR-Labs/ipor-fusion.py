from __future__ import annotations

import json
from collections.abc import Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import datetime, timezone
from typing import Any

import click
import requests
from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted, Web3RPCError

from ipor_fusion.cli.config_store import (
    FusionConfig,
    VaultEntry,
    load_config,
    save_config,
)
from ipor_fusion.cli.explorer import get_contract_name
from ipor_fusion.cli.vault_fetcher import (
    _fetch_deployment_info,
    _fetch_vault_data,
    _resolve_token_decimals,
    _resolve_token_symbol,
    _safe_call,
    _VaultData,
)
from ipor_fusion.cli.vault_health import (
    _BalanceFuseTotals,
    _compute_erc20_balances,
    _compute_health_check,
    _compute_reconciliation,
    _print_erc20_balances,
    _print_health_check,
    _print_reconciliation,
)
from ipor_fusion.cli.vault_rendering import (
    _format_age,
    _format_amount,
    _format_remaining,
    _format_usd,
    _print_table,
    _substrate_details,
)
from ipor_fusion.cli.vault_substrate import (
    _format_market_label,
    _format_substrate,
    _market_name,
)
from ipor_fusion.config.roles import Roles
from ipor_fusion.core.access import (
    AccessManager,
    resolve_access_manager,
    role_account_sort_key,
)
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.errors import ContractNotFoundError, NotPlasmaVaultError
from ipor_fusion.readers.oracle_mapping import (
    OracleMapping,
    OracleNode,
    OraclePrice,
    build_oracle_mapping,
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


CHAIN_NAMES: dict[int, str] = {
    1: "ethereum",
    42161: "arbitrum",
    8453: "base",
    10: "optimism",
    137: "polygon",
    56: "bsc",
    43114: "avalanche",
    250: "fantom",
}

CHAIN_NAME_TO_ID: dict[str, int] = {name: cid for cid, name in CHAIN_NAMES.items()}


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

BLOCK_EXPLORER_URLS: dict[int, str] = {
    1: "https://etherscan.io",
    42161: "https://arbiscan.io",
    8453: "https://basescan.org",
    10: "https://optimistic.etherscan.io",
    137: "https://polygonscan.com",
    56: "https://bscscan.com",
    43114: "https://snowtrace.io",
    250: "https://ftmscan.com",
}

IPOR_APP_URL = "https://app.ipor.io/fusion"

UINT256_MAX = 2**256 - 1

# Balance fuses whose ``balanceOf()`` is structurally zero (``pure`` → 0). Their
# market is a registered *capability* (swap / flash-loan / instant-withdrawal /
# admin plumbing), not a *venue* where assets actually sit. Anything backed by a
# real ``*BalanceFuse`` is a venue.
_CAPABILITY_BALANCE_FUSES: frozenset[str] = frozenset({"ZeroBalanceFuse"})


def _partition_balance_fuses(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split balance-fuse entries into ``(venues, zero_balance)``.

    Venues are real ``*BalanceFuse`` markets where assets can sit. Zero-balance
    fuses are ZeroBalanceFuse-backed markets (swap/flash-loan/admin plumbing)
    with a structurally-zero balance — capabilities, not liquidity markets.
    Splitting keeps both visible while letting a consumer count only venues as
    markets.
    """
    venues = [e for e in entries if e.get("contract") not in _CAPABILITY_BALANCE_FUSES]
    zero_balance = [
        e for e in entries if e.get("contract") in _CAPABILITY_BALANCE_FUSES
    ]
    return venues, zero_balance


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
    provider_url = _resolve_provider(cfg, chain_id)
    ctx = Web3Context.from_url(provider_url)
    if block_number is not None:
        ctx.default_block = block_number
    return chain_id, ctx


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

    data = _fetch_vault_data(ctx, plasma_vault, block_number, chain_id=chain_id)
    _print_vault_info(
        ctx, plasma_vault, cfg, data, vault_address, chain_id, json_output
    )


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
            f"RoleGranted log scan failed ({type(exc).__name__}: {exc}). "
            "The provider must serve broad eth_getLogs queries."
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
    price feed, classifies its type by interface probing (Chainlink / ERC4626
    / Morpho collateral / unknown) and recursively follows feeds that derive
    their price from another asset. Unknown feeds are reported as partial,
    never dropped. Historical blocks require an archive node.
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

    mapping = build_oracle_mapping(
        ctx, Web3.to_checksum_address(vault_address), effective_block, max_depth
    )

    if json_output:
        click.echo(json.dumps(mapping.to_dict(), indent=2))
        return
    _print_oracle_mapping(mapping)


def _format_wad_price(price: OraclePrice) -> str:
    if price.normalized_wad is None:
        return "N/A"
    return _format_amount(int(price.normalized_wad), 18)


def _print_oracle_node(node: OracleNode) -> None:
    click.echo(f"  {node.symbol or '?'} ({node.asset})")
    click.echo(f"    Path:   {' → '.join(node.path)}")
    click.echo(f"    Price:  {_format_wad_price(node.price)}")
    if node.status == "resolved":
        click.secho("    Status: resolved", fg="green")
    else:
        click.secho(f"    Status: partial ({node.reason})", fg="yellow")


def _print_oracle_mapping(mapping: OracleMapping) -> None:
    name_suffix = f" ({mapping.vault_name})" if mapping.vault_name else ""
    click.echo(f"Vault:        {mapping.vault}{name_suffix}")
    click.echo(
        f"Underlying:   {mapping.asset.get('symbol') or '?'} "
        f"({mapping.asset.get('address')})"
    )
    click.echo(f"Price Oracle: {mapping.price_oracle}")
    click.echo(f"Block:        {mapping.block_number}")
    click.echo(f"Enumerated:   {mapping.asset_source}")
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
    ctx: Web3Context, data: _VaultData
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
    consumed_aave_mids: set[int] = set()
    prices = data.token_prices_usd or {}

    for ipor_mid, positions in (data.morpho_positions or {}).items():
        click.echo(f"  {_format_market_label(ipor_mid)}:")
        for pb in positions:
            coll_sym = _resolve_token_symbol(ctx, pb.collateral_token) or "?"
            loan_sym = _resolve_token_symbol(ctx, pb.loan_token) or "?"
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
        click.echo(f"  {_format_market_label(ipor_mid)}:")
        for ab in aave_positions:
            asset_symbol = _resolve_token_symbol(ctx, ab.asset) or "?"
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
            click.echo(f"  {_format_market_label(m.market_id)}:")
            click.echo(f"    morpho market 0x{sid}:")
            _print_health_lines(m, indent="      ")
    for mid, m in aave_health.items():
        if mid not in consumed_aave_mids:
            click.echo(f"  {_format_market_label(mid)}:")
            _print_health_lines(m, indent="    ")


def _index_lending_health(lh: Any) -> tuple[dict[str, Any], dict[int, Any]]:
    """Split lending health rows by protocol for fast lookup during rendering.

    Morpho rows are keyed by morpho substrate id (one row per substrate).
    Aave rows are keyed by IPOR market id (account-aggregated, one row per market).
    """
    morpho: dict[str, Any] = {}
    aave: dict[int, Any] = {}
    if lh is None:
        return morpho, aave
    for m in lh.markets:
        if m.protocol == "morpho" and m.substrate_id:
            morpho[str(m.substrate_id).lower().removeprefix("0x")] = m
        elif m.protocol == "aave_v3":
            aave[m.market_id] = m
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


# Failure modes of the heavy RoleGranted scan: JSON-RPC rejections/limits plus
# transport-level errors — web3's HTTPProvider re-raises raw requests
# exceptions (read timeouts, 429/5xx via raise_for_status).
_ROLE_SCAN_ERRORS = (
    ContractLogicError,
    Web3RPCError,
    TimeExhausted,
    requests.RequestException,
)


def _fetch_role_accounts_json(
    ctx: Web3Context, data: _VaultData
) -> list[dict[str, Any]] | None:
    """All confirmed role holders on the AccessManager, sorted; None when the
    RoleGranted log scan fails (provider without broad eth_getLogs support,
    or a transport-level failure on the heavy query)."""
    try:
        accounts = AccessManager(
            ctx,
            data.access_manager,  # type: ignore[arg-type]
        ).get_all_role_accounts()
    except _ROLE_SCAN_ERRORS:
        return None
    return [ra.to_dict() for ra in sorted(accounts, key=role_account_sort_key)]


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
        click.echo("  (unavailable — provider could not serve the log scan)")
    else:
        _print_role_accounts_table(role_accounts)
    click.echo()


def _print_vault_info(
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    cfg: FusionConfig,
    data: _VaultData,
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
    ) = _fetch_deployment_info(ctx, chain_id, vault_address, api_key)

    if json_output:
        result = _build_json_output(
            ctx, plasma_vault, data, vault_address, chain_id, chain_label, api_key
        )
        click.echo(json.dumps(result, indent=2))
        return

    # Start the heavy RoleGranted scan now; joined when its section prints.
    # shutdown(wait=False) only stops new submissions — the task completes.
    role_pool = ThreadPoolExecutor(max_workers=1)
    role_accounts_fut = role_pool.submit(_fetch_role_accounts_json, ctx, data)
    role_pool.shutdown(wait=False)

    total_assets_usd = _format_usd(
        data.total_assets, data.asset_decimals, data.asset_price_usd
    )

    name_suffix = f" ({data.vault_name})" if data.vault_name else ""
    click.echo(f"Vault:            {vault_address}{name_suffix}")
    if explorer_base := BLOCK_EXPLORER_URLS.get(chain_id):
        click.echo(f"Etherscan:        {explorer_base}/address/{vault_address}")
    click.echo(f"IPOR app:         {IPOR_APP_URL}/{chain_label}/{vault_address}")
    click.echo(f"Chain:            {chain_label} (chain-id={chain_id})")
    block_suffix = " (latest)" if data.is_latest else ""
    click.echo(f"Block:            {data.block_number}{block_suffix}")
    block_dt = datetime.fromtimestamp(data.block_timestamp, tz=timezone.utc)
    block_iso = block_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
    click.echo(f"Block time:       {data.block_timestamp} ({block_iso})")
    if data.deployment_block is not None and data.deployment_timestamp is not None:
        deploy_dt = datetime.fromtimestamp(data.deployment_timestamp, tz=timezone.utc)
        deploy_iso = deploy_dt.strftime("%Y-%m-%dT%H:%M:%SZ")
        age = _format_age(data.deployment_timestamp)
        click.echo(
            f"Deployed at:      block {data.deployment_block} ({deploy_iso}, {age})"
        )
    elif data.deployment_error:
        click.echo(f"Deployed at:      N/A ({data.deployment_error})")
    else:
        click.echo("Deployed at:      N/A")
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
    click.echo(f"Access Manager:   {data.access_manager}")
    click.echo(f"Price Oracle:     {data.price_oracle_addr}")
    click.echo(f"Rewards Manager:  {data.rewards_manager or 'N/A'}")
    click.echo(f"Withdraw Manager: {data.withdraw_manager or 'N/A'}")
    if data.withdraw_manager_data:
        wmd = data.withdraw_manager_data
        window_h = wmd.withdraw_window / 3600
        click.echo(f"  Window:         {wmd.withdraw_window}s ({window_h:.1f}h)")
        click.echo(
            f"  Request fee:    {wmd.request_fee / 1e18:.4%}"
            f"   Withdraw fee: {wmd.withdraw_fee / 1e18:.4%}"
        )
        release_fmt = _format_amount(wmd.shares_to_release, data.share_decimals)
        click.echo(f"  Shares to release: {release_fmt}")
        if wmd.last_release_funds_timestamp > 0:
            last_dt = datetime.fromtimestamp(
                wmd.last_release_funds_timestamp, tz=timezone.utc
            )
            click.echo(f"  Last release:   {last_dt.strftime('%Y-%m-%dT%H:%M:%SZ')}")
        _print_pending_requests(data, plasma_vault)
    click.echo()

    _print_role_accounts(role_accounts_fut.result())

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

    bf_totals = _print_balance_fuses_table(
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
    all_substrate_addrs = _print_substrates(
        ctx, plasma_vault, data.balance_fuses, chain_id, api_key
    )
    click.echo()

    click.echo("ERC20 Balances (vault holdings):")
    erc20_totals = _print_erc20_balances(ctx, plasma_vault, data)
    click.echo()

    _print_reconciliation(
        data,
        bf_totals,
        erc20_totals,
        plasma_vault,
    )
    click.echo()

    _print_lending_health(ctx, data)
    click.echo()

    _print_health_check(
        data, bf_totals, erc20_totals, all_substrate_addrs, plasma_vault
    )


def _build_share_price_json(data: _VaultData) -> dict | None:
    if data.total_supply == 0:
        return None
    price = (data.total_assets / 10**data.asset_decimals) / (
        data.total_supply / 10**data.share_decimals
    )
    result: dict = {
        "asset": round(price, 10),
    }
    if data.asset_price_usd is not None:
        result["usd"] = round(price * data.asset_price_usd, 10)
    return result


def _build_deployment_json(data: _VaultData) -> dict | None:
    if data.deployment_block is None or data.deployment_timestamp is None:
        if data.deployment_error:
            return {"error": data.deployment_error}
        return None
    return {
        "block": data.deployment_block,
        "timestamp": data.deployment_timestamp,
        "timestamp_utc": datetime.fromtimestamp(
            data.deployment_timestamp, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "age_days": (
            datetime.now(tz=timezone.utc)
            - datetime.fromtimestamp(data.deployment_timestamp, tz=timezone.utc)
        ).days,
    }


def _build_withdraw_manager_json(
    data: _VaultData, plasma_vault: PlasmaVault
) -> dict | None:
    if data.withdraw_manager_data is None:
        return None
    wmd = data.withdraw_manager_data
    sdec = data.share_decimals
    adec = data.asset_decimals

    total_pending_shares = sum((r.shares for r in wmd.pending_requests), 0)

    requests_json = []
    for req in wmd.pending_requests:
        assets: int | None = _safe_call(
            lambda s=req.shares: plasma_vault.convert_to_assets(s).call()  # type: ignore[misc]
        )
        entry: dict = {
            "account": req.account,
            "shares": {
                "raw": req.shares,
                "formatted": _format_amount(req.shares, sdec),
            },
            "end_withdraw_window_timestamp": req.end_withdraw_window_timestamp,
            "end_withdraw_window_utc": datetime.fromtimestamp(
                req.end_withdraw_window_timestamp, tz=timezone.utc
            ).strftime("%Y-%m-%dT%H:%M:%SZ"),
            "remaining_seconds": max(
                0, req.end_withdraw_window_timestamp - data.block_timestamp
            ),
            "can_withdraw": req.can_withdraw,
        }
        if assets is not None:
            entry["assets"] = {
                "raw": assets,
                "formatted": _format_amount(assets, adec),
            }
            if data.asset_price_usd is not None:
                entry["assets"]["usd"] = round(
                    (assets / 10**adec) * data.asset_price_usd, 2
                )
        requests_json.append(entry)

    result: dict = {
        "withdraw_window_seconds": wmd.withdraw_window,
        "withdraw_window_seconds_note": "Length of the window, starting at request time, in which a scheduled withdrawal can be executed (also requires the vault Alpha to release funds after the request).",
        "request_fee_wad": wmd.request_fee,
        "request_fee_percent": round(wmd.request_fee / 1e18 * 100, 4),
        "request_fee_percent_note": "Scheduled-exit fee, charged once at request. Mutually exclusive with withdraw_fee — a user pays one path, never summed.",
        "withdraw_fee_wad": wmd.withdraw_fee,
        "withdraw_fee_percent": round(wmd.withdraw_fee / 1e18 * 100, 4),
        "withdraw_fee_percent_note": "Instant-exit fee on standard redeem/withdraw from unallocated balance. Mutually exclusive with request_fee — a user pays one path, never summed.",
        "shares_to_release": {
            "raw": wmd.shares_to_release,
            "formatted": _format_amount(wmd.shares_to_release, sdec),
        },
        "shares_to_release_note": "Current shares the vault Alpha has approved for release via releaseFunds().",
        "last_release_funds_timestamp": wmd.last_release_funds_timestamp,
        "last_release_funds_timestamp_note": "Release timestamp set by the last releaseFunds() call; 0 if never released.",
        "pending_requests": requests_json,
        "total_pending_shares": {
            "raw": total_pending_shares,
            "formatted": _format_amount(total_pending_shares, sdec),
        },
        "total_pending_shares_note": "Sum of shares across all pending withdrawal requests.",
    }
    if wmd.last_release_funds_timestamp > 0:
        result["last_release_funds_utc"] = datetime.fromtimestamp(
            wmd.last_release_funds_timestamp, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ")
    return result


def _build_breakdown_amount_json(
    ctx: Web3Context,
    raw: int,
    token_address: str,
    prices_usd: dict[str, float] | None,
) -> dict[str, Any]:
    """Build the JSON entry for one breakdown amount (collateral/borrow/supply/debt).

    Always includes raw + token. When ERC-20 metadata is resolvable, adds
    symbol, decimals, and `formatted` (decimal-shifted human string). When the
    oracle has a USD price for the token, adds `usd` (rounded to 2 decimals).
    """
    entry: dict[str, Any] = {"raw": raw, "token": token_address}
    if symbol := _resolve_token_symbol(ctx, token_address):
        entry["symbol"] = symbol
    decimals = _resolve_token_decimals(ctx, token_address)
    if decimals is not None:
        entry["decimals"] = decimals
        entry["formatted"] = _format_amount(raw, decimals)
        if prices_usd and (price := prices_usd.get(token_address.lower())) is not None:
            entry["usd"] = round((raw / 10**decimals) * price, 2)
    return entry


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
    symbol = _resolve_token_symbol(ctx, token_address) or "?"
    decimals = _resolve_token_decimals(ctx, token_address)
    if decimals is None:
        return f"{raw} raw ({symbol})"
    base = f"{_format_amount(raw, decimals)} {symbol}"
    if prices_usd:
        usd_suffix = _format_usd(raw, decimals, prices_usd.get(token_address.lower()))
        return f"{base}{usd_suffix}"
    return base


def _print_dependency_graph(data: _VaultData) -> None:
    if not data.dependency_graph:
        return

    from ipor_fusion.cli.vault_dep_graph import (
        compute_update_reach,
    )

    click.echo("Dependency Balance Graph:")
    for market_id, deps in data.dependency_graph.items():
        label = _format_market_label(market_id)
        dep_labels = ", ".join(_format_market_label(d) for d in deps)
        click.echo(f"  {label} → {dep_labels}")
    click.echo()

    reach = compute_update_reach(data.dependency_graph)
    if reach:
        click.echo("  Update reach (calling updateMarketsBalances for root market):")
        for market_id, reachable in sorted(reach.items(), key=lambda kv: -len(kv[1])):
            label = _format_market_label(market_id)
            targets = ", ".join(_format_market_label(r) for r in sorted(reachable))
            click.echo(f"    {label} refreshes: {targets}")
        click.echo()


def _build_dependency_graph_json(data: _VaultData) -> dict | None:
    if not data.dependency_graph:
        return None

    from ipor_fusion.cli.vault_dep_graph import (
        compute_update_groups,
        compute_update_reach,
    )

    edges = {
        _format_market_label(mid): [_format_market_label(d) for d in deps]
        for mid, deps in data.dependency_graph.items()
    }

    reach = compute_update_reach(data.dependency_graph)
    reach_json = {
        _format_market_label(mid): sorted(_format_market_label(r) for r in reachable)
        for mid, reachable in reach.items()
    }

    groups = compute_update_groups(data.dependency_graph)
    groups_json = [sorted(_format_market_label(m) for m in group) for group in groups]

    return {
        "edges": edges,
        "update_reach": reach_json,
        "update_groups": groups_json,
    }


def _build_json_output(  # noqa: C901, PLR0912, PLR0915
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    data: _VaultData,
    vault_address: str,
    chain_id: int,
    chain_label: str,
    api_key: str | None,
) -> dict:
    """Build a dict with all vault info for JSON serialization."""
    total_assets_usd: float | None = None
    if data.asset_price_usd is not None:
        total_assets_usd = (
            data.total_assets / 10**data.asset_decimals
        ) * data.asset_price_usd

    # Resolve fuse contract names in parallel
    with ThreadPoolExecutor() as pool:
        # The heavy RoleGranted scan overlaps the fetches below; the with-block
        # exit waits for it, so .result() in the return dict never blocks.
        role_accounts_fut = pool.submit(_fetch_role_accounts_json, ctx, data)
        fuse_name_futs = {
            addr: pool.submit(get_contract_name, chain_id, addr, api_key)
            for addr in data.fuses
        }
        instant_name_futs = {
            addr: pool.submit(get_contract_name, chain_id, addr, api_key)
            for addr in data.instant_fuses
        }
        bf_contract_futs = [
            pool.submit(get_contract_name, chain_id, bf.fuse, api_key)
            for bf in data.balance_fuses
        ]
        bf_balance_futs = [
            pool.submit(plasma_vault.total_assets_in_market(bf.market_id).call)
            for bf in data.balance_fuses
        ]
        substrate_futs = [
            pool.submit(plasma_vault.get_market_substrates(bf.market_id).call)
            for bf in data.balance_fuses
        ]

        fuse_markets = data.fuse_markets or {}

        def _fuse_entry(addr: str, contract: str | None) -> dict:
            entry: dict = {"address": addr, "contract": contract or "?"}
            if (mid := fuse_markets.get(addr)) is not None:
                entry["market_id"] = mid
                label = _market_name(mid)
                entry["market"] = label if label != "UNKNOWN" else str(mid)
            return entry

        fuses_json = [
            _fuse_entry(addr, fuse_name_futs[addr].result()) for addr in data.fuses
        ]
        instant_json = [
            _fuse_entry(addr, instant_name_futs[addr].result())
            for addr in data.instant_fuses
        ]

        balance_fuses_json = []
        for i, bf in enumerate(data.balance_fuses):
            market_label = _market_name(bf.market_id)
            market_str = (
                market_label if market_label != "UNKNOWN" else str(bf.market_id)
            )
            assets_in_market = bf_balance_futs[i].result()
            contract_name = bf_contract_futs[i].result() or "?"
            bf_entry: dict = {
                "market": market_str,
                "market_id": bf.market_id,
                "balance": {
                    "raw": assets_in_market,
                    "formatted": _format_amount(assets_in_market, data.asset_decimals),
                },
                "fuse": bf.fuse,
                "contract": contract_name,
            }
            if data.total_assets > 0:
                bf_entry["pct_of_total"] = round(
                    assets_in_market / data.total_assets * 100, 2
                )
            if data.dependency_graph and bf.market_id in data.dependency_graph:
                deps = data.dependency_graph[bf.market_id]
                bf_entry["depends_on"] = [_format_market_label(d) for d in deps]
            if data.morpho_positions and bf.market_id in data.morpho_positions:
                bf_entry["position_breakdown"] = [
                    {
                        "morpho_market_id": "0x" + str(pb.market_id),
                        "collateral_symbol": _resolve_token_symbol(
                            ctx, pb.collateral_token
                        )
                        or None,
                        "loan_symbol": _resolve_token_symbol(ctx, pb.loan_token)
                        or None,
                        "collateral": _build_breakdown_amount_json(
                            ctx,
                            int(pb.collateral),
                            pb.collateral_token,
                            data.token_prices_usd,
                        ),
                        "borrow": _build_breakdown_amount_json(
                            ctx,
                            int(pb.borrow_assets),
                            pb.loan_token,
                            data.token_prices_usd,
                        ),
                        "supply": _build_breakdown_amount_json(
                            ctx,
                            int(pb.supply_assets),
                            pb.loan_token,
                            data.token_prices_usd,
                        ),
                    }
                    for pb in data.morpho_positions[bf.market_id]
                ]
            if data.aave_positions and bf.market_id in data.aave_positions:
                bf_entry["position_breakdown"] = [
                    {
                        "asset": pb.asset,
                        "asset_symbol": _resolve_token_symbol(ctx, pb.asset) or None,
                        "supply": _build_breakdown_amount_json(
                            ctx, int(pb.supply), pb.asset, data.token_prices_usd
                        ),
                        "variable_debt": _build_breakdown_amount_json(
                            ctx,
                            int(pb.variable_debt),
                            pb.asset,
                            data.token_prices_usd,
                        ),
                        "stable_debt": _build_breakdown_amount_json(
                            ctx,
                            int(pb.stable_debt),
                            pb.asset,
                            data.token_prices_usd,
                        ),
                    }
                    for pb in data.aave_positions[bf.market_id]
                ]
            balance_fuses_json.append(bf_entry)

        balance_fuses_json, zero_balance_fuses_json = _partition_balance_fuses(
            balance_fuses_json
        )

        # Substrates - also resolve symbols/contracts for addresses
        all_sub_addresses: set[str] = set()
        market_subs_raw: list[tuple[str, int, list]] = []
        for i, bf in enumerate(data.balance_fuses):
            market_str = _format_market_label(bf.market_id)
            if subs := substrate_futs[i].result():
                market_subs_raw.append((market_str, bf.market_id, subs))
                for sub in subs:
                    sub_info = _format_substrate(sub, market_id=bf.market_id)
                    if sub_info.address:
                        all_sub_addresses.add(sub_info.address)

        sym_futs = {
            addr: pool.submit(_resolve_token_symbol, ctx, addr)
            for addr in all_sub_addresses
        }
        contract_futs = {
            addr: pool.submit(get_contract_name, chain_id, addr, api_key)
            for addr in all_sub_addresses
        }

        substrates_json: dict[str, list] = {}
        for market_str, mid, subs in market_subs_raw:
            entries = []
            for sub in subs:
                sub_info = _format_substrate(sub, market_id=mid)
                if sub_info.address:
                    entry: dict = {"address": sub_info.address}
                    symbol = sym_futs[sub_info.address].result()
                    contract = contract_futs[sub_info.address].result()
                    if symbol:
                        entry["symbol"] = symbol
                    if contract:
                        entry["contract"] = contract
                    if sub_info.type_label:
                        entry["substrate_type"] = sub_info.type_label
                    if sub_info.extra:
                        entry.update(sub_info.extra)
                    entries.append(entry)
                else:
                    raw_entry: dict = {"raw": sub_info.raw_hex}
                    if sub_info.is_error:
                        raw_entry["error"] = True
                    if sub_info.type_label:
                        raw_entry["substrate_type"] = sub_info.type_label
                    if sub_info.extra:
                        raw_entry.update(sub_info.extra)
                    entries.append(raw_entry)
            substrates_json[market_str] = entries

    # ERC20 balances
    erc20_totals = _compute_erc20_balances(ctx, plasma_vault, data)
    erc20_json = []
    for td in erc20_totals.token_details:
        erc20_entry: dict = {"address": td.address, "symbol": td.symbol}
        if td.decimals is not None and td.balance is not None:
            erc20_entry["decimals"] = td.decimals
            erc20_entry["balance"] = {
                "raw": td.balance,
                "formatted": _format_amount(td.balance, td.decimals),
            }
            erc20_entry["price_usd"] = td.price_usd
            erc20_entry["usd_value"] = td.usd_value
        erc20_entry["note"] = td.note
        erc20_json.append(erc20_entry)

    # Balance fuse totals for reconciliation (deduplicate by market_id)
    bf_totals = _BalanceFuseTotals()
    seen_json_markets: set[int] = set()
    for bfj in balance_fuses_json:
        mid = bfj["market_id"]
        if mid in seen_json_markets:
            continue
        seen_json_markets.add(mid)
        bf_totals.raw_total += bfj["balance"]["raw"]
        if data.asset_price_usd is not None:
            bf_totals.usd_total += (
                bfj["balance"]["raw"] / 10**data.asset_decimals
            ) * data.asset_price_usd

    # Reconciliation
    recon = _compute_reconciliation(data, bf_totals, erc20_totals, plasma_vault)
    decimals = data.asset_decimals
    reconciliation_json: dict = {
        "balance_fuses_total": {
            "raw": recon.bf_total_raw,
            "formatted": _format_amount(recon.bf_total_raw, decimals),
            "usd": recon.bf_total_usd,
        },
        "underlying_on_vault": {
            "raw": recon.underlying_raw,
            "formatted": _format_amount(recon.underlying_raw, decimals),
            "usd": recon.underlying_usd,
        },
        "erc20_direct_total": {
            "raw": recon.erc20_total_raw,
            "formatted": _format_amount(recon.erc20_total_raw, decimals),
            "usd": recon.erc20_total_usd,
        },
        "sum": {
            "raw": recon.sum_raw,
            "formatted": _format_amount(recon.sum_raw, decimals),
            "usd": recon.sum_usd,
        },
        "on_chain_total_assets": {
            "raw": recon.on_chain_raw,
            "formatted": _format_amount(recon.on_chain_raw, decimals),
            "usd": recon.on_chain_usd,
        },
        "delta": {
            "raw": recon.delta_raw,
            "formatted": _format_amount(abs(recon.delta_raw), decimals),
            "usd": recon.delta_usd,
            "percent": recon.delta_percent,
        },
        "pending_withdrawals": {
            "raw": recon.pending_withdrawal_raw,
            "formatted": _format_amount(recon.pending_withdrawal_raw, decimals),
            "usd": recon.pending_withdrawal_usd,
        },
        "implied_market_total": {
            "raw": recon.implied_market_total,
            "formatted": _format_amount(recon.implied_market_total, decimals),
        },
        "market_storage_divergence": recon.bf_total_raw - recon.implied_market_total,
    }

    # Lending health
    lending_health_json = None
    if data.lending_health and data.lending_health.has_lending_positions:
        lending_health_json = {
            "markets": [
                {
                    "protocol": m.protocol,
                    "market_id": m.market_id,
                    "market_name": m.market_name,
                    "current_ltv": m.current_ltv,
                    "max_ltv": m.max_ltv,
                    "health_factor": m.health_factor,
                    "total_collateral_usd": m.total_collateral_usd,
                    "total_debt_usd": m.total_debt_usd,
                    "ltv_usage_percent": m.ltv_usage_percent,
                    "is_warning": m.is_warning,
                    "is_critical": m.is_critical,
                }
                for m in data.lending_health.markets
            ],
            "worst_ltv_usage_percent": data.lending_health.worst_ltv_usage,
        }

    # Health check
    all_sub_lower = {a.lower() for a in all_sub_addresses}
    health = _compute_health_check(
        data, bf_totals, erc20_totals, all_sub_lower, plasma_vault
    )
    health_json = {
        "ok": health.ok,
        "warnings": health.warnings,
        "criticals": health.criticals,
    }

    explorer_base = BLOCK_EXPLORER_URLS.get(chain_id)
    links: dict[str, str] = {
        "ipor_app": f"{IPOR_APP_URL}/{chain_label}/{vault_address}",
    }
    if explorer_base:
        links["etherscan"] = f"{explorer_base}/address/{vault_address}"

    return {
        "vault": vault_address,
        "name": data.vault_name or None,
        "links": links,
        "chain": chain_label,
        "chain_id": chain_id,
        "block": data.block_number,
        "is_latest": data.is_latest,
        "block_timestamp": data.block_timestamp,
        "block_timestamp_utc": datetime.fromtimestamp(
            data.block_timestamp, tz=timezone.utc
        ).strftime("%Y-%m-%dT%H:%M:%SZ"),
        "deployment": _build_deployment_json(data),
        "asset": {
            "address": data.asset,
            "symbol": data.asset_symbol,
            "decimals": data.asset_decimals,
            "price_usd": data.asset_price_usd,
        },
        "share_decimals": data.share_decimals,
        "total_assets": {
            "raw": data.total_assets,
            "formatted": _format_amount(data.total_assets, data.asset_decimals),
            "usd": total_assets_usd,
        },
        "total_supply": {
            "raw": data.total_supply,
            "formatted": _format_amount(data.total_supply, data.share_decimals),
        },
        "share_price": _build_share_price_json(data),
        "supply_cap": {
            "raw": data.supply_cap,
            "formatted": (
                "unlimited"
                if data.supply_cap == UINT256_MAX
                else _format_amount(data.supply_cap, data.asset_decimals)
            ),
        },
        "managers": {
            "access": data.access_manager,
            "price_oracle": data.price_oracle_addr,
            "rewards": data.rewards_manager,
            "withdraw": data.withdraw_manager,
        },
        "role_accounts": role_accounts_fut.result(),
        "withdraw_manager_details": _build_withdraw_manager_json(data, plasma_vault),
        "fuses": fuses_json,
        "balance_fuses": balance_fuses_json,
        "zero_balance_fuses": zero_balance_fuses_json,
        "instant_withdrawal_fuses": instant_json,
        "substrates": substrates_json,
        "dependency_graph": _build_dependency_graph_json(data),
        "erc20_balances": erc20_json,
        "reconciliation": reconciliation_json,
        "lending_health": lending_health_json,
        "health_check": health_json,
    }


def _print_pending_requests(data: _VaultData, plasma_vault: PlasmaVault) -> None:
    if (wmd := data.withdraw_manager_data) is None:
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
        end_dt = datetime.fromtimestamp(
            req.end_withdraw_window_timestamp, tz=timezone.utc
        )
        remaining = req.end_withdraw_window_timestamp - data.block_timestamp
        remaining_str = _format_remaining(remaining)
        status = "can_withdraw" if req.can_withdraw else "waiting"
        rows.append(
            (
                req.account,
                f"{_format_amount(req.shares, data.share_decimals)} shares",
                f"{assets_str}{usd_str}",
                f"{end_dt.strftime('%Y-%m-%dT%H:%M:%SZ')} ({remaining_str})",
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
                market_label = _format_market_label(mid)
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
) -> _BalanceFuseTotals:
    totals = _BalanceFuseTotals()
    seen_market_ids: set[int] = set()
    with ThreadPoolExecutor() as pool:
        futures: list[tuple[int, int, str, Future, Future]] = []
        for idx, balance_fuse in enumerate(balance_fuses, 1):
            market_id_str = _format_market_label(balance_fuse.market_id)
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
            # Deduplicate: only count each market_id once in totals
            # (multiple balance fuses can reference the same market,
            # but totalAssetsInMarket is a single storage slot per market).
            if market_id not in seen_market_ids:
                totals.raw_total += assets_in_market
                totals.per_market[market_id_str] = assets_in_market
                if asset_price_usd is not None:
                    totals.usd_total += (
                        assets_in_market / 10**decimals
                    ) * asset_price_usd
                seen_market_ids.add(market_id)
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
    return totals


def _print_substrates(  # noqa: C901
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    balance_fuses: list,
    chain_id: int,
    api_key: str | None,
) -> set[str]:
    # Phase 1: fetch all substrates in parallel
    with ThreadPoolExecutor() as pool:
        substrate_futures: list[tuple[str, int, Future]] = []
        for balance_fuse in balance_fuses:
            market_id_str = _format_market_label(balance_fuse.market_id)
            fut = pool.submit(
                plasma_vault.get_market_substrates(balance_fuse.market_id).call
            )
            substrate_futures.append((market_id_str, balance_fuse.market_id, fut))

    # Collect substrates and identify addresses to resolve
    market_substrates: list[tuple[str, int, list]] = []
    all_addresses: set[str] = set()
    for market_id_str, mid, fut in substrate_futures:
        if not (substrates := fut.result()):
            continue
        market_substrates.append((market_id_str, mid, substrates))
        for sub in substrates:
            sub_info = _format_substrate(sub, market_id=mid)
            if sub_info.address:
                all_addresses.add(sub_info.address)

    if not market_substrates:
        click.echo("  (none)")
        return {a.lower() for a in all_addresses}

    # Phase 2: resolve all symbols + contract names in parallel
    with ThreadPoolExecutor() as pool:
        symbol_futs = {
            addr: pool.submit(_resolve_token_symbol, ctx, addr)
            for addr in all_addresses
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
            sub_info = _format_substrate(sub, market_id=mid)
            if sub_info.address:
                symbol = resolved_symbols.get(sub_info.address, "")
                contract = resolved_contracts.get(sub_info.address, "")
                details = _substrate_details(
                    symbol, contract, sub_info.type_label, sub_info.extra
                )
                click.echo(f"    {sub_info.address}{details}")
            elif sub_info.is_error:
                click.secho(f"    {sub_info.raw_hex} [encoding error]", fg="red")
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

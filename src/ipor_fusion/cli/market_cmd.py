"""`fusion market` command group — protocol-level market inspection.

Currently exposes `morpho-blue <marketId>`: fetches a Morpho Blue market's
parameters and state (loan/collateral/oracle/IRM/LLTV, totals, liquidity, APYs)
plus the MetaMorpho vaults supplying it (current allocation, supply caps, and
PublicAllocator flow caps + fees). Combines on-chain reads via `MorphoReader`
with the Morpho public GraphQL API for vault discovery.
"""

from __future__ import annotations

import json as json_lib

import click
from eth_typing import ChecksumAddress

from ipor_fusion.chains import CHAIN_NAMES
from ipor_fusion.cli.config_store import load_config
from ipor_fusion.cli.options import ADDRESS, CHAIN, _resolve_provider
from ipor_fusion.cli.vault_rendering import _format_amount, _print_table
from ipor_fusion.core.context import Web3Context
from ipor_fusion.market_info import (
    build_meta_morpho_json,
    build_morpho_blue_market_json,
)
from ipor_fusion.morpho_api import (
    PUBLIC_ALLOCATOR_ADDRESSES,
    MorphoApiError,
    MorphoApiMarket,
    VaultAllocation,
    VaultV1Info,
    VaultV2Cap,
    VaultV2Info,
    fetch_market,
    fetch_vault,
)
from ipor_fusion.readers.morpho import (
    WAD,
    MorphoMarket,
    MorphoMarketParams,
    MorphoMarketRates,
    MorphoReader,
    morpho_blue_address,
)
from ipor_fusion.types import ChainId, MorphoBlueMarketId, parse_morpho_market_id
from ipor_fusion.vault_info import BLOCK_EXPLORER_URLS


class MarketIdType(click.ParamType):
    """A 32-byte Morpho market ID (64 hex chars, with or without 0x prefix)."""

    name = "marketId"

    def convert(self, value, param, ctx):  # type: ignore[override]
        if not isinstance(value, str):
            self.fail(f"expected string, got {type(value).__name__}", param, ctx)
        try:
            return parse_morpho_market_id(value)
        except ValueError as exc:
            self.fail(str(exc), param, ctx)


MARKET_ID = MarketIdType()


@click.group()
def market() -> None:
    """Inspect lending markets across protocols."""


@market.command("morpho-blue")
@click.argument("market_id", type=MARKET_ID)
@click.option(
    "--chain",
    "chain_id",
    type=CHAIN,
    required=True,
    help="Chain ID or name (ethereum, base, arbitrum, ...).",
)
@click.option(
    "--block",
    type=int,
    default=None,
    help="Block number for on-chain reads (default: latest).",
)
@click.option(
    "--no-api",
    is_flag=True,
    default=False,
    help="Skip Morpho API call (no vault discovery, no PublicAllocator info).",
)
@click.option(
    "--vault",
    "vault_filter",
    type=ADDRESS,
    default=None,
    help="Show only this vault in the connected-vaults section.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of a table.",
)
def morpho_blue(
    market_id: MorphoBlueMarketId,
    chain_id: ChainId,
    block: int | None,
    no_api: bool,
    vault_filter: str | None,
    json_output: bool,
) -> None:
    """Inspect a Morpho Blue market by its 32-byte market ID.

    Returns market parameters (loan/collateral/oracle/IRM/LLTV), on-chain totals
    + liquidity + APYs, and — unless --no-api is set — the list of MetaMorpho
    vaults supplying this market with their PublicAllocator config (so you can
    see which vault to call `reallocateTo` on, with what fee, and up to what
    flow cap).
    """
    cfg = load_config()
    provider_url = _resolve_provider(cfg, chain_id)
    ctx = Web3Context.from_url(provider_url)
    if block is not None:
        ctx.default_block = block
    reader = MorphoReader(ctx, morpho_blue_address(chain_id))
    mid = market_id
    params, state = reader.require_market(mid)
    rates = reader.rates_from(state, params)

    api_market: MorphoApiMarket | None = None
    api_error: str | None = None
    if not no_api:
        try:
            api_market = fetch_market(market_id, chain_id)
        except MorphoApiError as exc:
            api_error = str(exc)

    public_allocator = PUBLIC_ALLOCATOR_ADDRESSES.get(chain_id)

    if json_output:
        click.echo(
            json_lib.dumps(
                build_morpho_blue_market_json(
                    market_id,
                    chain_id,
                    params,
                    state,
                    rates,
                    api_market,
                    api_error,
                    public_allocator,
                ),
                indent=2,
            )
        )
        return

    _print_market(market_id, chain_id, params, state, rates, public_allocator)
    if no_api:
        click.echo("\nConnected vaults: skipped (--no-api).")
        return
    if api_error:
        click.secho(f"\nConnected vaults: unavailable ({api_error}).", fg="yellow")
        return
    assert api_market is not None  # noqa: S101  # narrowing; guaranteed by guard above
    _print_vaults(api_market, vault_filter, params, public_allocator)


def _print_market(
    market_id: MorphoBlueMarketId,
    chain_id: ChainId,
    params: MorphoMarketParams,
    state: MorphoMarket,
    rates: MorphoMarketRates,
    public_allocator: str | None,
) -> None:
    chain_name = CHAIN_NAMES.get(chain_id, str(chain_id))
    explorer = BLOCK_EXPLORER_URLS.get(chain_id, "")
    liquidity = state.total_supply_assets - state.total_borrow_assets
    click.secho(f"Morpho Blue market {market_id}", bold=True)
    click.echo(f"  Chain:           {chain_name} (id {chain_id})")
    if explorer:
        click.echo(
            f"  App:             https://app.morpho.org/{chain_name}/market/{market_id}"
        )
    click.echo(f"  Loan token:      {params.loan_token}")
    click.echo(f"  Collateral:      {params.collateral_token}")
    click.echo(f"  Oracle:          {params.oracle}")
    click.echo(f"  IRM:             {params.irm}")
    click.echo(f"  LLTV:            {params.lltv / WAD * 100:.2f}%")
    if public_allocator:
        click.echo(f"  PublicAllocator: {public_allocator}")
    else:
        click.echo("  PublicAllocator: (not deployed on this chain)")
    click.echo("")
    click.echo("State (on-chain):")
    click.echo(f"  Total supply:    {state.total_supply_assets:,} (raw)")
    click.echo(f"  Total borrow:    {state.total_borrow_assets:,} (raw)")
    click.echo(f"  Liquidity:       {liquidity:,} (raw)")
    click.echo(f"  Utilization:     {rates.utilization * 100:.2f}%")
    click.echo(f"  Borrow APY:      {rates.borrow_apy * 100:.2f}%")
    click.echo(f"  Supply APY:      {rates.supply_apy * 100:.2f}%")
    click.echo(f"  Protocol fee:    {state.fee / WAD * 100:.2f}%")


def _print_vaults(
    api_market: MorphoApiMarket,
    vault_filter: str | None,
    params: MorphoMarketParams,
    public_allocator: str | None,
) -> None:
    decimals = api_market.loan_decimals
    symbol = api_market.loan_symbol or "loan"
    click.echo("")
    click.secho(f"Connected MetaMorpho vaults ({len(api_market.vaults)}):", bold=True)
    if not api_market.vaults:
        click.echo("  (none — no vault has this market in its supplyQueue)")
        click.echo("")
        click.echo("Reallocate parameters (MarketParams):")
        _print_reallocate_hint(params)
        return

    matched = [
        v
        for v in api_market.vaults
        if not vault_filter or v.vault_address.lower() == vault_filter.lower()
    ]
    if not matched:
        click.echo(f"  (no match for --vault {vault_filter})")
        click.echo("")
        click.echo("Reallocate parameters (MarketParams):")
        _print_reallocate_hint(params)
        return

    summary_rows = [_vault_row(v, decimals, symbol, public_allocator) for v in matched]
    _print_table(
        ("Vault", "Address", "Allocation", "Cap", "Public reallocate"),
        summary_rows,
    )
    click.echo("")
    for vault in matched:
        _print_vault_allocators(vault, public_allocator)
    click.echo("Reallocate parameters (MarketParams):")
    _print_reallocate_hint(params)


def _vault_row(
    vault: VaultAllocation,
    loan_decimals: int,
    loan_symbol: str,
    public_allocator: str | None,
) -> tuple[str, ...]:
    alloc = f"{_format_amount(vault.supply_assets, loan_decimals)} {loan_symbol}"
    cap = (
        f"{_format_amount(vault.supply_cap, loan_decimals)} {loan_symbol}"
        if vault.supply_cap > 0
        else "0"
    )
    public = _format_public_status(vault, loan_decimals, loan_symbol, public_allocator)
    name = vault.vault_symbol or vault.vault_name or "?"
    return (name, vault.vault_address, alloc, cap, public)


def _format_public_status(
    vault: VaultAllocation,
    loan_decimals: int,
    loan_symbol: str,
    public_allocator: str | None,
) -> str:
    role_granted = public_allocator is not None and any(
        a.lower() == public_allocator.lower() for a in vault.allocators
    )
    if vault.flow_cap is None:
        return "n/a (no flow cap configured)"
    if not role_granted:
        return "blocked (allocator role not granted to PublicAllocator)"
    if vault.flow_cap.max_in == 0:
        return "disabled (maxIn=0)"
    fee_eth = vault.flow_cap.fee_wei / WAD
    return (
        f"in {_format_amount(vault.flow_cap.max_in, loan_decimals)} {loan_symbol}, "
        f"fee {fee_eth:.6f} ETH"
    )


def _print_vault_allocators(
    vault: VaultAllocation, public_allocator: str | None
) -> None:
    name = vault.vault_symbol or vault.vault_name or vault.vault_address
    click.echo(f"Allocators for {name} ({vault.vault_address}):")
    if not vault.allocators:
        click.echo("  (none)")
    else:
        for addr in vault.allocators:
            tag = ""
            if public_allocator and addr.lower() == public_allocator.lower():
                tag = "  [PublicAllocator — public reallocate enabled]"
            elif (
                vault.flow_cap
                and vault.flow_cap.admin
                and addr.lower() == vault.flow_cap.admin.lower()
            ):
                tag = "  [PublicAllocator admin]"
            click.echo(f"  {addr}{tag}")
    click.echo("")


def _print_reallocate_hint(params: MorphoMarketParams) -> None:
    click.echo(f"  loanToken:       {params.loan_token}")
    click.echo(f"  collateralToken: {params.collateral_token}")
    click.echo(f"  oracle:          {params.oracle}")
    click.echo(f"  irm:             {params.irm}")
    click.echo(f"  lltv:            {params.lltv}")


# ───────────── meta-morpho subcommand ─────────────


@market.command("meta-morpho")
@click.argument("vault_address", type=ADDRESS)
@click.option(
    "--chain",
    "chain_id",
    type=CHAIN,
    required=True,
    help="Chain ID or name (ethereum, base, arbitrum, ...).",
)
@click.option(
    "--market",
    "market_filter",
    type=MARKET_ID,
    default=None,
    help="Filter caps/allocations to a single Morpho Blue market ID.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of a table.",
)
def meta_morpho(
    vault_address: ChecksumAddress,
    chain_id: ChainId,
    market_filter: MorphoBlueMarketId | None,
    json_output: bool,
) -> None:
    """Inspect a MetaMorpho V1 or Morpho Vault V2 by address.

    Auto-detects vault version. Shows roles (owner/curator/allocators/sentinels),
    fees, adapters (V2) or supply allocations (V1), and per-market caps with
    remaining headroom. Use --market to focus on a single Morpho Blue market —
    useful when deciding whether to push liquidity into it via reallocate.
    """
    try:
        info = fetch_vault(vault_address, chain_id)
    except MorphoApiError as exc:
        raise click.UsageError(str(exc)) from exc

    if json_output:
        click.echo(json_lib.dumps(build_meta_morpho_json(info, chain_id), indent=2))
        return

    if isinstance(info, VaultV2Info):
        _print_vault_v2(info, chain_id, market_filter)
    else:
        _print_vault_v1(info, chain_id, market_filter)


def _print_vault_v2(
    vault: VaultV2Info, chain_id: ChainId, market_filter: MorphoBlueMarketId | None
) -> None:
    chain_name = CHAIN_NAMES.get(chain_id, str(chain_id))
    click.secho(f"Morpho Vault V2 — {vault.name} ({vault.symbol})", bold=True)
    click.echo(f"  Address:          {vault.address}")
    click.echo(f"  Chain:            {chain_name} (id {chain_id})")
    click.echo(
        f"  Asset:            {vault.asset_symbol} "
        f"({_format_amount(vault.total_assets, vault.asset_decimals)} total, "
        f"{_format_amount(vault.idle_assets, vault.asset_decimals)} idle)"
    )
    click.echo(
        f"  Liquidity:        {_format_amount(vault.liquidity, vault.asset_decimals)} {vault.asset_symbol}"
    )
    click.echo(f"  Share price:      {vault.share_price:.6f}")
    click.echo(f"  Max APY:          {vault.max_apy * 100:.2f}%")
    click.echo("")
    click.echo("Roles:")
    click.echo(f"  Owner:            {vault.owner}")
    click.echo(f"  Curator:          {vault.curator or ''}")
    click.echo(
        f"  Performance fee:  {vault.performance_fee * 100:.2f}% → {vault.performance_fee_recipient or ''}"
    )
    click.echo(
        f"  Management fee:   {vault.management_fee * 100:.2f}% → {vault.management_fee_recipient or ''}"
    )
    click.echo(f"  Allocators ({len(vault.allocators)}):")
    for addr in vault.allocators or ["(none)"]:
        click.echo(f"    {addr}")
    click.echo(f"  Sentinels ({len(vault.sentinels)}):")
    for addr in vault.sentinels or ["(none)"]:
        click.echo(f"    {addr}")
    click.echo("")
    click.echo("Adapters:")
    if not vault.adapters:
        click.echo("  (none)")
    else:
        adapter_rows: list[tuple[str, ...]] = [
            (
                a.adapter_type or "?",
                a.address,
                _format_amount(a.assets, vault.asset_decimals)
                + " "
                + vault.asset_symbol,
                a.inner_vault or "",
            )
            for a in vault.adapters
        ]
        _print_table(("Type", "Adapter", "Assets", "Inner vault"), adapter_rows)
    if vault.liquidity_adapter:
        click.echo("")
        click.echo(f"  Liquidity adapter: {vault.liquidity_adapter.address}")
    click.echo("")

    market_caps = [c for c in vault.caps if c.cap_type == "MarketV1"]
    other_caps = [c for c in vault.caps if c.cap_type != "MarketV1"]
    if market_filter:
        market_caps = [c for c in market_caps if c.market_id == market_filter]

    click.secho(f"Morpho Blue market caps ({len(market_caps)}):", bold=True)
    _print_market_caps(market_caps, vault.asset_symbol, vault.asset_decimals)
    click.echo("")
    if not market_filter:
        click.secho(f"Other caps ({len(other_caps)}):", bold=True)
        _print_other_caps(other_caps, vault.asset_symbol, vault.asset_decimals)


def _print_market_caps(
    caps: list[VaultV2Cap], asset_symbol: str, asset_decimals: int
) -> None:
    if not caps:
        click.echo("  (none)")
        return
    rows: list[tuple[str, ...]] = []
    for cap in caps:
        market_short = (cap.market_id or "?")[:18] + "…"
        rows.append(
            (
                market_short,
                _format_amount(cap.absolute_cap, asset_decimals),
                _format_amount(cap.allocation, asset_decimals),
                _format_amount(cap.room, asset_decimals),
                f"LLTV {cap.lltv / WAD * 100:.0f}%" if cap.lltv else "",
            )
        )
    _print_table(
        ("Market (short)", f"Cap ({asset_symbol})", "Allocated", "Room", "LLTV"),
        rows,
    )
    click.echo("")
    for cap in caps:
        if not cap.market_id:
            continue
        click.echo(f"Market {cap.market_id}")
        click.echo(f"  loanToken:       {cap.loan_token}")
        click.echo(f"  collateralToken: {cap.collateral_token}")
        click.echo(f"  oracle:          {cap.oracle}")
        click.echo(f"  irm:             {cap.irm}")
        click.echo(f"  lltv:            {cap.lltv}")
        click.echo(
            f"  cap / alloc / room: "
            f"{_format_amount(cap.absolute_cap, asset_decimals)} / "
            f"{_format_amount(cap.allocation, asset_decimals)} / "
            f"{_format_amount(cap.room, asset_decimals)} {asset_symbol}"
        )


def _print_other_caps(
    caps: list[VaultV2Cap], asset_symbol: str, asset_decimals: int
) -> None:
    if not caps:
        click.echo("  (none)")
        return
    rows: list[tuple[str, ...]] = [
        (
            c.cap_type,
            c.cap_id[:18] + "…",
            _format_amount(c.absolute_cap, asset_decimals),
            _format_amount(c.allocation, asset_decimals),
            _format_amount(c.room, asset_decimals),
        )
        for c in caps
    ]
    _print_table(
        ("Type", "Cap ID", f"Cap ({asset_symbol})", "Allocated", "Room"),
        rows,
    )


def _print_vault_v1(
    vault: VaultV1Info, chain_id: ChainId, market_filter: MorphoBlueMarketId | None
) -> None:
    chain_name = CHAIN_NAMES.get(chain_id, str(chain_id))
    public_allocator = PUBLIC_ALLOCATOR_ADDRESSES.get(chain_id)
    role_granted = public_allocator is not None and any(
        a.lower() == public_allocator.lower() for a in vault.allocators
    )
    click.secho(f"MetaMorpho V1 — {vault.name} ({vault.symbol})", bold=True)
    click.echo(f"  Address:          {vault.address}")
    click.echo(f"  Chain:            {chain_name} (id {chain_id})")
    click.echo(
        f"  Asset:            {vault.asset_symbol} "
        f"({_format_amount(vault.total_assets, vault.asset_decimals)} total)"
    )
    click.echo(
        f"  Performance fee:  {vault.fee_wad / WAD * 100:.2f}% → {vault.fee_recipient or ''}"
    )
    click.echo("")
    click.echo("Roles:")
    click.echo(f"  Owner:            {vault.owner}")
    click.echo(f"  Curator:          {vault.curator or ''}")
    click.echo(f"  Guardian:         {vault.guardian or ''}")
    click.echo(f"  Allocators ({len(vault.allocators)}):")
    for addr in vault.allocators or ["(none)"]:
        tag = (
            "  [PublicAllocator]"
            if public_allocator and addr.lower() == public_allocator.lower()
            else ""
        )
        click.echo(f"    {addr}{tag}")
    click.echo("")
    click.echo("Public reallocate:")
    if not vault.public_allocator:
        click.echo("  (no PublicAllocator config)")
    else:
        fee_eth = vault.public_allocator.fee_wei / WAD
        status = "enabled" if role_granted else "blocked (role not granted)"
        click.echo(f"  Status:           {status}")
        click.echo(f"  Fee:              {fee_eth:.6f} ETH per call")
        click.echo(f"  Admin:            {vault.public_allocator.admin}")
    click.echo("")

    allocations = vault.allocations
    if market_filter:
        allocations = [a for a in allocations if a.market_id == market_filter]
    click.secho(f"Allocations ({len(allocations)}):", bold=True)
    if not allocations:
        click.echo("  (none)")
        return
    rows: list[tuple[str, ...]] = []
    for alloc in allocations:
        flow = vault.public_allocator_flow_caps.get(alloc.market_id.lower(), (0, 0))
        flow_str = (
            f"in {_format_amount(flow[0], alloc.loan_decimals)}" if flow[0] else "—"
        )
        rows.append(
            (
                alloc.market_id[:18] + "…",
                f"{alloc.loan_symbol}/{alloc.collateral_symbol}",
                _format_amount(alloc.supply_assets, alloc.loan_decimals),
                _format_amount(alloc.supply_cap, alloc.loan_decimals),
                _format_amount(alloc.cap_room, alloc.loan_decimals),
                flow_str,
            )
        )
    _print_table(
        ("Market", "Pair", "Allocated", "Cap", "Room", "Public flow in"),
        rows,
    )

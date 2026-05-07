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

from ipor_fusion.cli.config_store import load_config
from ipor_fusion.cli.morpho_api import (
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
from ipor_fusion.cli.vault_cmd import (
    ADDRESS,
    BLOCK_EXPLORER_URLS,
    CHAIN,
    CHAIN_NAMES,
    _resolve_provider,
)
from ipor_fusion.cli.vault_rendering import _format_amount, _print_table
from ipor_fusion.core.context import Web3Context
from ipor_fusion.readers.lending_health import MORPHO_BLUE_ADDRESS
from ipor_fusion.readers.morpho import (
    WAD,
    MorphoMarket,
    MorphoMarketParams,
    MorphoMarketRates,
    MorphoReader,
)
from ipor_fusion.types import MorphoBlueMarketId


class MarketIdType(click.ParamType):
    """A 32-byte Morpho market ID (64 hex chars, with or without 0x prefix)."""

    name = "marketId"

    def convert(self, value, param, ctx):  # type: ignore[override]
        if not isinstance(value, str):
            self.fail(f"expected string, got {type(value).__name__}", param, ctx)
        raw = value.strip().removeprefix("0x").removeprefix("0X")
        if len(raw) != 64 or not all(c in "0123456789abcdefABCDEF" for c in raw):
            self.fail(
                f"invalid Morpho market ID (expected 32-byte hex): {value}", param, ctx
            )
        return raw.lower()


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
    market_id: str,
    chain_id: int,
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
    reader = MorphoReader(ctx, MORPHO_BLUE_ADDRESS)
    mid = MorphoBlueMarketId(market_id)
    params = reader.market_params(mid)
    state = reader.market(mid)
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
                _build_json(
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
    assert api_market is not None
    _print_vaults(api_market, vault_filter, params, public_allocator)


def _print_market(
    market_id: str,
    chain_id: int,
    params: MorphoMarketParams,
    state: MorphoMarket,
    rates: MorphoMarketRates,
    public_allocator: str | None,
) -> None:
    chain_name = CHAIN_NAMES.get(chain_id, str(chain_id))
    explorer = BLOCK_EXPLORER_URLS.get(chain_id, "")
    liquidity = state.total_supply_assets - state.total_borrow_assets
    click.secho(f"Morpho Blue market 0x{market_id}", bold=True)
    click.echo(f"  Chain:           {chain_name} (id {chain_id})")
    if explorer:
        click.echo(
            f"  App:             https://app.morpho.org/{chain_name}/market/0x{market_id}"
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


def _build_json(
    market_id: str,
    chain_id: int,
    params: MorphoMarketParams,
    state: MorphoMarket,
    rates: MorphoMarketRates,
    api_market: MorphoApiMarket | None,
    api_error: str | None,
    public_allocator: str | None,
) -> dict:
    out: dict = {
        "market_id": "0x" + market_id,
        "chain_id": chain_id,
        "public_allocator": public_allocator,
        "market_params": {
            "loan_token": params.loan_token,
            "collateral_token": params.collateral_token,
            "oracle": params.oracle,
            "irm": params.irm,
            "lltv": str(params.lltv),
        },
        "state": {
            "total_supply_assets": str(state.total_supply_assets),
            "total_supply_shares": str(state.total_supply_shares),
            "total_borrow_assets": str(state.total_borrow_assets),
            "total_borrow_shares": str(state.total_borrow_shares),
            "liquidity_assets": str(
                state.total_supply_assets - state.total_borrow_assets
            ),
            "fee_wad": str(state.fee),
            "last_update": state.last_update,
        },
        "rates": {
            "rate_per_second_wad": str(rates.rate_per_second_wad),
            "utilization": rates.utilization,
            "borrow_apy": rates.borrow_apy,
            "supply_apy": rates.supply_apy,
        },
    }
    if api_error:
        out["api_error"] = api_error
    if api_market is not None:
        out["loan_asset"] = {
            "address": api_market.loan_token,
            "symbol": api_market.loan_symbol,
            "decimals": api_market.loan_decimals,
        }
        out["collateral_asset"] = {
            "address": api_market.collateral_token,
            "symbol": api_market.collateral_symbol,
            "decimals": api_market.collateral_decimals,
        }
        out["vaults"] = [_vault_to_json(v) for v in api_market.vaults]
    return out


def _vault_to_json(vault: VaultAllocation) -> dict:
    entry: dict = {
        "address": vault.vault_address,
        "name": vault.vault_name,
        "symbol": vault.vault_symbol,
        "asset": {"symbol": vault.asset_symbol, "decimals": vault.asset_decimals},
        "total_assets": str(vault.total_assets),
        "supply_assets": str(vault.supply_assets),
        "supply_cap": str(vault.supply_cap),
        "allocators": list(vault.allocators),
    }
    if vault.flow_cap is not None:
        entry["public_allocator_config"] = {
            "fee_wei": str(vault.flow_cap.fee_wei),
            "max_in": str(vault.flow_cap.max_in),
            "max_out": str(vault.flow_cap.max_out),
            "admin": vault.flow_cap.admin,
        }
    else:
        entry["public_allocator_config"] = None
    return entry


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
    type=str,
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
    vault_address: str,
    chain_id: int,
    market_filter: str | None,
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

    market_filter_norm = (
        ("0x" + market_filter.removeprefix("0x").lower()) if market_filter else None
    )

    if json_output:
        click.echo(json_lib.dumps(_meta_morpho_json(info, chain_id), indent=2))
        return

    if isinstance(info, VaultV2Info):
        _print_vault_v2(info, chain_id, market_filter_norm)
    else:
        _print_vault_v1(info, chain_id, market_filter_norm)


def _print_vault_v2(
    vault: VaultV2Info, chain_id: int, market_filter: str | None
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
    click.echo(f"  Curator:          {vault.curator}")
    click.echo(
        f"  Performance fee:  {vault.performance_fee * 100:.2f}% → {vault.performance_fee_recipient}"
    )
    click.echo(
        f"  Management fee:   {vault.management_fee * 100:.2f}% → {vault.management_fee_recipient}"
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
        market_caps = [
            c for c in market_caps if (c.market_id or "").lower() == market_filter
        ]

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
    vault: VaultV1Info, chain_id: int, market_filter: str | None
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
        f"  Performance fee:  {vault.fee_wad / WAD * 100:.2f}% → {vault.fee_recipient}"
    )
    click.echo("")
    click.echo("Roles:")
    click.echo(f"  Owner:            {vault.owner}")
    click.echo(f"  Curator:          {vault.curator}")
    click.echo(f"  Guardian:         {vault.guardian}")
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
        allocations = [a for a in allocations if a.market_id.lower() == market_filter]
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


def _meta_morpho_json(info: VaultV1Info | VaultV2Info, chain_id: int) -> dict:
    if isinstance(info, VaultV2Info):
        return {
            "version": "v2",
            "chain_id": chain_id,
            "address": info.address,
            "name": info.name,
            "symbol": info.symbol,
            "asset": {
                "address": info.asset_address,
                "symbol": info.asset_symbol,
                "decimals": info.asset_decimals,
            },
            "total_assets": str(info.total_assets),
            "idle_assets": str(info.idle_assets),
            "liquidity": str(info.liquidity),
            "share_price": info.share_price,
            "max_apy": info.max_apy,
            "performance_fee": info.performance_fee,
            "performance_fee_recipient": info.performance_fee_recipient,
            "management_fee": info.management_fee,
            "management_fee_recipient": info.management_fee_recipient,
            "owner": info.owner,
            "curator": info.curator,
            "allocators": list(info.allocators),
            "sentinels": list(info.sentinels),
            "liquidity_adapter": (
                {
                    "address": info.liquidity_adapter.address,
                    "type": info.liquidity_adapter.adapter_type,
                    "assets": str(info.liquidity_adapter.assets),
                    "inner_vault": info.liquidity_adapter.inner_vault,
                }
                if info.liquidity_adapter
                else None
            ),
            "adapters": [
                {
                    "address": a.address,
                    "type": a.adapter_type,
                    "assets": str(a.assets),
                    "inner_vault": a.inner_vault,
                }
                for a in info.adapters
            ],
            "caps": [_v2_cap_json(c) for c in info.caps],
        }
    return {
        "version": "v1",
        "chain_id": chain_id,
        "address": info.address,
        "name": info.name,
        "symbol": info.symbol,
        "asset": {
            "address": info.asset_address,
            "symbol": info.asset_symbol,
            "decimals": info.asset_decimals,
        },
        "total_assets": str(info.total_assets),
        "fee_wad": str(info.fee_wad),
        "owner": info.owner,
        "curator": info.curator,
        "guardian": info.guardian,
        "fee_recipient": info.fee_recipient,
        "allocators": list(info.allocators),
        "public_allocator": (
            {
                "fee_wei": str(info.public_allocator.fee_wei),
                "admin": info.public_allocator.admin,
                "flow_caps": {
                    mid: {"max_in": str(mi), "max_out": str(mo)}
                    for mid, (mi, mo) in info.public_allocator_flow_caps.items()
                },
            }
            if info.public_allocator
            else None
        ),
        "allocations": [
            {
                "market_id": a.market_id,
                "lltv": str(a.lltv),
                "loan_symbol": a.loan_symbol,
                "loan_decimals": a.loan_decimals,
                "collateral_symbol": a.collateral_symbol,
                "supply_assets": str(a.supply_assets),
                "supply_cap": str(a.supply_cap),
                "cap_room": str(a.cap_room),
                "market_supply_apy": a.market_supply_apy,
            }
            for a in info.allocations
        ],
    }


def _v2_cap_json(cap: VaultV2Cap) -> dict:
    return {
        "cap_id": cap.cap_id,
        "type": cap.cap_type,
        "id_data": cap.id_data,
        "absolute_cap": str(cap.absolute_cap),
        "relative_cap_wad": str(cap.relative_cap_wad),
        "allocation": str(cap.allocation),
        "room": str(cap.room),
        "market_id": cap.market_id,
        "loan_token": cap.loan_token,
        "collateral_token": cap.collateral_token,
        "oracle": cap.oracle,
        "irm": cap.irm,
        "lltv": str(cap.lltv) if cap.lltv is not None else None,
    }

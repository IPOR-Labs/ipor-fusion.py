from __future__ import annotations

from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor

import click
from eth_typing import HexAddress

from ipor_fusion.cli.vault_rendering import (
    _format_amount,
    _format_usd,
    _print_table,
    _substrate_details,
)
from ipor_fusion.readers.lending_health import LendingMarketHealth, VaultLendingHealth
from ipor_fusion.substrates import (
    decode_substrate,
    format_market_label,
)
from ipor_fusion.types import FuseAddress, MarketId, parse_hex_address
from ipor_fusion.vault_data import (
    GET_MARKET_SUBSTRATES,
    VaultData,
)
from ipor_fusion.vault_info import (
    _CAPABILITY_BALANCE_FUSES,
)

# Address to contract name, "" when unknown (`get_contract_name` bound to the
# chain, API key and the context's cache).
NameResolver = Callable[[HexAddress], str]


def _print_lending_health(data: VaultData) -> None:  # noqa: C901
    lh = data.lending_health
    has_health = lh is not None and lh.has_lending_positions
    has_breakdown = bool(data.morpho_positions) or bool(data.aave_positions)
    if not has_health and not has_breakdown:
        click.echo("Position Breakdown: (no lending positions)")
        return

    click.echo("Position Breakdown:")
    morpho_health, aave_health = _index_lending_health(lh)
    consumed_morpho_subs: set[str] = set()
    consumed_aave_mids: set[MarketId | str] = set()

    for ipor_mid, positions in (data.morpho_positions or {}).items():
        click.echo(f"  {format_market_label(ipor_mid)}:")
        for pb in positions:
            coll_sym = _token_symbol(data, pb.collateral_token)
            loan_sym = _token_symbol(data, pb.loan_token)
            click.echo(f"    morpho market {pb.market_id} ({coll_sym}/{loan_sym}):")
            click.echo(
                f"      Collateral:    {_format_token_amount(data, int(pb.collateral), pb.collateral_token)}"
            )
            click.echo(
                f"      Borrow:        {_format_token_amount(data, int(pb.borrow_assets), pb.loan_token)}"
            )
            click.echo(
                f"      Supply:        {_format_token_amount(data, int(pb.supply_assets), pb.loan_token)}"
            )
            sid = str(pb.market_id).lower().removeprefix("0x")
            consumed_morpho_subs.add(sid)
            if m := morpho_health.get(sid):
                _print_health_lines(m, indent="      ")

    for ipor_mid, aave_positions in (data.aave_positions or {}).items():
        click.echo(f"  {format_market_label(ipor_mid)}:")
        for ab in aave_positions:
            asset_symbol = _token_symbol(data, ab.asset)
            click.echo(f"    asset {ab.asset} ({asset_symbol}):")
            click.echo(
                f"      Supply:        {_format_token_amount(data, int(ab.supply), ab.asset)}"
            )
            click.echo(
                f"      Variable Debt: {_format_token_amount(data, int(ab.variable_debt), ab.asset)}"
            )
            if ab.stable_debt > 0:
                click.echo(
                    f"      Stable Debt:   {_format_token_amount(data, int(ab.stable_debt), ab.asset)}"
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
    lh: VaultLendingHealth | None,
) -> tuple[dict[str, LendingMarketHealth], dict[MarketId | str, LendingMarketHealth]]:
    """Split lending health rows by protocol for fast lookup during rendering.

    Morpho rows are keyed by morpho substrate id (one row per substrate).
    Aave V3 rows are keyed by IPOR market id (account-aggregated, one row per
    market); Aave V4 rows by Spoke address (account-aggregated per Spoke).
    """
    morpho: dict[str, LendingMarketHealth] = {}
    aave: dict[MarketId | str, LendingMarketHealth] = {}
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


def _print_health_lines(market: LendingMarketHealth, indent: str) -> None:
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


def _print_markets(data: VaultData, resolve_name: NameResolver) -> None:
    """Fuses, balance fuses, dependency graph and substrates."""
    _print_fuse_section(
        "Fuses",
        data.fuses,
        data.fuse_markets,
        data.market_substrates,
        resolve_name,
    )
    click.echo()

    _print_fuse_section(
        "Instant Withdrawal Fuses",
        data.instant_fuses,
        data.fuse_markets,
        data.market_substrates,
        resolve_name,
    )
    click.echo()

    _print_balance_fuses_table(data, resolve_name)
    click.echo()

    _print_dependency_graph(data)

    click.echo("Substrates per Market:")
    _print_market_substrates(data, resolve_name)
    click.echo()


def _token_symbol(data: VaultData, token_address: HexAddress) -> str:
    return data.token_symbols.get(parse_hex_address(token_address)) or "?"


def _format_token_amount(data: VaultData, raw: int, token_address: HexAddress) -> str:
    """Format a raw amount with the snapshot's symbol and decimals, suffixed
    with ` ($X.XX)` when the snapshot has the token's USD price."""
    key = parse_hex_address(token_address)
    symbol = data.token_symbols.get(key) or "?"
    decimals = data.token_decimals.get(key)
    if decimals is None:
        return f"{raw} raw ({symbol})"
    base = f"{_format_amount(raw, decimals)} {symbol}"
    if prices_usd := data.token_prices_usd:
        return f"{base}{_format_usd(raw, decimals, prices_usd.get(key))}"
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


def _print_fuse_section(
    title: str,
    fuses: Sequence[FuseAddress],
    fuse_markets: dict[FuseAddress, MarketId] | None,
    market_substrates: dict[MarketId, list[bytes]] | None,
    resolve_name: NameResolver,
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
    counts: dict[FuseAddress, int] = {}
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
        name_futs = {addr: pool.submit(resolve_name, addr) for addr in counts}
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


def _print_balance_fuses_table(data: VaultData, resolve_name: NameResolver) -> None:
    balance_fuses = data.balance_fuses
    decimals = data.asset_decimals
    asset_symbol = data.asset_symbol
    asset_price_usd = data.asset_price_usd
    with ThreadPoolExecutor() as pool:
        futures: list[tuple[int, MarketId, str, Future[str]]] = []
        for idx, balance_fuse in enumerate(balance_fuses, 1):
            market_id_str = format_market_label(balance_fuse.market_id)
            f_contract = pool.submit(resolve_name, balance_fuse.fuse)
            futures.append((idx, balance_fuse.market_id, market_id_str, f_contract))

        venue_rows: list[tuple[str, ...]] = []
        zero_rows: list[tuple[str, ...]] = []
        for idx, market_id, market_id_str, f_contract in futures:
            assets_in_market = data.market_balances[market_id]
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


def _print_market_substrates(data: VaultData, resolve_name: NameResolver) -> set[str]:
    if GET_MARKET_SUBSTRATES in data.unimplemented_getters:
        click.secho(
            f"  (unavailable: the vault predates {GET_MARKET_SUBSTRATES})",
            fg="yellow",
        )
        return set()
    return _print_substrates(data, resolve_name)


def _print_substrates(  # noqa: C901
    data: VaultData, resolve_name: NameResolver
) -> set[str]:
    substrates_by_market = data.market_substrates or {}
    balance_fuses = data.balance_fuses
    # Phase 1: collect each balance fuse's substrates and the addresses to resolve
    market_substrates: list[tuple[str, MarketId, list[bytes]]] = []
    all_addresses: set[HexAddress] = set()
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

    # Phase 2: resolve contract names in parallel; symbols are in the snapshot
    resolved_symbols = data.token_symbols
    with ThreadPoolExecutor() as pool:
        contract_futs = {
            addr: pool.submit(resolve_name, addr) for addr in all_addresses
        }
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

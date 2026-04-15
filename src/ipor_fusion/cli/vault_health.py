from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass, field

import click
from web3 import Web3

from ipor_fusion.cli.vault_fetcher import (
    _VaultData,
    _resolve_token_symbol,
    _safe_call,
)
from ipor_fusion.cli.vault_rendering import _format_amount, _format_usd, _print_table
from ipor_fusion.cli.vault_substrate import _format_substrate, _market_name
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.oracle import PriceOracleMiddleware
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.types import Shares


@dataclass
class _BalanceFuseTotals:
    raw_total: int = 0
    usd_total: float = 0.0
    per_market: dict[str, int] = field(default_factory=dict)


@dataclass
class _TokenInfo:
    symbol: str
    balance_str: str
    usd_value: float | None = None


@dataclass
class _TokenDetail:
    address: str
    symbol: str
    decimals: int | None
    balance: int | None
    price_usd: float | None
    usd_value: float | None
    note: str


@dataclass
class _Erc20Totals:
    raw_asset_total: int = 0
    usd_total: float = 0.0
    cached_bf_value: int = 0
    underlying_balance_raw: int = 0
    tokens_without_price: list[str] = field(default_factory=list)
    token_addrs_on_vault: set[str] = field(default_factory=set)
    token_info: dict[str, _TokenInfo] = field(default_factory=dict)
    token_details: list[_TokenDetail] = field(default_factory=list)


def _compute_erc20_balances(  # pylint: disable=too-complex
    ctx: Web3Context, plasma_vault: PlasmaVault, data: _VaultData
) -> _Erc20Totals:
    totals = _Erc20Totals()
    erc20_market = None
    for balance_fuse in data.balance_fuses:
        if _market_name(balance_fuse.market_id) == "ERC20_VAULT_BALANCE":
            erc20_market = balance_fuse.market_id
            break
    if erc20_market is None:
        return totals

    totals.cached_bf_value = plasma_vault.total_assets_in_market(erc20_market)

    substrates = plasma_vault.get_market_substrates(erc20_market)
    vault_addr = Web3.to_checksum_address(plasma_vault.address)
    oracle = PriceOracleMiddleware(
        ctx, Web3.to_checksum_address(data.price_oracle_addr)
    )

    erc20_substrate_addrs: set[str] = set()
    token_addrs: list[str] = []
    for sub in substrates:
        sub_info = _format_substrate(sub, market_id=erc20_market)
        if sub_info.address:
            token_addrs.append(sub_info.address)
            erc20_substrate_addrs.add(sub_info.address.lower())

    if not token_addrs:
        return totals

    with ThreadPoolExecutor() as pool:
        token_futures: dict[str, dict[str, Future]] = {}
        for addr in token_addrs:
            checksum = Web3.to_checksum_address(addr)
            token = ERC20(ctx, checksum)
            token_futures[addr] = {
                "symbol": pool.submit(_resolve_token_symbol, ctx, addr),
                "decimals": pool.submit(_safe_call, token.decimals),
                "balance": pool.submit(
                    _safe_call, lambda t=token: t.balance_of(vault_addr)
                ),
                "price": pool.submit(
                    _safe_call, lambda a=checksum: oracle.get_asset_price(a)
                ),
            }

        resolved: list[tuple] = []
        for addr in token_addrs:
            futs = token_futures[addr]
            resolved.append(
                (
                    addr,
                    futs["symbol"].result() or "?",
                    futs["decimals"].result(),
                    futs["balance"].result(),
                    futs["price"].result(),
                )
            )

        for addr, symbol, token_decimals, balance, price in resolved:
            if token_decimals is None or balance is None:
                continue
            if addr.lower() == data.asset.lower():
                totals.underlying_balance_raw = balance
            price_usd = price.readable() if price else None
            if balance > 0:
                totals.token_addrs_on_vault.add(addr.lower())
            if price_usd is not None and data.asset_price_usd:
                token_value_usd = (balance / 10**token_decimals) * price_usd
                totals.usd_total += token_value_usd
                totals.raw_asset_total += int(
                    token_value_usd / data.asset_price_usd * 10**data.asset_decimals
                )
            elif balance > 0:
                totals.tokens_without_price.append(f"{addr} ({symbol})")

        cached_usd = (
            (totals.cached_bf_value / 10**data.asset_decimals) * data.asset_price_usd
            if data.asset_price_usd
            else None
        )

        for addr, symbol, token_decimals, balance, price in resolved:
            if token_decimals is None or balance is None:
                totals.token_details.append(
                    _TokenDetail(addr, symbol, token_decimals, balance, None, None, "")
                )
                continue

            price_usd = price.readable() if price else None
            token_usd_val = (
                (balance / 10**token_decimals) * price_usd if price_usd else None
            )

            if balance > 0:
                totals.token_info[addr.lower()] = _TokenInfo(
                    symbol=symbol,
                    balance_str=_format_amount(balance, token_decimals),
                    usd_value=token_usd_val,
                )

            is_underlying = addr.lower() == data.asset.lower()
            in_bf = addr.lower() in erc20_substrate_addrs
            note = ""
            if is_underlying:
                note = "underlying asset"
            elif not in_bf:
                note = "not in ERC20_VAULT_BALANCE"
            elif balance == 0:
                note = "in ERC20_VAULT_BALANCE, balance=0"
            elif (
                price_usd
                and cached_usd is not None
                and (balance / 10**token_decimals * price_usd) > cached_usd
            ):
                usd_fmt = f"${balance / 10**token_decimals * price_usd:,.2f}"
                note = f"{usd_fmt} not reflected in totalAssets (stale cache)"
            elif in_bf:
                note = "in ERC20_VAULT_BALANCE"

            totals.token_details.append(
                _TokenDetail(
                    addr,
                    symbol,
                    token_decimals,
                    balance,
                    price_usd,
                    token_usd_val,
                    note,
                )
            )
    return totals


def _print_erc20_balances(
    ctx: Web3Context, plasma_vault: PlasmaVault, data: _VaultData
) -> _Erc20Totals:
    totals = _compute_erc20_balances(ctx, plasma_vault, data)
    if not totals.token_details:
        has_erc20_market = any(
            _market_name(bf.market_id) == "ERC20_VAULT_BALANCE"
            for bf in data.balance_fuses
        )
        click.echo(
            "  (none)" if has_erc20_market else "  (no ERC20_VAULT_BALANCE market)"
        )
        return totals

    rows: list[tuple[str, ...]] = []
    for td in totals.token_details:
        if td.decimals is None or td.balance is None:
            rows.append((td.address, td.symbol, "error", "", ""))
            continue
        balance_str = (
            f"{_format_amount(td.balance, td.decimals)}"
            f"{_format_usd(td.balance, td.decimals, td.price_usd)}"
        )
        rows.append(
            (
                td.address,
                td.symbol,
                balance_str,
                f"${td.price_usd:,.2f}" if td.price_usd else "N/A",
                td.note,
            )
        )
    _print_table(("Token", "Symbol", "Balance", "Price", "Note"), rows)
    return totals


@dataclass
class _ReconciliationData:
    bf_total_raw: int = 0
    bf_total_usd: float = 0.0
    underlying_raw: int = 0
    underlying_usd: float = 0.0
    erc20_total_raw: int = 0
    erc20_total_usd: float = 0.0
    sum_raw: int = 0
    sum_usd: float = 0.0
    on_chain_raw: int = 0
    on_chain_usd: float | None = None
    delta_raw: int = 0
    delta_usd: float = 0.0
    delta_percent: float = 0.0
    pending_withdrawal_raw: int = 0
    pending_withdrawal_usd: float = 0.0
    implied_market_total: int = 0


def _compute_reconciliation(
    data: _VaultData,
    bf_totals: _BalanceFuseTotals,
    erc20_totals: _Erc20Totals,
    plasma_vault: PlasmaVault | None = None,
) -> _ReconciliationData:
    decimals = data.asset_decimals
    price = data.asset_price_usd

    # Extract underlying-only value (balance fuses already price non-underlying
    # ERC20s via ERC20BalanceFuse, so adding all ERC20s would double-count).
    # Use the raw on-chain balance directly to avoid USD round-trip rounding.
    underlying_raw = erc20_totals.underlying_balance_raw
    underlying_usd = 0.0
    if underlying_raw > 0 and price:
        underlying_usd = (underlying_raw / 10**decimals) * price

    sum_raw = bf_totals.raw_total + underlying_raw
    sum_usd = bf_totals.usd_total + underlying_usd
    on_chain = data.total_assets
    delta_raw = sum_raw - on_chain
    delta_usd = abs(sum_usd - (on_chain / 10**decimals * price)) if price else 0
    pct = abs(delta_raw / on_chain * 100) if on_chain else 0.0

    # Pending withdrawal value: shares_to_release converted to underlying assets.
    # totalAssets uses a global storage slot (getTotalAssetsInAllMarkets) while
    # the CLI sums per-market totalAssetsInMarket values. These can diverge after
    # withdrawals with sharesToRelease > 0, because _updateMarketsBalances only
    # refreshes the markets touched by instant withdrawal fuses.
    pending_raw = 0
    pending_usd = 0.0
    if (
        data.withdraw_manager_data
        and data.withdraw_manager_data.shares_to_release > 0
        and plasma_vault is not None
    ):
        assets = _safe_call(
            lambda: plasma_vault.convert_to_assets(
                Shares(data.withdraw_manager_data.shares_to_release)
            )
        )
        if assets is not None:
            pending_raw = assets
            if price:
                pending_usd = (assets / 10**decimals) * price

    # Implied global market total: totalAssets - underlying.
    # Divergence from per-market sum indicates accumulated storage drift.
    implied_market_total = max(0, on_chain - underlying_raw)

    return _ReconciliationData(
        bf_total_raw=bf_totals.raw_total,
        bf_total_usd=bf_totals.usd_total,
        underlying_raw=underlying_raw,
        underlying_usd=underlying_usd,
        erc20_total_raw=erc20_totals.raw_asset_total,
        erc20_total_usd=erc20_totals.usd_total,
        sum_raw=sum_raw,
        sum_usd=sum_usd,
        on_chain_raw=on_chain,
        on_chain_usd=(on_chain / 10**decimals * price) if price else None,
        delta_raw=delta_raw,
        delta_usd=delta_usd,
        delta_percent=pct,
        pending_withdrawal_raw=pending_raw,
        pending_withdrawal_usd=pending_usd,
        implied_market_total=implied_market_total,
    )


def _print_reconciliation(
    data: _VaultData,
    bf_totals: _BalanceFuseTotals,
    erc20_totals: _Erc20Totals,
    plasma_vault: PlasmaVault | None = None,
) -> None:
    recon = _compute_reconciliation(data, bf_totals, erc20_totals, plasma_vault)
    decimals = data.asset_decimals
    sym = data.asset_symbol
    price = data.asset_price_usd

    fmt_bf = _format_amount(recon.bf_total_raw, decimals)
    fmt_underlying = _format_amount(recon.underlying_raw, decimals)
    fmt_sum = _format_amount(recon.sum_raw, decimals)
    fmt_onchain = _format_amount(recon.on_chain_raw, decimals)
    fmt_delta = _format_amount(abs(recon.delta_raw), decimals)

    usd_bf = f" (${recon.bf_total_usd:,.2f})" if price else ""
    usd_underlying = f" (${recon.underlying_usd:,.2f})" if price else ""
    usd_sum = f" (${recon.sum_usd:,.2f})" if price else ""
    usd_onchain = _format_usd(recon.on_chain_raw, decimals, price)
    usd_delta = f" (${recon.delta_usd:,.2f})" if price else ""

    click.echo("Balance Reconciliation:")
    click.echo(
        f"  Balance fuses total:  {fmt_bf} {sym}{usd_bf}   [sum balance fuses, cached]"
    )
    click.echo(
        f"  Underlying on vault:  {fmt_underlying} {sym}{usd_underlying}   [{sym} held directly]"
    )
    click.echo(f"  Sum:                  {fmt_sum} {sym}{usd_sum}")
    click.echo(f"  On-chain totalAssets: {fmt_onchain} {sym}{usd_onchain}")
    sign = "+" if recon.delta_raw >= 0 else "-"
    delta_line = (
        f"  Delta:                {sign}{fmt_delta} {sym}"
        f"{usd_delta}   ({recon.delta_percent:.2f}%)"
    )
    if recon.delta_percent > 1.0:
        click.secho(f"{delta_line} !!! MISMATCH", fg="red")
    else:
        click.echo(delta_line)

    # Per-market sum vs on-chain global market total divergence diagnostic
    market_divergence = recon.bf_total_raw - recon.implied_market_total
    if abs(market_divergence) > 10**decimals:
        fmt_implied = _format_amount(recon.implied_market_total, decimals)
        fmt_div = _format_amount(abs(market_divergence), decimals)
        click.secho(
            f"  Market storage drift: sum(per-market)={fmt_bf} vs "
            f"implied global={fmt_implied} {sym}, "
            f"divergence={fmt_div} {sym}",
            fg="yellow",
        )
        click.secho(
            "  → updateMarketsBalances() needed for all markets to resync",
            fg="yellow",
        )

    # Pending withdrawal context
    if recon.pending_withdrawal_raw > 0:
        fmt_pending = _format_amount(recon.pending_withdrawal_raw, decimals)
        usd_pending = f" (${recon.pending_withdrawal_usd:,.2f})" if price else ""
        click.echo(
            f"  Pending withdrawals:  {fmt_pending} {sym}{usd_pending}"
            f"   [sharesToRelease converted to assets]"
        )
        if recon.delta_raw > 0 and recon.pending_withdrawal_raw > 0:
            coverage = min(recon.pending_withdrawal_raw / recon.delta_raw, 1.0)
            if coverage > 0.5:
                click.secho(
                    f"  → Pending withdrawals explain " f"~{coverage:.0%} of the delta",
                    fg="yellow",
                )

    erc20_non_underlying = erc20_totals.raw_asset_total - (
        erc20_totals.underlying_balance_raw
        if erc20_totals.underlying_balance_raw > 0
        else 0
    )

    if erc20_totals.cached_bf_value > 0 or erc20_non_underlying > 0:
        cached_fmt = _format_amount(erc20_totals.cached_bf_value, decimals)
        direct_fmt = _format_amount(erc20_non_underlying, decimals)
        if erc20_non_underlying > 0 and erc20_totals.cached_bf_value == 0:
            click.secho(
                f"  ERC20_VAULT_BALANCE cached={cached_fmt}, "
                f"ERC20 direct={direct_fmt} — likely stale cache",
                fg="yellow",
            )
        elif erc20_totals.cached_bf_value > 0 and erc20_non_underlying > 0:
            cached_vs_direct = abs(erc20_totals.cached_bf_value - erc20_non_underlying)
            if cached_vs_direct / erc20_non_underlying > 0.1:
                click.secho(
                    f"  ERC20_VAULT_BALANCE cached={cached_fmt}, "
                    f"ERC20 direct={direct_fmt} — significant divergence",
                    fg="yellow",
                )


@dataclass
class _HealthCheckData:
    ok: list[str] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)


def _compute_health_check(  # pylint: disable=too-complex
    data: _VaultData,
    bf_totals: _BalanceFuseTotals,
    erc20_totals: _Erc20Totals,
    all_substrate_addrs: set[str],
    plasma_vault: PlasmaVault | None = None,
) -> _HealthCheckData:
    decimals = data.asset_decimals
    sym = data.asset_symbol
    underlying = data.asset.lower()
    result = _HealthCheckData()

    # Lending health warnings
    if data.lending_health and data.lending_health.has_lending_positions:
        for m in data.lending_health.markets:
            if m.ltv_usage_percent is None:
                continue
            ltv_str = f"{m.current_ltv:.4f}" if m.current_ltv is not None else "?"
            max_str = f"{m.max_ltv:.4f}"
            hf_str = (
                f", health_factor={m.health_factor:.4f}"
                if m.health_factor is not None
                else ""
            )
            line = (
                f"{m.protocol} {m.market_name}: "
                f"LTV {ltv_str}/{max_str} ({m.ltv_usage_percent:.1f}% usage)"
                f"{hf_str}"
            )
            if m.is_critical:
                result.warnings.append(f"CRITICAL — {line} — NEAR LIQUIDATION")
            elif m.is_warning:
                result.warnings.append(f"WARNING — {line} — approaching liquidation")
            else:
                result.ok.append(line)

    underlying_raw = erc20_totals.underlying_balance_raw
    expected = bf_totals.raw_total + underlying_raw
    if data.total_assets > 0:
        pct = abs(expected - data.total_assets) / data.total_assets * 100
        fmt_exp = _format_amount(expected, decimals)
        fmt_ta = _format_amount(data.total_assets, decimals)
        line = (
            f"Balance fuses + underlying = {fmt_exp} {sym} "
            f"vs totalAssets {fmt_ta} {sym} ({pct:.2f}%)"
        )
        if pct < 1.0:
            result.ok.append(line)
        else:
            result.warnings.append(line)
            recon = _compute_reconciliation(data, bf_totals, erc20_totals, plasma_vault)
            if recon.pending_withdrawal_raw > 0 and recon.delta_raw > 0:
                fmt_pend = _format_amount(recon.pending_withdrawal_raw, decimals)
                result.warnings.append(
                    f"Pending withdrawals: {fmt_pend} {sym} "
                    f"(sharesToRelease) — market storage may be stale"
                )
            implied_market = max(0, data.total_assets - underlying_raw)
            divergence = bf_totals.raw_total - implied_market
            if abs(divergence) > 10**decimals:
                fmt_div = _format_amount(abs(divergence), decimals)
                result.warnings.append(
                    f"Market storage drift: {fmt_div} {sym} between "
                    f"sum(per-market) and global total — "
                    f"updateMarketsBalances() needed for all markets"
                )

    uncovered: list[str] = []
    cached_usd = (
        erc20_totals.cached_bf_value / 10**decimals * data.asset_price_usd
        if data.asset_price_usd and erc20_totals.cached_bf_value > 0
        else 0
    )
    for addr in sorted(erc20_totals.token_addrs_on_vault):
        if addr == underlying:
            continue
        if not (info := erc20_totals.token_info.get(addr)):
            continue
        usd_str = f" (${info.usd_value:,.2f})" if info.usd_value else ""
        if addr not in all_substrate_addrs:
            uncovered.append(
                f"    {info.symbol:<16} {info.balance_str}{usd_str}"
                f" — not in any balance fuse substrate"
            )
        elif 0 < cached_usd < (info.usd_value or 0):
            uncovered.append(
                f"    {info.symbol:<16} {info.balance_str}{usd_str}"
                f" — {usd_str.strip()} not reflected in totalAssets (stale cache)"
            )
    if uncovered:
        result.warnings.append("ERC20 holdings not covered by balance fuses:")
        result.warnings.extend(uncovered)

    erc20_non_underlying = erc20_totals.raw_asset_total - underlying_raw
    if erc20_non_underlying > 0 and (
        erc20_totals.cached_bf_value == 0
        or abs(erc20_non_underlying - erc20_totals.cached_bf_value)
        / erc20_non_underlying
        > 0.1
    ):
        result.warnings.append(
            "updateMarketsBalances needed — ERC20_VAULT_BALANCE cache stale"
        )

    for token_ref in erc20_totals.tokens_without_price:
        result.warnings.append(f"No price feed for {token_ref}")

    return result


def _print_health_check(
    data: _VaultData,
    bf_totals: _BalanceFuseTotals,
    erc20_totals: _Erc20Totals,
    all_substrate_addrs: set[str],
    plasma_vault: PlasmaVault | None = None,
) -> None:
    health = _compute_health_check(
        data, bf_totals, erc20_totals, all_substrate_addrs, plasma_vault
    )
    click.echo("Health Check:")
    for line in health.ok:
        click.secho(f"  {line}", fg="green")
    if not health.ok and not health.warnings:
        click.secho("  All checks passed", fg="green")
    for line in health.warnings:
        click.secho(f"  {line}", fg="yellow")

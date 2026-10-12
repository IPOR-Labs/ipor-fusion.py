"""Rendering of the vault validation (`ipor_fusion.validation`) for
``fusion vault info`` and ``fusion vault validate``."""

from __future__ import annotations

from functools import partial

import click

from ipor_fusion.cli.vault_rendering import _format_amount, _format_usd, _print_table
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.substrates import market_name
from ipor_fusion.validation import (
    Erc20Holdings,
    VaultValidation,
    read_erc20_holdings,
)
from ipor_fusion.vault_data import VaultData, resolve_token_symbol
from ipor_fusion.vault_info import health_check_lists


def _read_erc20_holdings(
    ctx: Web3Context, plasma_vault: PlasmaVault, data: VaultData
) -> Erc20Holdings:
    """ERC20 holdings with token symbols from the CLI's contract cache."""
    return read_erc20_holdings(
        ctx, plasma_vault, data, partial(resolve_token_symbol, ctx)
    )


def _print_erc20_balances(data: VaultData, erc20: Erc20Holdings) -> None:
    if not erc20.tokens:
        has_erc20_market = any(
            market_name(bf.market_id) == "ERC20_VAULT_BALANCE"
            for bf in data.balance_fuses
        )
        click.echo(
            "  (none)" if has_erc20_market else "  (no ERC20_VAULT_BALANCE market)"
        )
        return

    rows: list[tuple[str, ...]] = []
    for token in erc20.tokens:
        if token.decimals is None or token.balance is None:
            rows.append((token.address, token.symbol, "error", "", ""))
            continue
        balance = (
            f"{_format_amount(token.balance, token.decimals)}"
            f"{_format_usd(token.balance, token.decimals, token.price_usd)}"
        )
        rows.append(
            (
                token.address,
                token.symbol,
                balance,
                f"${token.price_usd:,.2f}" if token.price_usd else "N/A",
                token.note,
            )
        )
    _print_table(("Token", "Symbol", "Balance", "Price", "Note"), rows)


def _print_reconciliation(data: VaultData, validation: VaultValidation) -> None:
    recon = validation.reconciliation
    erc20 = validation.erc20
    decimals = data.asset_decimals
    sym = data.asset_symbol
    price = data.asset_price_usd

    fmt_bf = _format_amount(recon.balance_fuses_total, decimals)
    fmt_underlying = _format_amount(recon.underlying_on_vault, decimals)
    fmt_sum = _format_amount(recon.sum, decimals)
    fmt_onchain = _format_amount(recon.on_chain_total_assets, decimals)
    fmt_delta = _format_amount(abs(recon.delta), decimals)

    usd_bf = f" (${recon.balance_fuses_total_usd:,.2f})" if price else ""
    usd_underlying = f" (${recon.underlying_on_vault_usd:,.2f})" if price else ""
    usd_sum = f" (${recon.sum_usd:,.2f})" if price else ""
    usd_onchain = _format_usd(recon.on_chain_total_assets, decimals, price)
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
    sign = "+" if recon.delta >= 0 else "-"
    delta_line = (
        f"  Delta:                {sign}{fmt_delta} {sym}"
        f"{usd_delta}   ({recon.delta_percent:.2f}%)"
    )
    if recon.delta_percent > 1.0:
        click.secho(f"{delta_line} !!! MISMATCH", fg="red")
    else:
        click.echo(delta_line)

    if abs(recon.market_storage_divergence) > 10**decimals:
        fmt_implied = _format_amount(recon.implied_market_total, decimals)
        fmt_div = _format_amount(abs(recon.market_storage_divergence), decimals)
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

    if recon.pending_withdrawals > 0:
        fmt_pending = _format_amount(recon.pending_withdrawals, decimals)
        usd_pending = f" (${recon.pending_withdrawals_usd:,.2f})" if price else ""
        click.echo(
            f"  Pending withdrawals:  {fmt_pending} {sym}{usd_pending}"
            f"   [sharesToRelease converted to assets]"
        )
        if recon.delta > 0:
            coverage = min(recon.pending_withdrawals / recon.delta, 1.0)
            if coverage > 0.5:
                click.secho(
                    f"  → Pending withdrawals explain ~{coverage:.0%} of the delta",
                    fg="yellow",
                )

    _print_erc20_cache_divergence(erc20, recon.underlying_on_vault, decimals)


def _print_erc20_cache_divergence(
    erc20: Erc20Holdings, underlying: int, decimals: int
) -> None:
    cached = erc20.market_cached_value
    direct = erc20.value_in_asset - underlying
    if cached <= 0 and direct <= 0:
        return
    cached_fmt = _format_amount(cached, decimals)
    direct_fmt = _format_amount(direct, decimals)
    if direct > 0 and cached == 0:
        click.secho(
            f"  ERC20_VAULT_BALANCE cached={cached_fmt}, "
            f"ERC20 direct={direct_fmt} — likely stale cache",
            fg="yellow",
        )
    elif cached > 0 and direct > 0 and abs(cached - direct) / direct > 0.1:
        click.secho(
            f"  ERC20_VAULT_BALANCE cached={cached_fmt}, "
            f"ERC20 direct={direct_fmt} — significant divergence",
            fg="yellow",
        )


def _print_health_check(validation: VaultValidation) -> None:
    ok, warnings, criticals = health_check_lists(validation)
    click.echo("Health Check:")
    for line in ok:
        click.secho(f"  {line}", fg="green")
    if not ok and not warnings and not criticals:
        click.secho("  All checks passed", fg="green")
    for line in criticals:
        click.secho(f"  {line}", fg="red", bold=True)
    for line in warnings:
        click.secho(f"  {line}", fg="yellow")


_SEVERITY_STYLE = {
    "critical": {"fg": "red", "bold": True},
    "warning": {"fg": "yellow"},
    "ok": {"fg": "green"},
}


def _print_validation(validation: VaultValidation) -> None:
    style = _SEVERITY_STYLE[validation.verdict]
    click.secho(f"Verdict: {validation.verdict.upper()}", **style)
    for severity, title, messages in (
        ("critical", "Criticals", validation.criticals),
        ("warning", "Warnings", validation.warnings),
        ("ok", "Passed", validation.ok),
    ):
        if not messages:
            continue
        click.echo()
        click.echo(f"{title} ({len(messages)}):")
        for message in messages:
            for line in message.split("\n"):
                click.secho(f"  {line}", **_SEVERITY_STYLE[severity])

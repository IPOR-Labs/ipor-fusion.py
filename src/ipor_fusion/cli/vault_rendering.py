from __future__ import annotations

from datetime import datetime, timezone

import click


def _format_amount(raw: int, decimals: int) -> str:
    if decimals == 0:
        return f"{raw:,}"
    integer_part = raw // (10**decimals)
    fractional_part = raw % (10**decimals)
    frac_str = str(fractional_part).zfill(decimals)[:6].rstrip("0") or "0"
    return f"{integer_part:,}.{frac_str}"


def _format_remaining(seconds: int) -> str:
    if seconds <= 0:
        return "expired"
    hours = seconds // 3600
    minutes = (seconds % 3600) // 60
    if hours >= 24:
        days = hours // 24
        hours = hours % 24
        return f"{days}d {hours}h left"
    if hours > 0:
        return f"{hours}h {minutes}m left"
    return f"{minutes}m left"


def _format_usd(raw: int, decimals: int, price_usd: float | None) -> str:
    if price_usd is None:
        return ""
    value = (raw / 10**decimals) * price_usd
    return f" (${value:,.2f})"


def _format_age(timestamp: int) -> str:
    """Format deployment age as human-readable string."""
    delta = datetime.now(tz=timezone.utc) - datetime.fromtimestamp(
        timestamp, tz=timezone.utc
    )
    days = delta.days
    if days == 0:
        return "today"
    if days == 1:
        return "1 day ago"
    return f"{days} days ago"


def _print_table(headers: tuple[str, ...], rows: list[tuple[str, ...]]) -> None:
    if not rows:
        click.echo("  (none)")
        return

    widths = [len(hdr) for hdr in headers]
    for row in rows:
        for col_idx, val in enumerate(row):
            widths[col_idx] = max(widths[col_idx], len(val))

    fmt = "  ".join(f"{{:<{wid}}}" for wid in widths)
    click.echo(f"  {fmt.format(*headers)}")
    click.echo(f"  {fmt.format(*('-' * wid for wid in widths))}")
    for row in rows:
        click.echo(f"  {fmt.format(*row)}")


def _substrate_details(
    symbol: str,
    contract: str,
    type_label: str,
    extra: dict[str, str] | None = None,
) -> str:
    parts: list[str] = []
    if symbol:
        parts.append(f"symbol={symbol}")
    if contract:
        parts.append(f"contract={contract}")
    if type_label:
        parts.append(f"substrate-type={type_label}")
    if extra:
        for key, val in extra.items():
            parts.append(f"{key}={val}")
    return f" ({', '.join(parts)})" if parts else ""

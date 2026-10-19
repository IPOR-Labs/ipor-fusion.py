from __future__ import annotations

import click

from ipor_fusion.cli.vault_rendering import (
    _format_amount,
)
from ipor_fusion.readers.oracle_mapping import (
    TYPE_CHAINLINK,
    TYPE_CHAINLINK_STYLE,
    TYPE_DUAL_XREF,
    WAD_DECIMALS,
    OracleMapping,
    OracleNode,
    OraclePrice,
)


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

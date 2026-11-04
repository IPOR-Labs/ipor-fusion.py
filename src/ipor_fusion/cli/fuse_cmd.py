"""`fusion fuse whitelist` — the fuses the chain's Fuse Whitelist lists for a market."""

from __future__ import annotations

import json as json_lib
from datetime import UTC, datetime

import click

from ipor_fusion.cli.config_store import load_config
from ipor_fusion.cli.options import BLOCK_NUMBER, CHAIN, _resolve_provider
from ipor_fusion.cli.vault_rendering import _print_table
from ipor_fusion.core.context import Web3Context
from ipor_fusion.errors import (
    AddressNotInRegistryError,
    AddressRegistryError,
    UnsupportedChainError,
)
from ipor_fusion.fuse_whitelist import fetch_fuse_whitelist_json
from ipor_fusion.types import ChainId, MarketId


@click.group()
def fuse() -> None:
    """Inspect fuses: which ones IPOR whitelists for a market."""


@fuse.command("whitelist")
@click.option(
    "--chain",
    "chain_id",
    type=CHAIN,
    required=True,
    help="Chain ID or name (ethereum, base, arbitrum, ...).",
)
@click.option(
    "--market-id",
    "market_id",
    type=click.IntRange(min=0),
    required=True,
    help="Numeric market ID (IporFusionMarkets), e.g. 11 for EULER_V2.",
)
@click.option(
    "--block-number",
    "--block",
    "block_number",
    type=BLOCK_NUMBER,
    default=None,
    help="Block number for on-chain reads (0 or omitted: latest).",
)
@click.option(
    "--include-deprecated",
    is_flag=True,
    default=False,
    help="Also list deprecated and removed fuses.",
)
@click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of a table.",
)
def whitelist(
    chain_id: ChainId,
    market_id: int,
    block_number: int | None,
    include_deprecated: bool,
    json_output: bool,
) -> None:
    """List the whitelisted fuses of a market: use one of these instead of
    deploying a copy, which the IPOR app would show as unknown."""
    ctx = Web3Context.from_url(_resolve_provider(load_config(), chain_id))
    if block_number is not None:
        ctx = ctx.at_block(block_number)
    try:
        document = fetch_fuse_whitelist_json(
            ctx, MarketId(market_id), include_deprecated=include_deprecated
        )
    except (
        AddressNotInRegistryError,
        AddressRegistryError,
        UnsupportedChainError,
    ) as exc:
        raise click.UsageError(str(exc)) from exc
    if json_output:
        click.echo(json_lib.dumps(document, indent=2))
        return
    click.secho(
        f"Fuse Whitelist {document['whitelist']} on {document['chain']},"
        f" market {document['market']}",
        bold=True,
    )
    _print_table(
        ("Fuse", "State", "Type", "Role", "Added"),
        [
            (
                f["fuse"],
                f["state"],
                f["type_name"],
                f["role"] or "?",
                datetime.fromtimestamp(f["added_at"], UTC).date().isoformat(),
            )
            for f in document["fuses"]
        ],
    )

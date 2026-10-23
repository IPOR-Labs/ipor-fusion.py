"""`fusion address` — IPOR Fusion deployment addresses from the ipor-abi registry."""

from __future__ import annotations

import json as json_lib

import click

from ipor_fusion.cli.options import CHAIN
from ipor_fusion.cli.vault_rendering import _print_table
from ipor_fusion.errors import AddressRegistryError, UnsupportedChainError
from ipor_fusion.registry import (
    AddressRegistry,
    build_address_list_json,
    build_address_lookup_json,
    build_address_names_json,
    fetch_address_registries,
    fetch_address_registry,
)
from ipor_fusion.types import ChainId

_JSON = click.option(
    "--json",
    "json_output",
    is_flag=True,
    default=False,
    help="Emit machine-readable JSON instead of a table.",
)


def _registries(chain_id: ChainId | None) -> list[AddressRegistry]:
    try:
        return fetch_address_registries(None if chain_id is None else [chain_id])
    except (AddressRegistryError, UnsupportedChainError) as exc:
        raise click.UsageError(str(exc)) from exc


@click.group()
def address() -> None:
    """Look up IPOR Fusion contract addresses (ipor-abi registry)."""


@address.command("lookup")
@click.argument("query")
@click.option(
    "--chain",
    "chain_id",
    type=CHAIN,
    default=None,
    help="Search one chain (ID or name); default: every chain with a registry.",
)
@_JSON
def lookup(query: str, chain_id: ChainId | None, json_output: bool) -> None:
    """Find contracts by name substring, or the names of an address."""
    document = build_address_lookup_json(query, _registries(chain_id))
    if json_output:
        click.echo(json_lib.dumps(document, indent=2))
        return
    _print_table(
        ("Chain", "Name", "Address"),
        [(m["chain"], m["name"], m["address"]) for m in document["matches"]],
    )


@address.command("names")
@click.option(
    "--chain",
    "chain_id",
    type=CHAIN,
    default=None,
    help="List one chain (ID or name); default: every chain with a registry.",
)
@_JSON
def names(chain_id: ChainId | None, json_output: bool) -> None:
    """List the contract names in the registry, with the chains that have them."""
    document = build_address_names_json(_registries(chain_id))
    if json_output:
        click.echo(json_lib.dumps(document, indent=2))
        return
    _print_table(
        ("Name", "Chains"),
        [
            (entry["name"], ", ".join(str(c) for c in entry["chain_ids"]))
            for entry in document["names"]
        ],
    )


@address.command("list")
@click.option(
    "--chain",
    "chain_id",
    type=CHAIN,
    required=True,
    help="Chain ID or name (ethereum, base, arbitrum, ...).",
)
@_JSON
def list_addresses(chain_id: ChainId, json_output: bool) -> None:
    """Every registered contract of one chain."""
    try:
        registry = fetch_address_registry(chain_id)
    except (AddressRegistryError, UnsupportedChainError) as exc:
        raise click.UsageError(str(exc)) from exc
    document = build_address_list_json(registry)
    if json_output:
        click.echo(json_lib.dumps(document, indent=2))
        return
    _print_table(("Name", "Address"), list(document["addresses"].items()))

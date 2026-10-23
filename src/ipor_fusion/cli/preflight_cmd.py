"""`fusion vault preflight`: check an execute batch before sending it."""

from __future__ import annotations

import json
import sys
from pathlib import Path

import click
from eth_typing import ChecksumAddress

from ipor_fusion.cli.config_store import load_config
from ipor_fusion.cli.options import (
    ADDRESS,
    BLOCK_NUMBER,
    CHAIN,
    VAULT_ADDRESS,
    _build_ctx,
)
from ipor_fusion.cli.vault_rendering import _print_table
from ipor_fusion.preflight import (
    ExecutePreflight,
    build_execute_preflight_json,
    parse_execute_actions,
    preflight_execute,
)
from ipor_fusion.types import ChainId, PlasmaVaultAddress


def _read_calldata(calldata: str) -> str:
    if calldata.startswith("@"):
        try:
            return Path(calldata[1:]).read_text()
        except OSError as exc:
            raise click.BadParameter(str(exc), param_hint="--calldata") from exc
    return calldata


@click.command("preflight")
@click.argument("vault_address", type=VAULT_ADDRESS)
@click.option(
    "--calldata",
    required=True,
    help="execute((address,bytes)[]) calldata as hex, or @file holding it.",
)
@click.option(
    "--sender",
    type=ADDRESS,
    required=True,
    help="The account that will send execute (an ALPHA).",
)
@click.option(
    "--chain-id", type=CHAIN, default=None, help="Chain ID or name (e.g. 1, ethereum)."
)
@click.option(
    "--block-number",
    type=BLOCK_NUMBER,
    default=None,
    help="Block (0 or omitted: latest).",
)
@click.option(
    "--simulate/--no-simulate",
    default=True,
    help="Run the batch through eth_simulateV1 (default) or only the static checks.",
)
@click.option(
    "--json", "json_output", is_flag=True, default=False, help="Output as JSON."
)
def preflight(
    vault_address: PlasmaVaultAddress,
    calldata: str,
    sender: ChecksumAddress,
    chain_id: ChainId | None,
    block_number: int | None,
    simulate: bool,
    json_output: bool,
) -> None:
    """Check an execute batch as SENDER would send it: role, fuse registration,
    balance fuses, and a simulation of the exact calldata. Read-only. Exits with
    status 1 when a check is critical."""
    try:
        actions = parse_execute_actions(_read_calldata(calldata))
    except ValueError as exc:
        raise click.BadParameter(str(exc), param_hint="--calldata") from exc
    if not actions:
        raise click.BadParameter("the batch has no actions", param_hint="--calldata")
    _, ctx = _build_ctx(load_config(), vault_address, chain_id, block_number)
    result = preflight_execute(
        ctx, vault_address, actions, sender=sender, simulate=simulate
    )
    if json_output:
        click.echo(json.dumps(build_execute_preflight_json(result), indent=2))
    else:
        _print_preflight(result)
    if result.verdict == "critical":
        sys.exit(1)


def _print_preflight(result: ExecutePreflight) -> None:
    click.echo(f"Vault:   {result.vault}")
    click.echo(f"Block:   {result.block_number}")
    click.echo(f"Sender:  {result.sender}")
    click.echo(f"Verdict: {result.verdict}")
    click.echo("\nActions")
    _print_table(
        ("#", "Fuse", "Registered", "Market", "Encoder"),
        [
            (
                str(a.index),
                a.fuse,
                "yes" if a.registered else "NO",
                "?" if a.market_id is None else str(a.market_id),
                ", ".join(a.candidates) or "unknown",
            )
            for a in result.actions
        ],
    )
    click.echo("\nFindings")
    _print_table(
        ("Severity", "Check", "Message"),
        [(f.severity, f.check, f.message) for f in result.findings],
    )
    if result.simulation is not None:
        sim = result.simulation
        click.echo("\nSimulation")
        click.echo(f"  success: {sim.success}  gas: {sim.gas_used}")
        click.echo(
            f"  totalAssets: {sim.total_assets_before} -> {sim.total_assets_after}"
        )
        if sim.revert_reason:
            click.echo(f"  revert: {sim.revert_reason}")

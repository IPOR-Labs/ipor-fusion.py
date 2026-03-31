from __future__ import annotations

import click

from ipor_fusion.cli.config_store import (
    FusionConfig,
    load_config,
    save_config,
)
from ipor_fusion.cli.vault_cmd import ADDRESS


@click.group()
def config() -> None:
    """Manage CLI configuration."""


@config.command("set-provider")
@click.argument("url")
@click.option(
    "--chain-id",
    type=int,
    default=None,
    help="Chain ID (auto-detected from URL if omitted).",
)
def set_provider(url: str, chain_id: int | None) -> None:
    """Set RPC provider URL for a chain (chain ID auto-detected via eth_chainId)."""
    from web3 import Web3  # pylint: disable=import-outside-toplevel

    if chain_id is None:
        web3 = Web3(Web3.HTTPProvider(url))
        chain_id = web3.eth.chain_id
        click.echo(f"Detected chain ID: {chain_id}")

    cfg = load_config()
    cfg.providers[str(chain_id)] = url
    save_config(cfg)
    click.echo(f"Provider for chain {chain_id} set.")


@config.command("set-etherscan-key")
@click.argument("api_key")
def set_etherscan_key(api_key: str) -> None:
    """Set Etherscan API key (works for all chains via Etherscan V2)."""
    cfg = load_config()
    cfg.etherscan_api_key = api_key
    save_config(cfg)
    click.echo("Etherscan API key set.")


@config.command("set-default-vault")
@click.argument("address", type=ADDRESS)
def set_default_vault(address: str) -> None:
    """Set the default vault address."""
    cfg = load_config()
    cfg.default_vault = address
    save_config(cfg)
    click.echo(f"Default vault set to {address}")


@config.command("show")
def show() -> None:
    """Display current configuration."""
    cfg = load_config()
    _print_config(cfg)


def _print_config(cfg: FusionConfig) -> None:
    click.echo("Providers:")
    if cfg.providers:
        for chain_id, url in cfg.providers.items():
            click.echo(f"  Chain {chain_id}: {url}")
    else:
        click.echo("  (none)")

    click.echo(f"\nDefault vault: {cfg.default_vault or '(not set)'}")

    click.echo("\nSaved vaults:")
    if cfg.vaults:
        for vault in cfg.vaults:
            click.echo(f"  [{vault.chain_id}] {vault.label}: {vault.address}")
    else:
        click.echo("  (none)")

    click.echo(
        f"\nEtherscan API key: {'***' if cfg.etherscan_api_key else '(not set)'}"
    )

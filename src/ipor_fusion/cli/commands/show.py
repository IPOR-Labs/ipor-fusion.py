#!/usr/bin/env python3
"""
Show command for ipor-fusion CLI
"""

import click
from .base import BaseCommand


@click.command()
@click.option(
    "--config-file",
    help="Path to the configuration file (default: ipor-fusion-config.yaml)",
)
def show(config_file):
    """
    Display the current configuration settings.
    """
    try:
        config = BaseCommand.load_config(config_file)

        click.secho("Current Configuration:", fg="green", bold=True)
        click.echo(f"  Plasma Vault Address: {config.plasma_vault_address}")
        click.echo(f"  Provider URL: {config.provider_url}")
        
        # Handle private key display
        if config.is_private_key_encrypted():
            click.echo(f"  Private Key: [ENCRYPTED] {'*' * 10}")
            click.secho("  ⚠️  Private key is encrypted", fg="yellow")
        else:
            click.echo(f"  Private Key: {'*' * 10}{config.private_key[-4:]}")
            click.secho("  ⚠️  Private key is not encrypted", fg="yellow")
        
        click.echo(f"  Network: {config.network}")
        click.echo(f"  Gas Limit: {config.gas_limit}")

        if config.gas_price:
            click.echo(f"  Gas Price: {config.gas_price}")
        if config.max_priority_fee:
            click.echo(f"  Max Priority Fee: {config.max_priority_fee}")

    except Exception as e:
        click.secho(f"Error loading configuration: {e}", fg="red")
        raise

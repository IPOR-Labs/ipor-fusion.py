#!/usr/bin/env python3
"""
Init command for ipor-fusion CLI
"""

import click
from .base import BaseCommand


@click.command()
@click.option(
    "--plasma-vault-address",
    prompt="Set plasma vault address",
    help="Set plasma vault address",
)
@click.option(
    "--rpc-url",
    prompt="RPC provider url like alchemy (https://...)",
    help="HTTP(S) endpoint for the chosen network",
)
@click.option(
    "--private-key",
    prompt="Private key",
    hide_input=True,
    confirmation_prompt=True,
    help="Your Ethereum account private key",
)
@click.option(
    "--network",
    default="mainnet",
    help="Network name (default: mainnet)",
)
@click.option(
    "--gas-limit",
    default=300000,
    type=int,
    help="Gas limit for transactions (default: 300000)",
)
@click.option(
    "--gas-price",
    type=int,
    help="Gas price for transactions (optional)",
)
@click.option(
    "--max-priority-fee",
    type=int,
    help="Max priority fee for transactions (optional)",
)
@click.option(
    "--config-file",
    help="Path to the configuration file (default: ipor-fusion-config.yaml)",
)
@click.option(
    "--encrypt-private-key",
    is_flag=True,
    help="Encrypt the private key with a password",
)
def init(
    plasma_vault_address,
    rpc_url,
    private_key,
    network,
    gas_limit,
    gas_price,
    max_priority_fee,
    config_file,
    encrypt_private_key,
):
    """
    Initialize a YAML configuration file with network, provider URL, and private key.
    """
    BaseCommand.create_config_file(
        plasma_vault_address=plasma_vault_address,
        rpc_url=rpc_url,
        private_key=private_key,
        network=network,
        gas_limit=gas_limit,
        gas_price=gas_price,
        max_priority_fee=max_priority_fee,
        config_file=config_file,
        encrypt_private_key=encrypt_private_key,
    )

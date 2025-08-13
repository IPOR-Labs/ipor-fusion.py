#!/usr/bin/env python3
"""
Init command for ipor-fusion CLI
"""
import re

import click
from web3 import Web3

from .base import BaseCommand
from ...PlasmaVaultSystemFactory import PlasmaVaultSystemFactory


@click.command()
@click.option(
    "--rpc-url",
    prompt="RPC provider url like alchemy (https://...)",
    help="HTTP(S) endpoint for the chosen network",
)
@click.option(
    "--plasma-vault-address",
    prompt="Set plasma vault address",
    help="Set plasma vault address",
)
@click.option(
    "--config-file",
    prompt="Set config file",
    help="Set config file path. Default: ipor-fusion-config.yaml.",
)
def init(
        rpc_url,
        plasma_vault_address,
        config_file,
):
    """
    Initialize a YAML configuration file with network, provider URL, and private key.
    """

    system = PlasmaVaultSystemFactory(
        provider_url=rpc_url,
    ).get(Web3.to_checksum_address(plasma_vault_address))

    name = system.plasma_vault().name()
    name = re.sub(r'\s+', '-', name)

    use_default_name = click.confirm(f'Do you want to use \"{name}\" as the vault name in vaults list?')
    if not use_default_name:
        name = click.prompt('Enter your vault name:').replace(' ', '-')
        name = re.sub(r'\s+', '-', name)

    private_key = None
    encrypt_private_key = False
    if click.confirm('Do you want to set private key? '):
        while True:
            private_key = click.prompt('Enter private key:', hide_input=True)
            repeated_private_key = click.prompt('Repeat private key:', hide_input=True)

            if private_key == repeated_private_key:
                break

            click.secho("Private keys do not match!", fg="red")

        encrypt_private_key = click.confirm('Do you want to encrypt private key?')

    BaseCommand.add_new_plasma_vault(
        plasma_vault_address=plasma_vault_address,
        rpc_url=rpc_url,
        name=name,
        private_key=private_key,
        encrypt_private_key=encrypt_private_key,
        config_file=config_file
    )

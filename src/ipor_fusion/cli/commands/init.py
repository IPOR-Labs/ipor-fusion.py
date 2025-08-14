#!/usr/bin/env python3
"""
Init command for ipor-fusion CLI
"""
import re

import click
from web3 import Web3, HTTPProvider

from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.cli.commands.base import BaseCommand


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

    if not is_valid_http_url(rpc_url):
        click.secho("Invalid RPC URL!", fg="red", err=True)

    chain_id = get_chain_id(rpc_url)

    plasma_vault_address_checksum = Web3.to_checksum_address(plasma_vault_address)
    system = PlasmaVaultSystemFactory(
        provider_url=rpc_url,
    ).get(plasma_vault_address_checksum)

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

    BaseCommand.init_config(
        chain_id=chain_id,
        plasma_vault_address=plasma_vault_address_checksum,
        rpc_url=rpc_url,
        name=name,
        private_key=private_key,
        encrypt_private_key=encrypt_private_key,
        config_file=config_file
    )


def get_chain_id(rpc_url: str) -> int:
    try:
        web3 = Web3(HTTPProvider(rpc_url))
        return web3.eth.chain_id
    except Exception as e:
        click.secho(f"Error connecting to RPC provider: {e}", fg="red")
        raise

def is_valid_http_url(url):
    return (
        isinstance(url, str) and
        url.startswith(('http://', 'https://')) and
        len(url) > 8 and  # Minimum valid URL length
        '.' in url  # Must contain domain
    )
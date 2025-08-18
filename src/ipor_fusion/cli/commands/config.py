import re
import sys

import click
import yaml
from web3 import HTTPProvider, Web3

from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.cli.commands.base import BaseCommand
from ipor_fusion.cli.config import ConfigManager, PlasmaVaultConfig, FuseConfig


@click.group(help="Manage CLI configuration")
def config():
    pass


@config.command(help="Validate the configuration file")
@click.option(
    "--config-file",
    help="Set config file path. Default: ipor-fusion-config.yaml.",
)
def validate(config_file):
    BaseCommand.load_config(config_file=config_file)
    config_path = ConfigManager.get_config_path(config_file)
    click.secho(f"Configuration file is valid: {config_path}", fg="green")


@config.command(help="Display the current configuration settings")
@click.option(
    "--config-file",
    help="Set config file path. Default: ipor-fusion-config.yaml.",
)
def show(config_file):
    config = BaseCommand.load_config(config_file=config_file)
    config_path = ConfigManager.get_config_path(config_file)
    click.echo(f"Configuration file path: {config_path}")
    click.echo(yaml.dump(config.to_dict(), default_flow_style=True, indent=2))


def get_rpc_url_by_plasma_vault_name(config, name):
    for chain in config.chain_configs:
        for vault in chain.plasma_vaults:
            if vault.name == name:
                return chain.rpc_url
    return None


def get_fuse_name(fuse_address):
    return None


@config.command(help="Update the current vault configuration from blockchain")
@click.option(
    "--config-file",
    help="Set config file path. Default: ipor-fusion-config.yaml.",
)
@click.option(
    "--name",
    help="Plasma vault name",
    default=None,
)
def update(config_file, name):
    config = BaseCommand.load_config(config_file=config_file)

    if not name:
        name = config.default_plasma_vault_name

    rpc_url = get_rpc_url_by_plasma_vault_name(config, name)
    plasma_vault = get_plasma_vault_by_name(config, name)

    system = PlasmaVaultSystemFactory(
        provider_url=rpc_url,
    ).get(plasma_vault.plasma_vault_address)

    click.echo("Getting fuses...         ", nl=False)
    fuse_addresses = system.plasma_vault().get_fuses()
    for chain in config.chain_configs:
        for vault in chain.plasma_vaults:
            if vault.name == name:
                vault.fuses = [FuseConfig(fuse_address=fuse_address, fuse_name=get_fuse_name(fuse_address)) for fuse_address in fuse_addresses]
                ConfigManager.update_config(config_file=config_file, config=config)
                click.secho(
                    f"OK", fg="green"
                )
                break

    click.echo("Getting reward fuses...  ", nl=False)
    fuse_addresses = system.plasma_vault().get_fuses()
    for chain in config.chain_configs:
        for vault in chain.plasma_vaults:
            if vault.name == name:
                vault.fuses = [FuseConfig(fuse_address=fuse_address, fuse_name=get_fuse_name(fuse_address)) for fuse_address in fuse_addresses]
                ConfigManager.update_config(config_file=config_file, config=config)
                click.secho(
                    f"OK", fg="green"
                )
                break


def get_plasma_vault_by_name(config, name) -> PlasmaVaultConfig:
    for chain in config.chain_configs:
        for vault in chain.plasma_vaults:
            if vault.name == name:
                return vault
    return None


@config.command(help="Initialize a YAML configuration file with provider URL, plasma vault address, and private key.")
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
        sys.exit(1)

    plasma_vault_address_checksum = get_checksum_address(plasma_vault_address)
    chain_id = get_chain_id(rpc_url)
    name = get_vault_name(plasma_vault_address_checksum, rpc_url)

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


def get_checksum_address(plasma_vault_address):
    try:
        return Web3.to_checksum_address(plasma_vault_address)
    except ValueError:
        click.secho("Invalid plasma vault address!", fg="red", err=True)
        sys.exit(1)


def get_vault_name(plasma_vault_address_checksum, rpc_url):
    try:
        system = PlasmaVaultSystemFactory(
            provider_url=rpc_url,
        ).get(plasma_vault_address_checksum)
        name = system.plasma_vault().name()
        return re.sub(r'\s+', '-', name)
    except Exception as e:
        click.secho(f"Error getting vault name: {e}", fg="red", err=True)
        sys.exit(1)


def get_chain_id(rpc_url: str) -> int:
    try:
        web3 = Web3(HTTPProvider(rpc_url))
        return web3.eth.chain_id
    except Exception as e:
        click.secho(f"Error connecting to RPC provider: {e}", fg="red", err=True)
        sys.exit(1)


def is_valid_http_url(url):
    return (
            isinstance(url, str) and url.startswith(('http://', 'https://'))
    )

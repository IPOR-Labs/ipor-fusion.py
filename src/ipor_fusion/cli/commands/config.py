import click

from ipor_fusion.cli.commands.base import BaseCommand
from ipor_fusion.cli.config import ConfigManager


@click.group()
def config():
    pass

@click.option(
    "--config-file",
    help="Set config file path. Default: ipor-fusion-config.yaml.",
)
@config.command()
def validate(config_file):
    BaseCommand.load_config(config_file=config_file)
    config_path = ConfigManager.get_config_path(config_file)
    click.secho(f"Configuration file is valid: {config_path}", fg="green")
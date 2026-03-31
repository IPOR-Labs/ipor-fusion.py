from __future__ import annotations

import sys

import click

from ipor_fusion.cli.config_cmd import config
from ipor_fusion.cli.vault_cmd import vault


@click.group()
@click.version_option(package_name="ipor_fusion")
def cli() -> None:
    """IPOR Fusion CLI — inspect and manage Plasma Vaults."""


cli.add_command(config)
cli.add_command(vault)


def main() -> None:
    """Entry point with user-friendly exception handling."""
    try:
        cli()
    except KeyboardInterrupt:
        sys.exit(130)
    except click.UsageError:
        raise
    except ConnectionError as exc:
        click.secho(f"Connection failed: {exc}. Check your provider URL.", fg="red")
        sys.exit(1)
    except ValueError as exc:
        msg = str(exc)
        if "address" in msg.lower():
            click.secho(f"Invalid address: {msg}", fg="red")
        else:
            click.secho(f"Error: {msg}", fg="red")
        sys.exit(1)
    except Exception as exc:  # pylint: disable=broad-except
        click.secho(f"Error: {exc}", fg="red")
        sys.exit(1)

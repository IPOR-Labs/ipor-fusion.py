from __future__ import annotations

import os
import sys

import click

from ipor_fusion.cli.config_cmd import config
from ipor_fusion.cli.vault_cmd import vault


@click.group()
@click.version_option(package_name="ipor_fusion")
@click.option("--verbose", "-v", is_flag=True, help="Print RPC call details.")
@click.option("--quiet", "-q", is_flag=True, help="Suppress non-essential output.")
@click.option("--no-color", is_flag=True, help="Disable colored output.")
@click.pass_context
def cli(ctx: click.Context, verbose: bool, quiet: bool, no_color: bool) -> None:
    """IPOR Fusion CLI — inspect and manage Plasma Vaults."""
    ctx.ensure_object(dict)
    ctx.obj["verbose"] = verbose
    ctx.obj["quiet"] = quiet
    if no_color or os.environ.get("NO_COLOR"):
        ctx.color = False
        ctx.obj["no_color"] = True
    else:
        ctx.obj["no_color"] = False


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

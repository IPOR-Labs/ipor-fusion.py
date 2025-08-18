#!/usr/bin/env python3
"""
Simple CLI for ipor-fusion init
"""

import click

from ipor_fusion.cli.commands.config import config


@click.group()
def cli():
    """ipor-fusion CLI"""

# cli.add_command(init)
cli.add_command(config)

if __name__ == "__main__":
    cli()

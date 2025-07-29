#!/usr/bin/env python3
"""
Simple CLI for ipor-fusion init
"""

import click
from ipor_fusion.cli.commands.registry import register_commands


@click.group()
def cli():
    """ipor-fusion CLI"""


# Automatically register all commands
register_commands(cli)


if __name__ == "__main__":
    cli()

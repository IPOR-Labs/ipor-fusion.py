#!/usr/bin/env python3
"""
Command registry for automatically loading CLI commands
"""

from typing import List
import importlib
import pkgutil
from pathlib import Path

import click


def discover_commands() -> List[click.Command]:
    """
    Automatically discover and load all command modules in the commands directory.

    Returns:
        List of click.Command objects
    """
    commands = []
    commands_dir = Path(__file__).parent

    # Import all modules in the commands directory
    for _, module_name, is_pkg in pkgutil.iter_modules([str(commands_dir)]):
        if not is_pkg and module_name not in ["__init__", "registry", "base"]:
            try:
                module = importlib.import_module(
                    f".{module_name}", package="ipor_fusion.cli.commands"
                )

                # Look for click.Command objects in the module
                for attr_name in dir(module):
                    attr = getattr(module, attr_name)
                    if isinstance(attr, click.Command):
                        commands.append(attr)

            except ImportError as e:
                print(f"Warning: Could not import command module {module_name}: {e}")

    return commands


def register_commands(cli_group: click.Group) -> None:
    """
    Register all discovered commands with the main CLI group.

    Args:
        cli_group: The main CLI group to register commands with
    """
    commands = discover_commands()
    for command in commands:
        cli_group.add_command(command)

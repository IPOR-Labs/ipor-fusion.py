#!/usr/bin/env python3
"""
Encrypt command for ipor-fusion CLI
"""

import click
from .base import BaseCommand


@click.command()
@click.option(
    "--config-file",
    help="Path to the configuration file (default: ipor-fusion-config.yaml)",
)
@click.option(
    "--password",
    help="Password for encryption (will prompt if not provided)",
)
def encrypt(config_file, password):
    """
    Encrypt the private key in an existing configuration file.
    """
    try:
        config_path = BaseCommand.load_config(config_file)
        
        # Check if private key is already encrypted
        if config_path.is_private_key_encrypted():
            click.secho("Private key is already encrypted.", fg="yellow")
            return
        
        # Prompt for password if not provided
        if not password:
            password = click.prompt(
                "Enter encryption password",
                hide_input=True,
                confirmation_prompt=True
            )
        
        # Import here to avoid circular imports
        from ipor_fusion.cli.config import ConfigManager
        
        # Encrypt the private key
        updated_path = ConfigManager.encrypt_existing_private_key(
            config_file=config_file,
            password=password
        )
        
        click.secho(f"Private key encrypted successfully in {updated_path}", fg="green")
        click.secho("Remember to keep your password safe!", fg="yellow")
        
    except Exception as e:
        click.secho(f"Error encrypting private key: {e}", fg="red")
        raise 
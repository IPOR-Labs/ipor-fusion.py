#!/usr/bin/env python3
"""
Base command class for ipor-fusion CLI commands
"""

from pathlib import Path
from typing import Optional

import click
from eth_typing import ChecksumAddress
from eth_utils import network_from_chain_id

from ipor_fusion.cli.config import ConfigManager, GeneralConfig, ChainConfig, PlasmaVaultConfig
from ipor_fusion.cli.encryption import EncryptionManager


class BaseCommand:
    """Base class for CLI commands with common functionality"""

    @staticmethod
    def init_config(
            chain_id: int,
            plasma_vault_address: ChecksumAddress,
            rpc_url: str,
            private_key: str,
            name: str,
            config_file: Optional[str] = None,
            encrypt_private_key: bool = False,
    ) -> Path:
        config_path = ConfigManager.get_config_path(config_file)

        if config_path.exists():
            click.confirm(
                f"Configuration file already exists at {config_path}. Overwrite?",
                abort=True,
            )

        try:
            # Handle encryption if requested
            encryption_password = None
            if encrypt_private_key:
                encryption_password = click.prompt(
                    "Enter encryption password for private key",
                    hide_input=True,
                    confirmation_prompt=True,
                )

            final_private_key = private_key
            if encrypt_private_key:
                if not encryption_password:
                    click.secho("Encryption password is required when encrypt_private_key is True", fg="red", err=True)

                final_private_key = EncryptionManager.encrypt_private_key(
                    private_key, encryption_password
                )

            plasma_vault_config = PlasmaVaultConfig(plasma_vault_address=plasma_vault_address, name=name,
                                                    private_key=final_private_key)
            chain_network = network_from_chain_id(chain_id)
            chain_config = ChainConfig(chain_id=chain_id, chain_name=chain_network.name,
                                       chain_short_name=chain_network.shortName,
                                       rpc_url=rpc_url,
                                       plasma_vaults=[plasma_vault_config])

            general_config = GeneralConfig(default_plasma_vault_name=plasma_vault_config.name, chain_configs=[chain_config])
            config_path = ConfigManager.create_config_file(
                general_config=general_config,
                config_file=config_file,
            )

            click.secho(f"Configuration file created at {config_path}", fg="green")
            if encrypt_private_key:
                click.secho(
                    "Private key has been encrypted with your password.", fg="green"
                )
            click.secho("You can now run other ipor-fusion commands.", fg="green")

            return config_path

        except Exception as e:
            click.secho(f"Error creating configuration file: {e}", fg="red")
            raise

    @staticmethod
    def load_config(
            config_file: Optional[str] = None
    ) -> GeneralConfig:
        config = None
        try:
            config = ConfigManager.load_config(config_file)
        except Exception as e:
            click.secho(f"Error loading configuration: {e}", fg="red")
            raise

        try:
            ConfigManager.validate_config(config)
        except Exception as e:
            click.secho(f"Error validate configuration: {e}", fg="red")
            raise

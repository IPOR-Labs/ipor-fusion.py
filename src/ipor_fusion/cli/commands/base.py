#!/usr/bin/env python3
"""
Base command class for ipor-fusion CLI commands
"""

from pathlib import Path
from typing import Optional
import click
from ipor_fusion.cli.config import ConfigManager, FusionConfig


class BaseCommand:
    """Base class for CLI commands with common functionality"""

    @staticmethod
    def create_config_file(
        plasma_vault_address: str,
        rpc_url: str,
        private_key: str,
        network: str = "mainnet",
        gas_limit: int = 300000,
        gas_price: Optional[int] = None,
        max_priority_fee: Optional[int] = None,
        config_file: Optional[str] = None,
        encrypt_private_key: bool = False,
    ) -> Path:
        """
        Create a YAML configuration file with the provided configuration.

        Args:
            plasma_vault_address: The plasma vault address
            rpc_url: The RPC provider URL
            private_key: The private key
            network: The network name (default: mainnet)
            gas_limit: Gas limit for transactions (default: 300000)
            gas_price: Gas price for transactions (optional)
            max_priority_fee: Max priority fee for transactions (optional)
            config_file: Optional path for the config file (defaults to ipor-fusion-config.yaml)
            encrypt_private_key: Whether to encrypt the private key (default: False)

        Returns:
            Path to the created configuration file
        """
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

            config_path = ConfigManager.create_config(
                plasma_vault_address=plasma_vault_address,
                rpc_url=rpc_url,
                private_key=private_key,
                network=network,
                gas_limit=gas_limit,
                gas_price=gas_price,
                max_priority_fee=max_priority_fee,
                config_file=config_file,
                encrypt_private_key=encrypt_private_key,
                encryption_password=encryption_password,
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
        config_file: Optional[str] = None, password: Optional[str] = None
    ) -> FusionConfig:
        """
        Load configuration from YAML file.

        Args:
            config_file: Optional path to the configuration file
            password: Password for decrypting private key (if encrypted)

        Returns:
            FusionConfig object
        """
        try:
            config = ConfigManager.load_config(config_file)

            # Handle encrypted private key
            if config.is_private_key_encrypted():
                if not password:
                    password = click.prompt(
                        "Enter password to decrypt private key", hide_input=True
                    )

                try:
                    # Test decryption
                    config.get_decrypted_private_key(password)
                    click.secho("Private key decrypted successfully.", fg="green")
                except ValueError as e:
                    click.secho(f"Failed to decrypt private key: {e}", fg="red")
                    raise

            ConfigManager.validate_config(config)
            return config
        except Exception as e:
            click.secho(f"Error loading configuration: {e}", fg="red")
            raise

    @staticmethod
    def get_common_options():
        """Get common click options for plasma vault, provider URL, and private key"""
        return [
            click.option(
                "--plasma-vault-address",
                prompt="Set plasma vault address",
                help="Set plasma vault address",
            ),
            click.option(
                "--rpc-url",
                prompt="RPC provider url like alchemy (https://...)",
                help="HTTP(S) endpoint for the chosen network",
            ),
            click.option(
                "--private-key",
                prompt="Private key",
                hide_input=True,
                confirmation_prompt=True,
                help="Your Ethereum account private key",
            ),
        ]

    @staticmethod
    def get_advanced_options():
        """Get advanced click options for network and gas settings"""
        return [
            click.option(
                "--network",
                default="mainnet",
                help="Network name (default: mainnet)",
            ),
            click.option(
                "--gas-limit",
                default=300000,
                type=int,
                help="Gas limit for transactions (default: 300000)",
            ),
            click.option(
                "--gas-price",
                type=int,
                help="Gas price for transactions (optional)",
            ),
            click.option(
                "--max-priority-fee",
                type=int,
                help="Max priority fee for transactions (optional)",
            ),
        ]

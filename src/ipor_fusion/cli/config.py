#!/usr/bin/env python3
"""
YAML configuration manager for IPOR Fusion CLI
"""

import os
from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Dict, Any, Optional

import click
import yaml
from dotenv import load_dotenv
from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.cli.encryption import EncryptionManager


@dataclass
class FusionConfig:
    """Configuration data class for IPOR Fusion"""

    plasma_vault_address: ChecksumAddress
    rpc_url: str
    private_key: str
    name: str

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary"""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "FusionConfig":
        """Create config from dictionary"""
        return cls(**data)

    def is_private_key_encrypted(self) -> bool:
        """Check if the private key is encrypted"""
        return EncryptionManager.is_encrypted_private_key(self.private_key)

    def decrypt_private_key(self, password: str) -> str:
        """
        Decrypt the private key if it's encrypted.

        Args:
            password: The password used for encryption

        Returns:
            Decrypted private key

        Raises:
            ValueError: If decryption fails or private key is not encrypted
        """
        if not self.is_private_key_encrypted():
            raise ValueError("Private key is not encrypted")

        return EncryptionManager.decrypt_private_key(self.private_key, password)

    def get_decrypted_private_key(self, password: Optional[str] = None) -> str:
        """
        Get the private key, decrypting if necessary.

        Args:
            password: The password for decryption (required if private key is encrypted)

        Returns:
            Decrypted private key

        Raises:
            ValueError: If private key is encrypted but no password provided
        """
        if self.is_private_key_encrypted():
            if password is None:
                raise ValueError(
                    "Private key is encrypted. Password required for decryption."
                )
            return self.decrypt_private_key(password)
        return self.private_key


class ConfigManager:
    """Manages YAML configuration files for IPOR Fusion"""

    DEFAULT_CONFIG_FILE = "ipor-fusion-config.yaml"
    DEFAULT_CONFIG_TEMPLATE = {
        "plasma_vault_address": "",
        "rpc_url": "",
        "private_key": "",
        "name": "",
    }

    @staticmethod
    def get_config_path(config_file: Optional[str] = None) -> Path:
        """Get the path to the configuration file"""
        if config_file:
            return Path(config_file)
        return Path.cwd() / ConfigManager.DEFAULT_CONFIG_FILE

    @staticmethod
    def create_config(
            plasma_vault_address: str,
            rpc_url: str,
            private_key: str,
            name: str,
            encrypt_private_key: bool = False,
            config_file: Optional[str] = None,
            encryption_password: Optional[str] = None,
    ) -> Path:
        """
        Create a YAML configuration file with the provided settings.

        Args:
            plasma_vault_address: The plasma vault address
            rpc_url: The RPC provider URL
            private_key: The private key
            config_file: Optional path for the config file
            encrypt_private_key: Whether to encrypt the private key (default: False)
            encryption_password: Password for encryption (required if encrypt_private_key is True)

        Returns:
            Path to the created configuration file
        """
        config_path = ConfigManager.get_config_path(config_file)

        plasma_vault_checksum_address = Web3.to_checksum_address(plasma_vault_address)

        # Handle private key encryption
        final_private_key = private_key
        if encrypt_private_key:
            if not encryption_password:
                raise ValueError(
                    "Encryption password is required when encrypt_private_key is True"
                )
            final_private_key = EncryptionManager.encrypt_private_key(
                private_key, encryption_password
            )

        config = FusionConfig(
            plasma_vault_address=plasma_vault_checksum_address,
            rpc_url=rpc_url,
            private_key=final_private_key,
            name=name
        )

        ConfigManager._write_config(config_path, config)
        return config_path

    @staticmethod
    def load_config(config_file: Optional[str] = None) -> FusionConfig:
        """
        Load configuration from YAML file.

        Args:
            config_file: Optional path to the configuration file

        Returns:
            FusionConfig object

        Raises:
            FileNotFoundError: If config file doesn't exist
            yaml.YAMLError: If config file is invalid
            ValueError: If config file is empty
        """
        config_path = ConfigManager.get_config_path(config_file)

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            raise ValueError("Configuration file is empty")

        return FusionConfig.from_dict(data)

    @staticmethod
    def update_config(
            config_file: Optional[str] = None,
            encrypt_private_key: bool = False,
            encryption_password: Optional[str] = None,
            **kwargs,
    ) -> Path:
        """
        Update existing configuration file with new values.

        Args:
            config_file: Optional path to the configuration file
            encrypt_private_key: Whether to encrypt the private key
            encryption_password: Password for encryption (required if encrypt_private_key is True)
            **kwargs: Configuration values to update

        Returns:
            Path to the updated configuration file
        """
        config_path = ConfigManager.get_config_path(config_file)

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        # Load existing config
        config = ConfigManager.load_config(config_file)

        # Handle private key encryption if updating private_key
        if "private_key" in kwargs and encrypt_private_key:
            if not encryption_password:
                raise ValueError(
                    "Encryption password is required when encrypt_private_key is True"
                )
            kwargs["private_key"] = EncryptionManager.encrypt_private_key(
                kwargs["private_key"], encryption_password
            )

        # Update with new values
        for key, value in kwargs.items():
            if hasattr(config, key):
                setattr(config, key, value)

        # Write updated config
        ConfigManager._write_config(config_path, config)
        return config_path

    @staticmethod
    def validate_config(config: FusionConfig) -> bool:
        """
        Validate configuration values.

        Args:
            config: FusionConfig object to validate

        Returns:
            True if valid, raises ValueError if invalid
        """
        click.echo(config)
        if not config.plasma_vault_address:
            raise ValueError("plasma_vault_address is required")

        if not config.rpc_url:
            raise ValueError("rpc_url is required")

        if not config.private_key:
            raise ValueError("private_key is required")

        if not config.plasma_vault_address.startswith("0x"):
            raise ValueError("plasma_vault_address must be a valid Ethereum address")

        return True

    @staticmethod
    def _write_config(config_path: Path, config: FusionConfig) -> None:
        """Write configuration to YAML file"""
        config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config.to_dict(), f, default_flow_style=False, indent=2)

    @staticmethod
    def get_template() -> Dict[str, Any]:
        """Get configuration template"""
        return ConfigManager.DEFAULT_CONFIG_TEMPLATE.copy()

    @staticmethod
    def encrypt_existing_private_key(
            config_file: Optional[str] = None, password: Optional[str] = None
    ) -> Path:
        """
        Encrypt the private key in an existing configuration file.

        Args:
            config_file: Optional path to the configuration file
            password: Password for encryption (will prompt if not provided)

        Returns:
            Path to the updated configuration file
        """
        config_path = ConfigManager.get_config_path(config_file)

        if not config_path.exists():
            raise FileNotFoundError(f"Configuration file not found: {config_path}")

        # Load existing config
        config = ConfigManager.load_config(config_file)

        # Check if private key is already encrypted
        if config.is_private_key_encrypted():
            raise ValueError("Private key is already encrypted")

        # Prompt for password if not provided
        if not password:
            password = click.prompt(
                "Enter encryption password", hide_input=True, confirmation_prompt=True
            )

        # Encrypt the private key
        encrypted_private_key = EncryptionManager.encrypt_private_key(
            config.private_key, password
        )

        # Update the config
        config.private_key = encrypted_private_key

        # Write updated config
        ConfigManager._write_config(config_path, config)
        return config_path

    @staticmethod
    def convert_from_env(config_file: str = ".env") -> Path:
        """
        Convert existing .env file to YAML configuration.

        Args:
            config_file: Path to the .env file

        Returns:
            Path to the created YAML configuration file
        """
        env_path = Path(config_file)
        if not env_path.exists():
            raise FileNotFoundError(f".env file not found: {env_path}")

        load_dotenv(env_path)

        # Extract values
        plasma_vault = os.getenv("plasma_vault_address", "")
        rpc_url = os.getenv("rpc_url", "")
        private_key = os.getenv("PRIVATE_KEY", "")

        if not all([plasma_vault, rpc_url, private_key]):
            raise ValueError("Missing required environment variables in .env file")

        # Create YAML config
        return ConfigManager.create_config(
            plasma_vault_address=plasma_vault,
            rpc_url=rpc_url,
            private_key=private_key,
        )

    @staticmethod
    def create_example_config() -> Dict[str, Any]:
        """
        Create an example configuration showing different ways to write config fields as strings.

        Returns:
            Example configuration dictionary
        """
        return {
            # Required fields (always strings)
            "plasma_vault_address": "0x1234567890123456789012345678901234567890",
            "rpc_url": "https://example.com/rpc",
            "private_key": "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            # Optional fields with different string formats
            "network": "mainnet",
            "gas_limit": "300000",  # String format for gas limit
            "gas_price": "2000000000",  # String format for gas price (2 gwei)
            "max_priority_fee": "1500000000",  # String format for priority fee (1.5 gwei)
            # Additional string fields
            "custom_field": "This is a custom string field",
            "description": "Configuration for IPOR Fusion deployment",
            "notes": "This configuration is for testing purposes only",
            # Example with quotes for clarity
            "quoted_string": '"This is a quoted string"',
            # Example with special characters
            "special_chars": "Line 1\nLine 2\nLine 3",  # Multi-line string
            # Example with YAML block scalar
            "block_text": "This is a block of text\nthat spans multiple lines\nand preserves formatting",
        }

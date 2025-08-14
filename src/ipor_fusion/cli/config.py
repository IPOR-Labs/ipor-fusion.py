#!/usr/bin/env python3
"""
YAML configuration manager for IPOR Fusion CLI
"""

from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, List

import click
import yaml
from eth_typing import ChecksumAddress

from ipor_fusion.cli.encryption import EncryptionManager


class PlasmaVaultConfig:
    """Plasma Vault configuration"""
    plasma_vault_address: ChecksumAddress
    private_key: str
    name: str

    def __init__(self, plasma_vault_address: ChecksumAddress, name: str,
                 private_key: str = None):
        self.plasma_vault_address = plasma_vault_address
        self.name = name
        self.private_key = private_key

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

    def to_dict(self):
        return {
            'name': self.name,
            'plasma_vault_address': self.plasma_vault_address,
            'private_key': self.private_key,
        }

    @classmethod
    def from_dict(cls, vault_data: Dict[str, Any]) -> Any:
        return PlasmaVaultConfig(
            plasma_vault_address=vault_data['plasma_vault_address'],
            name=vault_data['name'],
            private_key=vault_data['private_key']
        )

    def validate(self):
        pass


class ChainConfig:
    """Chain configuration"""
    chain_id: int
    chain_name: str
    chain_short_name: str
    rpc_url: str
    plasma_vaults: List[PlasmaVaultConfig]

    def __init__(self, chain_id: int, chain_name: str, chain_short_name: str, rpc_url: str,
                 plasma_vaults: List[PlasmaVaultConfig]):
        self.chain_id = chain_id
        self.chain_name = chain_name
        self.chain_short_name = chain_short_name
        self.rpc_url = rpc_url
        self.plasma_vaults = plasma_vaults

    def to_dict(self):
        return {
            'chain_id': self.chain_id,
            'chain_name': self.chain_name,
            'chain_short_name': self.chain_short_name,
            'rpc_url': self.rpc_url,
            'plasma_vaults': [vault.to_dict() for vault in self.plasma_vaults]
        }

    @classmethod
    def from_dict(cls, chain_data: Dict[str, Any]) -> Any:
        plasma_vaults = []
        for vault_data in chain_data.get('plasma_vaults', []):
            plasma_vaults.append(PlasmaVaultConfig.from_dict(vault_data))

        return ChainConfig(
            chain_id=chain_data['chain_id'],
            chain_name=chain_data['chain_name'],
            chain_short_name=chain_data['chain_short_name'],
            rpc_url=chain_data['rpc_url'],
            plasma_vaults=plasma_vaults
        )

    def validate(self):
        pass


@dataclass
class GeneralConfig:
    """Configuration data class for IPOR Fusion"""

    default_plasma_vault_name: str
    chain_configs: List[ChainConfig]

    def to_dict(self) -> Dict[str, Any]:
        """Convert config to dictionary"""
        return {
            'default_plasma_vault_name' : self.default_plasma_vault_name,
            'chain_configs': [chain.to_dict() for chain in self.chain_configs]
        }

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GeneralConfig":
        """Create config from dictionary"""
        chain_configs = []
        for chain_data in data.get('chain_configs', []):
            plasma_vaults = []
            for vault_data in chain_data.get('plasma_vaults', []):
                plasma_vaults.append(PlasmaVaultConfig(
                    plasma_vault_address=vault_data['plasma_vault_address'],
                    name=vault_data['name'],
                    private_key=vault_data['private_key']
                ))

            chain_configs.append(ChainConfig(
                chain_id=chain_data['chain_id'],
                chain_name=chain_data['chain_name'],
                chain_short_name=chain_data['chain_short_name'],
                rpc_url=chain_data['rpc_url'],
                plasma_vaults=plasma_vaults
            ))

        return cls(default_plasma_vault_name=data.get('default_plasma_vault_name'), chain_configs=chain_configs)

    def validate(self):
        pass


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
    def create_config_file(
            general_config: GeneralConfig,
            config_file: Optional[str] = None,
    ) -> Path:
        """
        Create a YAML configuration file with the provided settings.

        Args:
            config_file: Optional path for the config file

        Returns:
            Path to the created configuration file
        """
        config_path = ConfigManager.get_config_path(config_file)

        # Handle private key encryption
        ConfigManager._write_config(config_path, general_config)
        return config_path

    @staticmethod
    def set_rpc_url(
            rpc_url: str,
            config_file: Optional[str] = None,
    ) -> Path:
        """
        Create a YAML configuration file with the provided settings.

        Args:
            rpc_url: The RPC provider URL
            config_file: Optional path for the config file

        Returns:
            Path to the created configuration file
        """
        config_path = ConfigManager.get_config_path(config_file)

        config = GeneralConfig(
            rpc_url=rpc_url,
        )

        ConfigManager._write_config(config_path, config)
        return config_path

    @staticmethod
    def load_config(config_file: Optional[str] = None) -> GeneralConfig:
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

        return GeneralConfig.from_dict(data)

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
    def validate_config(config: GeneralConfig) -> bool:
        try:
            config.validate()
            return True
        except Exception as e:
            click.secho(f"Error validating configuration: {e}", fg="red", err=True)


    @staticmethod
    def _write_config(config_path: Path, config: GeneralConfig) -> None:
        """Write configuration to YAML file"""
        config_path.parent.mkdir(parents=True, exist_ok=True)

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config.to_dict(), f, default_flow_style=False, indent=2)

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

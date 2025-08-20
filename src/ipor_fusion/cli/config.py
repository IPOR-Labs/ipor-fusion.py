#!/usr/bin/env python3
"""
YAML configuration manager for IPOR Fusion CLI
"""
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Dict, Any, Optional, List

import click
import yaml
from collections import OrderedDict
from eth_typing import ChecksumAddress

from ipor_fusion.cli.encryption import EncryptionManager


class FuseConfig:
    """Fuse configuration"""

    fuse_address: ChecksumAddress
    fuse_name: str

    def __init__(self, fuse_address: ChecksumAddress, fuse_name: str = None):
        self.fuse_address = fuse_address
        self.fuse_name = fuse_name

    @classmethod
    def from_dict(cls, fuse_data: Dict[str, Any]) -> Any:
        return FuseConfig(
            fuse_address=fuse_data["fuse_address"],
            fuse_name=fuse_data.get("fuse_name"),
        )

    def to_dict(self) -> OrderedDict:
        result = OrderedDict()
        if self.fuse_address:
            result["fuse_address"] = self.fuse_address
        if self.fuse_name:
            result["fuse_name"] = self.fuse_name
        return result


class PlasmaVaultConfig:
    """Plasma Vault configuration"""

    plasma_vault_address: ChecksumAddress
    private_key: str
    name: str
    fuses: List[FuseConfig] = []
    rewards_fuses: List[FuseConfig] = []

    def __init__(
        self,
        plasma_vault_address: ChecksumAddress,
        name: str,
        private_key: str = None,
        fuses: List[FuseConfig] = None,
        rewards_fuses: List[FuseConfig] = None,
    ):
        self.plasma_vault_address = plasma_vault_address
        self.name = name
        self.private_key = private_key
        self.fuses = fuses
        self.rewards_fuses = rewards_fuses

    def is_private_key_encrypted(self) -> bool:
        return EncryptionManager.is_encrypted_private_key(self.private_key)

    def decrypt_private_key(self, password: str) -> str:
        if not self.is_private_key_encrypted():
            raise ValueError("Private key is not encrypted")

        return EncryptionManager.decrypt_private_key(self.private_key, password)

    def get_decrypted_private_key(self, password: Optional[str] = None) -> str:
        if self.is_private_key_encrypted():
            if password is None:
                raise ValueError(
                    "Private key is encrypted. Password required for decryption."
                )
            return self.decrypt_private_key(password)
        return self.private_key

    def to_dict(self) -> OrderedDict:
        result = OrderedDict()
        if self.name:
            result["name"] = self.name
        if self.plasma_vault_address:
            result["plasma_vault_address"] = self.plasma_vault_address
        if self.private_key:
            result["private_key"] = self.private_key
        if self.fuses:
            result["fuses"] = [fuse.to_dict() for fuse in self.fuses]
        if self.rewards_fuses:
            result["rewards_fuses"] = [
                rewards_fuse.to_dict() for rewards_fuse in self.rewards_fuses
            ]
        return result

    @classmethod
    def from_dict(cls, vault_data: Dict[str, Any]) -> Any:
        fuses = []
        if vault_data.get("fuses"):
            fuses = [
                FuseConfig.from_dict(fuse_data) for fuse_data in vault_data.get("fuses")
            ]

        rewards_fuses = []
        if vault_data.get("rewards_fuses"):
            rewards_fuses = [
                FuseConfig.from_dict(fuse_data)
                for fuse_data in vault_data.get("rewards_fuses")
            ]
        return PlasmaVaultConfig(
            plasma_vault_address=vault_data["plasma_vault_address"],
            name=vault_data["name"],
            private_key=vault_data.get("private_key"),
            fuses=fuses,
            rewards_fuses=rewards_fuses,
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

    def __init__(
        self,
        chain_id: int,
        chain_name: str,
        chain_short_name: str,
        rpc_url: Optional[str],
        plasma_vaults: List[PlasmaVaultConfig],
        scan_api_access_token: Optional[str] = None,
    ):
        self.chain_id = chain_id
        self.chain_name = chain_name
        self.chain_short_name = chain_short_name
        self.rpc_url = rpc_url
        self.scan_api_access_token = scan_api_access_token
        self.plasma_vaults = plasma_vaults

    def to_dict(self) -> OrderedDict:
        result = OrderedDict()
        if self.chain_id:
            result["chain_id"] = self.chain_id
        if self.chain_name:
            result["chain_name"] = self.chain_name
        if self.chain_short_name:
            result["chain_short_name"] = self.chain_short_name
        if self.rpc_url:
            result["rpc_url"] = self.rpc_url
        if self.scan_api_access_token:
            result["scan_api_access_token"] = self.scan_api_access_token
        if self.plasma_vaults:
            result["plasma_vaults"] = [vault.to_dict() for vault in self.plasma_vaults]
        return result

    @classmethod
    def from_dict(cls, chain_data: Dict[str, Any]) -> Any:
        plasma_vaults = []
        for vault_data in chain_data.get("plasma_vaults", []):
            plasma_vaults.append(PlasmaVaultConfig.from_dict(vault_data))

        return ChainConfig(
            chain_id=chain_data["chain_id"],
            chain_name=chain_data["chain_name"],
            chain_short_name=chain_data["chain_short_name"],
            rpc_url=chain_data["rpc_url"],
            plasma_vaults=plasma_vaults,
            scan_api_access_token=chain_data.get("scan_api_access_token"),
        )

    def validate(self):
        pass


@dataclass
class GeneralConfig:
    """Configuration data class for IPOR Fusion"""

    default_plasma_vault_name: str
    chain_configs: List[ChainConfig]

    def to_dict(self) -> OrderedDict:
        """Convert config to ordered dictionary"""
        result = OrderedDict()
        if self.default_plasma_vault_name:
            result["default_plasma_vault_name"] = self.default_plasma_vault_name
        if self.chain_configs:
            result["chain_configs"] = [chain.to_dict() for chain in self.chain_configs]
        return result

    @classmethod
    def from_dict(cls, data: Dict[str, Any]) -> "GeneralConfig":
        """Create config from dictionary"""
        chain_configs = []
        for chain_data in data.get("chain_configs", []):
            chain_configs.append(ChainConfig.from_dict(chain_data))

        return cls(
            default_plasma_vault_name=data.get("default_plasma_vault_name"),
            chain_configs=chain_configs,
        )

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
            click.secho(
                f'Configuration file not found: {config_path}. Try "fusion config init" command.',
                fg="red",
                err=True,
            )
            sys.exit(1)

        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)

        if not data:
            click.secho(
                f"Configuration file is empty: {config_path}", fg="red", err=True
            )
            sys.exit(1)

        try:
            return GeneralConfig.from_dict(data)
        except Exception as e:
            click.secho(f"Error loading configuration: {e}", fg="red", err=True)
            sys.exit(1)

    @staticmethod
    def update_config(
        config: GeneralConfig,
        config_file: Optional[str] = None,
    ):
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
            click.secho(
                f"Configuration file not found: {config_path}", fg="red", err=True
            )
            sys.exit(1)

        # Write updated config
        ConfigManager._write_config(config_path, config)

    @staticmethod
    def validate_config(config: GeneralConfig) -> bool:
        try:
            config.validate()
            return True
        except Exception as e:
            click.secho(f"Error validating configuration: {e}", fg="red", err=True)

    @staticmethod
    def _write_config(config_path: Path, general_config: GeneralConfig) -> None:
        """Write configuration to YAML file"""
        config_path.parent.mkdir(parents=True, exist_ok=True)

        # Add custom representer for OrderedDict
        def ordered_dict_presenter(dumper, data):
            return dumper.represent_mapping("tag:yaml.org,2002:map", data.items())

        # Add representer for OrderedDict
        yaml.add_representer(
            OrderedDict, ordered_dict_presenter, Dumper=yaml.SafeDumper
        )

        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(
                general_config.to_dict(),
                f,
                Dumper=yaml.SafeDumper,
                default_flow_style=False,
                indent=2,
            )

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

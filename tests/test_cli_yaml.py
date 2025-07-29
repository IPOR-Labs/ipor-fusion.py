#!/usr/bin/env python3
"""
Tests for the IPOR Fusion CLI with YAML configuration
"""

import os
import tempfile
import shutil
from pathlib import Path
import yaml

from click.testing import CliRunner

from ipor_fusion.cli.ipor_fusion_cli import cli
from ipor_fusion.cli.commands.init import init
from ipor_fusion.cli.commands.show import show
from ipor_fusion.cli.commands.base import BaseCommand


class TestYAMLConfig:
    """Test YAML configuration functionality"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.temp_dir)

    def teardown_method(self):
        """Clean up after tests"""
        os.chdir(self.original_cwd)

        shutil.rmtree(self.temp_dir)

    def test_create_config_file(self):
        """Test creating a YAML configuration file"""
        plasma_vault = "0x1234567890123456789012345678901234567890"
        rpc_url = "https://example.com/rpc"
        private_key = (
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        )

        config_path = BaseCommand.create_config_file(
            plasma_vault_address=plasma_vault,
            rpc_url=rpc_url,
            private_key=private_key,
            network="mainnet",
            gas_limit=300000,
        )

        assert config_path.exists()
        assert config_path.name == "ipor-fusion-config.yaml"

        # Check file contents
        with open(config_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            assert data["plasma_vault_address"] == plasma_vault
            assert data["rpc_url"] == rpc_url
            assert data["private_key"] == private_key
            assert data["network"] == "mainnet"
            assert data["gas_limit"] == 300000

    def test_load_config(self):
        """Test loading configuration from YAML file"""
        # Create test config file
        config_data = {
            "plasma_vault_address": "0x1234567890123456789012345678901234567890",
            "rpc_url": "https://example.com/rpc",
            "private_key": "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "network": "mainnet",
            "gas_limit": 300000,
        }

        config_path = Path("ipor-fusion-config.yaml")
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        config = BaseCommand.load_config()
        assert config.plasma_vault_address == config_data["plasma_vault_address"]
        assert config.rpc_url == config_data["rpc_url"]
        assert config.private_key == config_data["private_key"]
        assert config.network == config_data["network"]
        assert config.gas_limit == config_data["gas_limit"]


class TestInitCommandYAML:
    """Test the init command with YAML configuration"""

    def setup_method(self):
        """Set up test fixtures"""
        self.runner = CliRunner()
        self.temp_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.temp_dir)

    def teardown_method(self):
        """Clean up after tests"""
        os.chdir(self.original_cwd)

        shutil.rmtree(self.temp_dir)

    def test_init_command_help(self):
        """Test init command help"""
        result = self.runner.invoke(init, ["--help"])
        assert result.exit_code == 0
        assert "Initialize a YAML configuration file" in result.output

    def test_init_command_with_args(self):
        """Test init command with arguments"""
        result = self.runner.invoke(
            init,
            [
                "--plasma-vault-address",
                "0x1234567890123456789012345678901234567890",
                "--rpc-url",
                "https://example.com/rpc",
                "--private-key",
                "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
                "--network",
                "mainnet",
                "--gas-limit",
                "300000",
            ],
        )
        assert result.exit_code == 0
        assert "Configuration file created" in result.output

        # Check that YAML file was created
        config_file = Path("ipor-fusion-config.yaml")
        assert config_file.exists()

        # Check file contents
        with open(config_file, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            assert (
                data["plasma_vault_address"]
                == "0x1234567890123456789012345678901234567890"
            )
            assert data["rpc_url"] == "https://example.com/rpc"
            assert (
                data["private_key"]
                == "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
            )
            assert data["network"] == "mainnet"
            assert data["gas_limit"] == 300000

    def test_init_command_interactive(self):
        """Test init command with interactive prompts"""
        inputs = [
            "0x1234567890123456789012345678901234567890",  # plasma vault address
            "https://example.com/rpc",  # provider url
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",  # private key
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",  # confirm private key
        ]
        result = self.runner.invoke(init, input="\n".join(inputs))
        assert result.exit_code == 0
        assert "Configuration file created" in result.output

        # Check that YAML file was created
        config_file = Path("ipor-fusion-config.yaml")
        assert config_file.exists()


class TestShowCommand:
    """Test the show command"""

    def setup_method(self):
        """Set up test fixtures"""
        self.runner = CliRunner()
        self.temp_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.temp_dir)

    def teardown_method(self):
        """Clean up after tests"""
        os.chdir(self.original_cwd)

        shutil.rmtree(self.temp_dir)

    def test_show_command_help(self):
        """Test show command help"""
        result = self.runner.invoke(show, ["--help"])
        assert result.exit_code == 0
        assert "Display the current configuration" in result.output

    def test_show_configuration(self):
        """Test showing configuration"""
        # Create test config file
        config_data = {
            "plasma_vault_address": "0x1234567890123456789012345678901234567890",
            "rpc_url": "https://example.com/rpc",
            "private_key": "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "network": "mainnet",
            "gas_limit": 300000,
        }

        config_path = Path("ipor-fusion-config.yaml")
        with open(config_path, "w", encoding="utf-8") as f:
            yaml.dump(config_data, f)

        result = self.runner.invoke(show)
        assert result.exit_code == 0
        assert "Current Configuration:" in result.output
        assert "0x1234567890123456789012345678901234567890" in result.output
        assert "https://example.com/rpc" in result.output
        assert "mainnet" in result.output
        assert "300000" in result.output


class TestCLIIntegrationYAML:
    """Integration tests for the CLI with YAML configuration"""

    def setup_method(self):
        """Set up test fixtures"""
        self.runner = CliRunner()
        self.temp_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.temp_dir)

    def teardown_method(self):
        """Clean up after tests"""
        os.chdir(self.original_cwd)

        shutil.rmtree(self.temp_dir)

    def test_cli_help_shows_new_commands(self):
        """Test that CLI help shows new YAML commands"""
        result = self.runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "init" in result.output
        assert "show" in result.output

    def test_full_cli_workflow_yaml(self):
        """Test a complete CLI workflow with YAML configuration"""
        # Test init command
        result = self.runner.invoke(
            cli,
            [
                "init",
                "--plasma-vault-address",
                "0x1234567890123456789012345678901234567890",
                "--rpc-url",
                "https://example.com/rpc",
                "--private-key",
                "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            ],
        )
        assert result.exit_code == 0
        assert "Configuration file created" in result.output

        # Test show command
        result = self.runner.invoke(cli, ["show"])
        assert result.exit_code == 0
        assert "Current Configuration:" in result.output

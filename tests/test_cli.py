#!/usr/bin/env python3
"""
Tests for the IPOR Fusion CLI functionality
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import patch, MagicMock
from click.testing import CliRunner

from ipor_fusion.cli.ipor_fusion_cli import cli
from ipor_fusion.cli.commands.init import init
from ipor_fusion.cli.commands.base import BaseCommand
from ipor_fusion.cli.commands.registry import discover_commands, register_commands


class TestCLI:
    """Test the main CLI functionality"""

    def setup_method(self):
        """Set up test fixtures"""
        self.runner = CliRunner()
        self.temp_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.temp_dir)

    def teardown_method(self):
        """Clean up after tests"""
        os.chdir(self.original_cwd)
        import shutil

        shutil.rmtree(self.temp_dir)

    def test_cli_help(self):
        """Test that CLI shows help"""
        result = self.runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "ipor-fusion CLI" in result.output
        assert "init" in result.output

    def test_cli_no_args(self):
        """Test CLI without arguments shows help"""
        result = self.runner.invoke(cli, [])
        assert result.exit_code ==2
        assert "ipor-fusion CLI" in result.output

    def test_cli_invalid_command(self):
        """Test CLI with invalid command"""
        result = self.runner.invoke(cli, ["invalid-command"])
        assert result.exit_code == 2
        assert "No such command" in result.output


class TestInitCommand:
    """Test the init command"""

    def setup_method(self):
        """Set up test fixtures"""
        self.runner = CliRunner()
        self.temp_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.temp_dir)

    def teardown_method(self):
        """Clean up after tests"""
        os.chdir(self.original_cwd)
        import shutil

        shutil.rmtree(self.temp_dir)

    def test_init_command_help(self):
        """Test init command help"""
        result = self.runner.invoke(init, ["--help"])
        assert result.exit_code == 0
        assert "Initialize a YAML configuration" in result.output

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
        assert "Configuration file created at" in result.output

        # Check that ipor-fusion-config.yaml file was created
        config_file = Path("ipor-fusion-config.yaml")
        assert config_file.exists()

        # Check file contents
        with open(config_file, "r") as f:
            content = f.read()
            assert (
                "plasma_vault_address: '0x1234567890123456789012345678901234567890'"
                in content
            )
            assert "provider_url: https://example.com/rpc" in content
            assert (
                "private_key: '0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890'"
                in content
            )

    def test_init_command_with_args(self):
        """Test init command with command line arguments"""
        result = self.runner.invoke(
            init,
            [
                "--plasma-vault-address",
                "0x1234567890123456789012345678901234567890",
                "--provider-url",
                "https://example.com/rpc",
                "--private-key",
                "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            ],
        )
        assert result.exit_code == 0
        assert "Configuration file created at" in result.output

    def test_init_command_overwrite_existing(self):
        """Test init command overwrites existing ipor-fusion-config.yaml file"""
        # Create existing ipor-fusion-config.yaml file
        with open("ipor-fusion-config.yaml", "w") as f:
            f.write("EXISTING: value\n")
            f.close()


        # Run init command
        inputs = [
            "0x1234567890123456789012345678901234567890",
            "https://example.com/rpc",
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "y",  # confirm overwrite
        ]
        result = self.runner.invoke(init, input="\n".join(inputs))
        assert result.exit_code == 0

        # Check that file was overwritten
        with open("ipor-fusion-config.yaml", "r") as f:
            content = f.read()
            assert "EXISTING=value" not in content
            assert "PLASMA_VAULT_ADDRESS" in content

    def test_init_command_abort_overwrite(self):
        """Test init command aborts when user doesn't confirm overwrite"""
        # Create existing ipor-fusion-config.yaml file
        with open("ipor-fusion-config.yaml", "w") as f:
            f.write("EXISTING=value\n")

        # Run init command
        inputs = [
            "0x1234567890123456789012345678901234567890",
            "https://example.com/rpc",
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "n",  # don't confirm overwrite
        ]
        result = self.runner.invoke(init, input="\n".join(inputs))
        assert result.exit_code == 1  # Aborted

        # Check that file was not overwritten
        with open("ipor-fusion-config.yaml", "r") as f:
            content = f.read()
            assert "EXISTING=value" in content


class TestBaseCommand:
    """Test the BaseCommand class"""

    def setup_method(self):
        """Set up test fixtures"""
        self.temp_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.temp_dir)

    def teardown_method(self):
        """Clean up after tests"""
        os.chdir(self.original_cwd)
        import shutil

        shutil.rmtree(self.temp_dir)

    def test_create_config_file(self):
        """Test BaseCommand.create_config_file method"""
        plasma_vault = "0x1234567890123456789012345678901234567890"
        provider_url = "https://example.com/rpc"
        private_key = (
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        )

        env_path = BaseCommand.create_config_file(plasma_vault, provider_url, private_key)

        assert env_path.exists()
        assert env_path.name == "ipor-fusion-config.yaml"

        with open(env_path, "r") as f:
            content = f.read()
            assert f"plasma_vault_address: '{plasma_vault}'" in content
            assert f"provider_url: {provider_url}" in content
            assert f"private_key: '{private_key}'" in content

    def test_create_config_file_custom_path(self):
        """Test BaseCommand.create_config_file with custom path"""
        custom_path = Path("customipor-fusion-config.yaml")
        plasma_vault = "0x1234567890123456789012345678901234567890"
        provider_url = "https://example.com/rpc"
        private_key = (
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        )

        env_path = BaseCommand.create_config_file(
            plasma_vault, provider_url, private_key, custom_path
        )

        assert env_path == custom_path
        assert custom_path.exists()

    def test_create_config_file_overwrite_existing(self):
        """Test BaseCommand.create_config_file overwrites existing file"""
        # Create existing file
        existing_file = Path("ipor-fusion-config.yaml")
        with open(existing_file, "w") as f:
            f.write("EXISTING=value\n")

        plasma_vault = "0x1234567890123456789012345678901234567890"
        provider_url = "https://example.com/rpc"
        private_key = (
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
        )

        # Mock click.confirm to return True (confirm overwrite)
        with patch("click.confirm", return_value=True):
            env_path = BaseCommand.create_config_file(
                plasma_vault, provider_url, private_key
            )

            assert env_path.exists()
            with open(env_path, "r") as f:
                content = f.read()
                assert "EXISTING=value" not in content
                assert "PLASMA_VAULT_ADDRESS" in content

    def test_get_common_options(self):
        """Test BaseCommand.get_common_options method"""
        options = BaseCommand.get_common_options()
        assert len(options) == 3

        # Check that all options are click.option decorators
        import click

        for option in options:
            assert isinstance(option, click.Option)


class TestCommandRegistry:
    """Test the command registry functionality"""

    def test_discover_commands(self):
        """Test that commands are discovered correctly"""
        commands = discover_commands()

        command_names = [cmd.name for cmd in commands]
        assert "init" in command_names
        assert "show" in command_names

    def test_register_commands(self):
        """Test that commands are registered correctly"""
        # Create a mock CLI group
        mock_cli = MagicMock()

        # Register commands
        register_commands(mock_cli)

        # Check that add_command was called for each discovered command
        commands = discover_commands()
        assert mock_cli.add_command.call_count == len(commands)


class TestCLIIntegration:
    """Integration tests for the CLI"""

    def setup_method(self):
        """Set up test fixtures"""
        self.runner = CliRunner()
        self.temp_dir = tempfile.mkdtemp()
        self.original_cwd = os.getcwd()
        os.chdir(self.temp_dir)

    def teardown_method(self):
        """Clean up after tests"""
        os.chdir(self.original_cwd)
        import shutil

        shutil.rmtree(self.temp_dir)

    def test_full_cli_workflow(self):
        """Test a complete CLI workflow"""
        # Test help
        result = self.runner.invoke(cli, ["--help"])
        assert result.exit_code == 0

        # Test init command
        inputs = [
            "0x1234567890123456789012345678901234567890",
            "https://example.com/rpc",
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
        ]
        result = self.runner.invoke(cli, ["init"], input="\n".join(inputs))
        assert result.exit_code == 0
        assert "Configuration file created at" in result.output

        inputs = [
            "0x1234567890123456789012345678901234567890",
            "https://example.com/rpc",
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            "n",  # don't overwrite
        ]
        result = self.runner.invoke(cli, ["show"], input="\n".join(inputs))
        assert result.exit_code == 0  # Aborted

    def test_cli_with_invalid_private_key(self):
        """Test CLI with invalid private key format"""
        result = self.runner.invoke(
            cli,
            [
                "init",
                "--plasma-vault-address",
                "0x1234567890123456789012345678901234567890",
                "--provider-url",
                "https://example.com/rpc",
                "--private-key",
                "invalid-key",
            ],
        )
        # Should still work as validation is not implemented in the current version
        assert result.exit_code == 0

    def test_cli_with_invalid_address(self):
        """Test CLI with invalid address format"""
        result = self.runner.invoke(
            cli,
            [
                "init",
                "--plasma-vault-address",
                "invalid-address",
                "--provider-url",
                "https://example.com/rpc",
                "--private-key",
                "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            ],
        )
        # Should still work as validation is not implemented in the current version
        assert result.exit_code == 0

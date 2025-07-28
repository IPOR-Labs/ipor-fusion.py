#!/usr/bin/env python3
"""
Simple tests for the IPOR Fusion CLI functionality
"""

from click.testing import CliRunner
from unittest.mock import patch

from ipor_fusion.cli.ipor_fusion_cli import cli
from ipor_fusion.cli.commands.init import init
from ipor_fusion.cli.commands.base import BaseCommand


class TestCLIBasic:
    """Basic CLI tests"""

    def setup_method(self):
        """Set up test fixtures"""
        self.runner = CliRunner()

    def test_cli_help(self):
        """Test that CLI shows help"""
        result = self.runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "ipor-fusion CLI" in result.output

    def test_cli_commands_listed(self):
        """Test that all commands are listed in help"""
        result = self.runner.invoke(cli, ["--help"])
        assert result.exit_code == 0
        assert "init" in result.output

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

    def test_init_help(self):
        """Test init command help"""
        result = self.runner.invoke(init, ["--help"])
        assert result.exit_code == 0
        assert "Initialize a .env file" in result.output

    def test_init_with_args(self):
        """Test init command with arguments"""
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
        assert ".env file created" in result.output


class TestBaseCommand:
    """Test the BaseCommand class"""

    def test_get_common_options(self):
        """Test BaseCommand.get_common_options method"""
        options = BaseCommand.get_common_options()
        assert len(options) == 3

        # Check that all options are click.option decorators
        import click

        for option in options:
            assert isinstance(option, click.Option)

    def test_create_config_file_mocked(self):
        """Test BaseCommand.create_config_file method with mocked file operations"""
        with (
            patch("pathlib.Path.exists", return_value=False),
            patch("click.confirm"),
            patch("builtins.open", create=True) as mock_open,
        ):

            mock_file = mock_open.return_value.__enter__.return_value

            plasma_vault = "0x1234567890123456789012345678901234567890"
            provider_url = "https://example.com/rpc"
            private_key = (
                "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
            )

            env_path = BaseCommand.create_config_file(
                plasma_vault, provider_url, private_key
            )

            # Check that file was written with correct content
            mock_file.write.assert_called()
            calls = mock_file.write.call_args_list

            # Check that the expected content was written
            written_content = "".join([call[0][0] for call in calls])
            assert (
                "PLASMA_VAULT_ADDRESS=0x1234567890123456789012345678901234567890"
                in written_content
            )
            assert "PROVIDER_URL=https://example.com/rpc" in written_content
            assert (
                "PRIVATE_KEY=0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890"
                in written_content
            )


class TestCLIIntegration:
    """Integration tests for the CLI"""

    def setup_method(self):
        """Set up test fixtures"""
        self.runner = CliRunner()

    def test_cli_init_command(self):
        """Test CLI init command"""
        result = self.runner.invoke(cli, ["init", "--help"])
        assert result.exit_code == 0
        assert "Initialize a .env file" in result.output

    def test_cli_command_execution(self):
        """Test CLI command execution with arguments"""
        result = self.runner.invoke(
            cli,
            [
                "init",
                "--plasma-vault-address",
                "0x1234567890123456789012345678901234567890",
                "--provider-url",
                "https://example.com/rpc",
                "--private-key",
                "0xabcdef1234567890abcdef1234567890abcdef1234567890abcdef1234567890",
            ],
        )
        assert result.exit_code == 0
        assert ".env file created" in result.output

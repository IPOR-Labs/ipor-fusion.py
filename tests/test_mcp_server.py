"""Tests for the MCP server tool definitions."""

from subprocess import CompletedProcess
from unittest.mock import patch

from ipor_fusion.mcp.server import (
    _run_fusion,
    vault_info,
    vault_list,
    vault_market_detail,
    vault_add,
    vault_remove,
    config_show,
    config_set_provider,
    config_set_etherscan_key,
    config_set_default_vault,
)


class TestRunFusion:
    @patch("ipor_fusion.mcp.server.subprocess.run")
    def test_returns_stdout_on_success(self, mock_run):
        mock_run.return_value = CompletedProcess(
            args=["fusion"], returncode=0, stdout="ok\n", stderr=""
        )
        assert _run_fusion("vault", "list") == "ok"
        mock_run.assert_called_once_with(
            ["fusion", "vault", "list"],
            capture_output=True,
            text=True,
            timeout=120,
            check=False,
        )

    @patch("ipor_fusion.mcp.server.subprocess.run")
    def test_returns_error_on_failure(self, mock_run):
        mock_run.return_value = CompletedProcess(
            args=["fusion"], returncode=1, stdout="", stderr="bad input\n"
        )
        result = _run_fusion("vault", "info")
        assert result == "Error: bad input"

    @patch("ipor_fusion.mcp.server.subprocess.run")
    def test_returns_stdout_as_error_when_stderr_empty(self, mock_run):
        mock_run.return_value = CompletedProcess(
            args=["fusion"], returncode=1, stdout="fallback msg\n", stderr=""
        )
        result = _run_fusion("config", "show")
        assert result == "Error: fallback msg"


class TestVaultInfo:
    @patch("ipor_fusion.mcp.server._run_fusion", return_value="{}")
    def test_default_args(self, mock_run):
        vault_info()
        mock_run.assert_called_once_with("vault", "info", "--json")

    @patch("ipor_fusion.mcp.server._run_fusion", return_value="{}")
    def test_all_args(self, mock_run):
        vault_info(vault_address="0xABC", chain_id=42161, block_number=100)
        mock_run.assert_called_once_with(
            "vault",
            "info",
            "--json",
            "--vault",
            "0xABC",
            "--chain-id",
            "42161",
            "--block-number",
            "100",
        )


class TestVaultMarketDetail:
    @patch("ipor_fusion.mcp.server._run_fusion", return_value="{}")
    def test_default_args(self, mock_run):
        vault_market_detail(market_id=1)
        mock_run.assert_called_once_with(
            "vault", "market-detail", "--json", "--market-id", "1"
        )

    @patch("ipor_fusion.mcp.server._run_fusion", return_value="{}")
    def test_all_args(self, mock_run):
        vault_market_detail(
            vault_address="0xABC", chain_id=42161, market_id=14, block_number=100
        )
        mock_run.assert_called_once_with(
            "vault",
            "market-detail",
            "--json",
            "--market-id",
            "14",
            "--vault",
            "0xABC",
            "--chain-id",
            "42161",
            "--block-number",
            "100",
        )


class TestVaultList:
    @patch("ipor_fusion.mcp.server._run_fusion", return_value="[]")
    def test_calls_list(self, mock_run):
        vault_list()
        mock_run.assert_called_once_with("vault", "list", "--json")


class TestVaultAdd:
    @patch("ipor_fusion.mcp.server._run_fusion", return_value="Vault added.")
    def test_minimal_args(self, mock_run):
        vault_add(address="0xABC")
        mock_run.assert_called_once_with("vault", "add", "0xABC")

    @patch("ipor_fusion.mcp.server._run_fusion", return_value="Vault added.")
    def test_all_args(self, mock_run):
        vault_add(address="0xABC", label="My Vault", chain_id=42161)
        mock_run.assert_called_once_with(
            "vault", "add", "0xABC", "--label", "My Vault", "--chain-id", "42161"
        )


class TestVaultRemove:
    @patch("ipor_fusion.mcp.server._run_fusion", return_value="Removed.")
    def test_calls_remove(self, mock_run):
        vault_remove(address="0xABC")
        mock_run.assert_called_once_with("vault", "remove", "0xABC")


class TestConfigShow:
    @patch("ipor_fusion.mcp.server._run_fusion", return_value="config")
    def test_calls_show(self, mock_run):
        config_show()
        mock_run.assert_called_once_with("config", "show")


class TestConfigSetProvider:
    @patch("ipor_fusion.mcp.server._run_fusion", return_value="Provider set.")
    def test_url_only(self, mock_run):
        config_set_provider(url="https://rpc.example.com")
        mock_run.assert_called_once_with(
            "config", "set-provider", "https://rpc.example.com"
        )

    @patch("ipor_fusion.mcp.server._run_fusion", return_value="Provider set.")
    def test_with_chain_id(self, mock_run):
        config_set_provider(url="https://rpc.example.com", chain_id=1)
        mock_run.assert_called_once_with(
            "config", "set-provider", "https://rpc.example.com", "--chain-id", "1"
        )


class TestConfigSetEtherscanKey:
    @patch("ipor_fusion.mcp.server._run_fusion", return_value="Key set.")
    def test_calls_set_key(self, mock_run):
        config_set_etherscan_key(api_key="ABC123")
        mock_run.assert_called_once_with("config", "set-etherscan-key", "ABC123")


class TestConfigSetDefaultVault:
    @patch("ipor_fusion.mcp.server._run_fusion", return_value="Default set.")
    def test_calls_set_default(self, mock_run):
        config_set_default_vault(address="0xDEF")
        mock_run.assert_called_once_with("config", "set-default-vault", "0xDEF")

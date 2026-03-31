"""Tests for the MCP server tool definitions."""

from subprocess import CompletedProcess
from unittest.mock import patch

from ipor_fusion.mcp.server import (
    _run_fusion,
    vault_info,
    vault_list,
    config_show,
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


class TestVaultList:
    @patch("ipor_fusion.mcp.server._run_fusion", return_value="[]")
    def test_calls_list(self, mock_run):
        vault_list()
        mock_run.assert_called_once_with("vault", "list", "--json")


class TestConfigShow:
    @patch("ipor_fusion.mcp.server._run_fusion", return_value="config")
    def test_calls_show(self, mock_run):
        config_show()
        mock_run.assert_called_once_with("config", "show")

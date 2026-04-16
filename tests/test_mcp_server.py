# pylint: disable=unused-argument,import-outside-toplevel
"""Tests for the MCP server tool definitions (direct SDK import)."""

from unittest.mock import MagicMock, patch

from ipor_fusion.mcp.server import (
    config_set_etherscan_key,
    config_set_provider,
    config_show,
    vault_add,
    vault_list,
    vault_remove,
)


def _empty_config():
    from ipor_fusion.cli.config_store import FusionConfig

    return FusionConfig()


def _config_with_provider():
    from ipor_fusion.cli.config_store import FusionConfig

    return FusionConfig(providers={"1": "https://rpc.example.com"})


def _config_with_vault():
    from ipor_fusion.cli.config_store import FusionConfig, VaultEntry

    return FusionConfig(
        providers={"1": "https://rpc.example.com"},
        vaults=[VaultEntry(address="0xABC", label="Test", chain_id=1)],
    )


class TestConfigShow:
    @patch("ipor_fusion.mcp.server.load_config", return_value=_empty_config())
    def test_empty_config(self, _):
        result = config_show()
        assert result.providers == {}
        assert result.vaults == []
        assert result.etherscan_api_key is None

    @patch("ipor_fusion.mcp.server.load_config", return_value=_config_with_vault())
    def test_config_with_vault(self, _):
        result = config_show()
        assert len(result.vaults) == 1
        assert result.vaults[0].address == "0xABC"


class TestConfigSetProvider:
    @patch("ipor_fusion.mcp.server.save_config")
    @patch("ipor_fusion.mcp.server.load_config", return_value=_empty_config())
    def test_with_chain_id(self, mock_load, mock_save):
        result = config_set_provider(url="https://rpc.example.com", chain_id=1)
        assert "chain 1" in result.message.lower()
        saved_cfg = mock_save.call_args[0][0]
        assert saved_cfg.providers["1"] == "https://rpc.example.com"

    @patch("ipor_fusion.mcp.server.Web3")
    @patch("ipor_fusion.mcp.server.save_config")
    @patch("ipor_fusion.mcp.server.load_config", return_value=_empty_config())
    def test_auto_detect_chain_id(self, mock_load, mock_save, mock_web3):
        mock_instance = MagicMock()
        mock_instance.eth.chain_id = 42161
        mock_web3.return_value = mock_instance
        mock_web3.HTTPProvider = MagicMock()

        result = config_set_provider(url="https://rpc.example.com")
        assert "42161" in result.message
        saved_cfg = mock_save.call_args[0][0]
        assert saved_cfg.providers["42161"] == "https://rpc.example.com"


class TestConfigSetEtherscanKey:
    @patch("ipor_fusion.mcp.server.save_config")
    @patch("ipor_fusion.mcp.server.load_config", return_value=_empty_config())
    def test_sets_key(self, mock_load, mock_save):
        result = config_set_etherscan_key(api_key="ABC123")
        assert "set" in result.message.lower()
        saved_cfg = mock_save.call_args[0][0]
        assert saved_cfg.etherscan_api_key == "ABC123"


class TestVaultList:
    @patch("ipor_fusion.mcp.server.load_config", return_value=_empty_config())
    def test_empty(self, _):
        result = vault_list()
        assert result == []

    @patch("ipor_fusion.mcp.server.load_config", return_value=_config_with_vault())
    def test_with_vault(self, _):
        result = vault_list()
        assert len(result) == 1
        assert result[0].address == "0xABC"
        assert result[0].label == "Test"
        assert result[0].chain_id == 1


class TestVaultAdd:
    @patch("ipor_fusion.mcp.server.save_config")
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_provider(),
    )
    def test_add_with_label_and_chain(self, mock_load, mock_save):
        result = vault_add(address="0xDEF", label="New Vault", chain_id=1)
        assert "added" in result.message.lower()
        saved_cfg = mock_save.call_args[0][0]
        assert any(v.address == "0xDEF" for v in saved_cfg.vaults)

    @patch("ipor_fusion.mcp.server.save_config")
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_vault(),
    )
    def test_update_existing(self, mock_load, mock_save):
        result = vault_add(address="0xABC", label="Updated", chain_id=1)
        assert "updated" in result.message.lower()
        saved_cfg = mock_save.call_args[0][0]
        assert len(saved_cfg.vaults) == 1
        assert saved_cfg.vaults[0].label == "Updated"


class TestVaultRemove:
    @patch("ipor_fusion.mcp.server.save_config")
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_vault(),
    )
    def test_remove_existing(self, mock_load, mock_save):
        result = vault_remove(address="0xABC")
        assert "removed" in result.message.lower()
        saved_cfg = mock_save.call_args[0][0]
        assert len(saved_cfg.vaults) == 0

    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_vault(),
    )
    def test_remove_missing(self, _):
        result = vault_remove(address="0xNONEXISTENT")
        assert "not found" in result.message.lower()

# pylint: disable=unused-argument
import json
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner

from ipor_fusion.cli import config_store
from ipor_fusion.cli.config_store import FusionConfig, VaultEntry, save_config
from ipor_fusion.cli.main import cli, main
from ipor_fusion.core.withdraw_manager import AccountRequest

ADDR_1 = "0x1111111111111111111111111111111111111111"
ADDR_2 = "0x2222222222222222222222222222222222222222"
ADDR_ORACLE = "0x4444444444444444444444444444444444444444"
ADDR_ACCESS = "0x5555555555555555555555555555555555555555"
ADDR_REWARDS = "0x6666666666666666666666666666666666666666"
ADDR_WITHDRAW = "0x7777777777777777777777777777777777777777"
ADDR_FUSE_1 = "0x8888888888888888888888888888888888888888"
ADDR_FUSE_2 = "0x9999999999999999999999999999999999999999"
ADDR_USER_1 = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    config_dir = tmp_path / ".fusion"
    cache_dir = tmp_path / ".cache"
    config_file = config_dir / "config.json"
    cache_file = cache_dir / "contract_cache.json"
    deployment_cache_file = cache_dir / "deployment_cache.json"
    monkeypatch.setattr(config_store, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_store, "CONFIG_FILE", config_file)
    monkeypatch.setattr(config_store, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(config_store, "CACHE_FILE", cache_file)
    monkeypatch.setattr(config_store, "DEPLOYMENT_CACHE_FILE", deployment_cache_file)
    return config_dir, config_file, cache_file


class TestConfigShow:
    def test_empty_config(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "(none)" in result.output
        assert "(not set)" in result.output

    def test_config_with_data(self, tmp_config):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            etherscan_api_key="secret123",
            vaults=[VaultEntry(address=ADDR_1, label="MyVault", chain_id=1)],
        )
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0
        assert "Chain 1" in result.output
        assert "https://rpc.example.com" in result.output
        assert ADDR_1 in result.output
        assert "MyVault" in result.output
        assert "***" in result.output


class TestConfigSetProvider:
    def test_with_explicit_chain_id(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["config", "set-provider", "https://rpc.example.com", "--chain-id", "1"],
        )
        assert result.exit_code == 0
        assert "Provider for chain 1 set" in result.output

        cfg_data = json.loads(tmp_config[1].read_text(encoding="utf-8"))
        assert cfg_data["providers"]["1"] == "https://rpc.example.com"

    @patch("web3.Web3")
    def test_auto_detect_chain_id(self, mock_web3_cls, tmp_config):
        mock_instance = MagicMock()
        mock_instance.eth.chain_id = 42161
        mock_web3_cls.return_value = mock_instance
        mock_web3_cls.HTTPProvider.return_value = "mock_provider"

        runner = CliRunner()
        result = runner.invoke(
            cli, ["config", "set-provider", "https://arb-rpc.example.com"]
        )
        assert result.exit_code == 0
        assert "Detected chain ID: 42161" in result.output
        assert "Provider for chain 42161 set" in result.output


class TestConfigSetEtherscanKey:
    def test_saves_key(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "set-etherscan-key", "my-api-key-123"])
        assert result.exit_code == 0
        assert "Etherscan API key set" in result.output

        cfg_data = json.loads(tmp_config[1].read_text(encoding="utf-8"))
        assert cfg_data["etherscan_api_key"] == "my-api-key-123"


class TestVaultList:
    def test_empty(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "list"])
        assert result.exit_code == 0
        assert "(no saved vaults)" in result.output

    def test_with_vaults(self, tmp_config):
        cfg = FusionConfig(
            vaults=[
                VaultEntry(address=ADDR_1, label="Vault A", chain_id=1),
                VaultEntry(address=ADDR_2, label="Vault B", chain_id=42161),
            ]
        )
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "list"])
        assert result.exit_code == 0
        assert "Vault A" in result.output
        assert "Vault B" in result.output
        assert ADDR_1 in result.output
        assert ADDR_2 in result.output
        assert "ethereum" in result.output
        assert "arbitrum" in result.output

    def test_ls_alias(self, tmp_config):
        cfg = FusionConfig(
            vaults=[VaultEntry(address=ADDR_1, label="AliasVault", chain_id=1)]
        )
        save_config(cfg)

        runner = CliRunner()
        result_ls = runner.invoke(cli, ["vault", "ls"])
        result_list = runner.invoke(cli, ["vault", "list"])
        assert result_ls.exit_code == 0
        assert result_ls.output == result_list.output


class TestVaultAdd:
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_add_vault_fetches_name(self, mock_ctx_cls, mock_pv_cls, tmp_config):
        cfg = FusionConfig(providers={"1": "https://rpc.example.com"})
        save_config(cfg)

        mock_ctx_cls.from_url.return_value = MagicMock()
        mock_pv_cls.return_value.name.return_value = "On-Chain Vault"

        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "add", ADDR_1, "--chain-id", "1"])
        assert result.exit_code == 0
        assert "On-Chain Vault" in result.output

        cfg_data = json.loads(tmp_config[1].read_text(encoding="utf-8"))
        assert len(cfg_data["vaults"]) == 1
        assert cfg_data["vaults"][0]["label"] == "On-Chain Vault"
        assert cfg_data["vaults"][0]["address"] == ADDR_1

    def test_add_vault_with_label(self, tmp_config):
        cfg = FusionConfig(providers={"1": "https://rpc.example.com"})
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["vault", "add", ADDR_1, "--chain-id", "1", "--label", "MyLabel"]
        )
        assert result.exit_code == 0
        assert "MyLabel" in result.output

        cfg_data = json.loads(tmp_config[1].read_text(encoding="utf-8"))
        assert cfg_data["vaults"][0]["label"] == "MyLabel"

    def test_add_duplicate_updates(self, tmp_config):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            vaults=[VaultEntry(address=ADDR_1, label="OldLabel", chain_id=1)],
        )
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(
            cli, ["vault", "add", ADDR_1, "--chain-id", "42161", "--label", "NewLabel"]
        )
        assert result.exit_code == 0
        assert "already exists" in result.output

        cfg_data = json.loads(tmp_config[1].read_text(encoding="utf-8"))
        assert len(cfg_data["vaults"]) == 1
        assert cfg_data["vaults"][0]["label"] == "NewLabel"
        assert cfg_data["vaults"][0]["chain_id"] == 42161

    def test_add_auto_detects_chain_id(self, tmp_config):
        cfg = FusionConfig(providers={"1": "https://rpc.example.com"})
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "add", ADDR_1, "--label", "AutoChain"])
        assert result.exit_code == 0
        assert "AutoChain" in result.output

        cfg_data = json.loads(tmp_config[1].read_text(encoding="utf-8"))
        assert cfg_data["vaults"][0]["chain_id"] == 1

    def test_add_fails_without_chain_id_multiple_providers(self, tmp_config):
        cfg = FusionConfig(
            providers={
                "1": "https://eth.example.com",
                "42161": "https://arb.example.com",
            }
        )
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "add", ADDR_1, "--label", "X"])
        assert result.exit_code != 0
        assert "Cannot auto-detect" in result.output


class TestVaultRemove:
    def test_remove_existing(self, tmp_config):
        cfg = FusionConfig(
            vaults=[VaultEntry(address=ADDR_1, label="ToRemove", chain_id=1)]
        )
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "remove", ADDR_1])
        assert result.exit_code == 0
        assert "Vault removed" in result.output

        cfg_data = json.loads(tmp_config[1].read_text(encoding="utf-8"))
        assert len(cfg_data["vaults"]) == 0

    def test_remove_not_found(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "remove", ADDR_1])
        assert result.exit_code == 0
        assert "Vault not found" in result.output


class TestVaultInfo:
    @patch("ipor_fusion.cli.vault_fetcher.WithdrawManager")
    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="SomeFuse")
    @patch("ipor_fusion.cli.vault_fetcher.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_fetcher.ERC20")
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_vault_info_output(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        mock_erc20_cls,
        mock_oracle_cls,
        mock_get_name,
        mock_wm_cls,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 12345678
        mock_ctx.web3.eth.get_block.return_value = {"timestamp": 1700000000}
        mock_ctx_cls.from_url.return_value = mock_ctx

        @dataclass
        class FakeBalanceFuse:
            market_id: int
            fuse: str

        balance_fuse_1 = FakeBalanceFuse(market_id=1, fuse=ADDR_FUSE_1)

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.decimals.return_value = 18
        mock_pv.total_assets.return_value = 1000 * 10**18
        mock_pv.total_supply.return_value = 900 * 10**18
        mock_pv.get_total_supply_cap.return_value = 5000 * 10**18
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_access_manager_address.return_value = ADDR_ACCESS
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv.get_fuses.return_value = [ADDR_FUSE_1, ADDR_FUSE_2]
        mock_pv.get_balance_fuses.return_value = [balance_fuse_1]
        mock_pv.get_rewards_claim_manager_address.return_value = ADDR_REWARDS
        mock_pv.withdraw_manager_address.return_value = ADDR_WITHDRAW
        mock_pv.get_instant_withdrawal_fuses.return_value = []
        mock_pv.name.return_value = "Test Vault"
        mock_pv.get_market_substrates.return_value = []
        mock_pv.total_assets_in_market.return_value = 500 * 10**18
        mock_pv.convert_to_assets.return_value = 111 * 10**6
        mock_pv_cls.return_value = mock_pv

        mock_erc20 = MagicMock()
        mock_erc20.symbol.return_value = "USDC"
        mock_erc20.decimals.return_value = 6
        mock_erc20_cls.return_value = mock_erc20

        mock_price = MagicMock()
        mock_price.readable.return_value = 1.0
        mock_oracle = MagicMock()
        mock_oracle.get_asset_price.return_value = mock_price
        mock_oracle_cls.return_value = mock_oracle

        mock_wm = MagicMock()
        mock_wm.get_withdraw_window.return_value = 86400
        mock_wm.get_request_fee.return_value = 0
        mock_wm.get_withdraw_fee.return_value = 0
        mock_wm.get_shares_to_release.return_value = 50 * 10**18
        mock_wm.get_last_release_funds_timestamp.return_value = 1699999000
        mock_wm.get_pending_requests.return_value = [
            AccountRequest(
                account=ADDR_USER_1,
                shares=100 * 10**18,
                end_withdraw_window_timestamp=1700100000,
                can_withdraw=False,
            )
        ]
        mock_wm_cls.return_value = mock_wm

        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "info", ADDR_1, "--chain-id", "1"])
        assert result.exit_code == 0, result.output
        assert ADDR_1 in result.output
        assert "ethereum" in result.output
        assert "USDC" in result.output
        assert ADDR_ACCESS in result.output
        assert ADDR_ORACLE in result.output
        assert ADDR_REWARDS in result.output
        assert ADDR_WITHDRAW in result.output
        assert "Fuses (2)" in result.output
        assert "Balance Fuses (1)" in result.output
        assert "SomeFuse" in result.output
        assert "Window:" in result.output
        assert "Pending requests (1):" in result.output
        assert ADDR_USER_1 in result.output
        assert "waiting" in result.output

    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="SomeFuse")
    @patch("ipor_fusion.cli.vault_fetcher.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_fetcher.ERC20")
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_vault_info_with_block_number(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        mock_erc20_cls,
        mock_oracle_cls,
        mock_get_name,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 12345678
        mock_ctx.web3.eth.get_block.return_value = {"timestamp": 1700000000}
        mock_ctx_cls.from_url.return_value = mock_ctx

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.decimals.return_value = 18
        mock_pv.total_assets.return_value = 0
        mock_pv.total_supply.return_value = 0
        mock_pv.get_total_supply_cap.return_value = 0
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_access_manager_address.return_value = ADDR_ACCESS
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv.get_fuses.return_value = []
        mock_pv.get_balance_fuses.return_value = []
        mock_pv.get_rewards_claim_manager_address.return_value = None
        mock_pv.withdraw_manager_address.return_value = None
        mock_pv.get_instant_withdrawal_fuses.return_value = []
        mock_pv.get_market_substrates.return_value = []
        mock_pv_cls.return_value = mock_pv

        mock_erc20 = MagicMock()
        mock_erc20.symbol.return_value = "USDC"
        mock_erc20.decimals.return_value = 6
        mock_erc20_cls.return_value = mock_erc20

        mock_oracle = MagicMock()
        mock_oracle.get_asset_price.return_value = None
        mock_oracle_cls.return_value = mock_oracle

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "vault",
                "info",
                ADDR_1,
                "--chain-id",
                "1",
                "--block-number",
                "99999",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "99999" in result.output
        assert mock_ctx.default_block == 99999

    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="SomeFuse")
    @patch("ipor_fusion.cli.vault_fetcher.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_fetcher.ERC20")
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_vault_info_unlimited_supply_cap(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        mock_erc20_cls,
        mock_oracle_cls,
        mock_get_name,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 100
        mock_ctx.web3.eth.get_block.return_value = {"timestamp": 1700000000}
        mock_ctx_cls.from_url.return_value = mock_ctx

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.decimals.return_value = 18
        mock_pv.total_assets.return_value = 0
        mock_pv.total_supply.return_value = 0
        mock_pv.get_total_supply_cap.return_value = 2**256 - 1
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_access_manager_address.return_value = ADDR_ACCESS
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv.get_fuses.return_value = []
        mock_pv.get_balance_fuses.return_value = []
        mock_pv.get_rewards_claim_manager_address.return_value = None
        mock_pv.withdraw_manager_address.return_value = None
        mock_pv.get_instant_withdrawal_fuses.return_value = []
        mock_pv.get_market_substrates.return_value = []
        mock_pv_cls.return_value = mock_pv

        mock_erc20 = MagicMock()
        mock_erc20.symbol.return_value = "USDC"
        mock_erc20.decimals.return_value = 6
        mock_erc20_cls.return_value = mock_erc20

        mock_oracle = MagicMock()
        mock_oracle.get_asset_price.return_value = None
        mock_oracle_cls.return_value = mock_oracle

        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "info", ADDR_1, "--chain-id", "1"])
        assert result.exit_code == 0, result.output
        assert "Supply Cap:       unlimited" in result.output

    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="SomeFuse")
    @patch("ipor_fusion.cli.vault_fetcher.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_fetcher.ERC20")
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_vault_info_shows_links(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        mock_erc20_cls,
        mock_oracle_cls,
        mock_get_name,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 100
        mock_ctx.web3.eth.get_block.return_value = {"timestamp": 1700000000}
        mock_ctx_cls.from_url.return_value = mock_ctx

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.decimals.return_value = 18
        mock_pv.total_assets.return_value = 0
        mock_pv.total_supply.return_value = 0
        mock_pv.get_total_supply_cap.return_value = 0
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_access_manager_address.return_value = ADDR_ACCESS
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv.get_fuses.return_value = []
        mock_pv.get_balance_fuses.return_value = []
        mock_pv.get_rewards_claim_manager_address.return_value = None
        mock_pv.withdraw_manager_address.return_value = None
        mock_pv.get_instant_withdrawal_fuses.return_value = []
        mock_pv.get_market_substrates.return_value = []
        mock_pv_cls.return_value = mock_pv

        mock_erc20 = MagicMock()
        mock_erc20.symbol.return_value = "USDC"
        mock_erc20.decimals.return_value = 6
        mock_erc20_cls.return_value = mock_erc20

        mock_oracle = MagicMock()
        mock_oracle.get_asset_price.return_value = None
        mock_oracle_cls.return_value = mock_oracle

        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "info", ADDR_1, "--chain-id", "1"])
        assert result.exit_code == 0, result.output
        assert (
            f"Etherscan:        https://etherscan.io/address/{ADDR_1}" in result.output
        )
        assert (
            f"IPOR app:         https://app.ipor.io/fusion/ethereum/{ADDR_1}"
            in result.output
        )

    @patch("ipor_fusion.cli.vault_fetcher.get_deployment_tx", return_value="0xdeadbeef")
    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="SomeFuse")
    @patch("ipor_fusion.cli.vault_fetcher.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_fetcher.ERC20")
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_vault_info_deployment_info(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        mock_erc20_cls,
        mock_oracle_cls,
        mock_get_name,
        mock_get_deploy_tx,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            etherscan_api_key="test-key",
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 100
        mock_ctx.web3.eth.get_block.return_value = {"timestamp": 1700000000}
        mock_ctx.web3.eth.get_transaction.return_value = {"blockNumber": 18500000}
        mock_ctx_cls.from_url.return_value = mock_ctx

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.decimals.return_value = 18
        mock_pv.total_assets.return_value = 0
        mock_pv.total_supply.return_value = 0
        mock_pv.get_total_supply_cap.return_value = 0
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_access_manager_address.return_value = ADDR_ACCESS
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv.get_fuses.return_value = []
        mock_pv.get_balance_fuses.return_value = []
        mock_pv.get_rewards_claim_manager_address.return_value = None
        mock_pv.withdraw_manager_address.return_value = None
        mock_pv.get_instant_withdrawal_fuses.return_value = []
        mock_pv.get_market_substrates.return_value = []
        mock_pv_cls.return_value = mock_pv

        mock_erc20 = MagicMock()
        mock_erc20.symbol.return_value = "USDC"
        mock_erc20.decimals.return_value = 6
        mock_erc20_cls.return_value = mock_erc20

        mock_oracle = MagicMock()
        mock_oracle.get_asset_price.return_value = None
        mock_oracle_cls.return_value = mock_oracle

        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "info", ADDR_1, "--chain-id", "1"])
        assert result.exit_code == 0, result.output
        assert "Deployed at:      block 18500000" in result.output
        assert "2023-11-14" in result.output

    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="SomeFuse")
    @patch("ipor_fusion.cli.vault_fetcher.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_fetcher.ERC20")
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_vault_info_deployment_na_without_api_key(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        mock_erc20_cls,
        mock_oracle_cls,
        mock_get_name,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 100
        mock_ctx.web3.eth.get_block.return_value = {"timestamp": 1700000000}
        mock_ctx_cls.from_url.return_value = mock_ctx

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.decimals.return_value = 18
        mock_pv.total_assets.return_value = 0
        mock_pv.total_supply.return_value = 0
        mock_pv.get_total_supply_cap.return_value = 0
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_access_manager_address.return_value = ADDR_ACCESS
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv.get_fuses.return_value = []
        mock_pv.get_balance_fuses.return_value = []
        mock_pv.get_rewards_claim_manager_address.return_value = None
        mock_pv.withdraw_manager_address.return_value = None
        mock_pv.get_instant_withdrawal_fuses.return_value = []
        mock_pv.get_market_substrates.return_value = []
        mock_pv_cls.return_value = mock_pv

        mock_erc20 = MagicMock()
        mock_erc20.symbol.return_value = "USDC"
        mock_erc20.decimals.return_value = 6
        mock_erc20_cls.return_value = mock_erc20

        mock_oracle = MagicMock()
        mock_oracle.get_asset_price.return_value = None
        mock_oracle_cls.return_value = mock_oracle

        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "info", ADDR_1, "--chain-id", "1"])
        assert result.exit_code == 0, result.output
        assert "Deployed at:      N/A" in result.output


class TestVaultListJson:
    def test_empty_json(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "list", "--json"])
        assert result.exit_code == 0
        assert json.loads(result.output) == []

    def test_json_with_vaults(self, tmp_config):
        cfg = FusionConfig(
            vaults=[
                VaultEntry(address=ADDR_1, label="Vault A", chain_id=1),
                VaultEntry(address=ADDR_2, label="Vault B", chain_id=42161),
            ],
        )
        save_config(cfg)

        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "list", "--json"])
        assert result.exit_code == 0
        data = json.loads(result.output)
        assert len(data) == 2
        assert data[0]["address"] == ADDR_1
        assert data[0]["label"] == "Vault A"
        assert data[0]["chain"] == "ethereum"
        assert data[1]["address"] == ADDR_2


class TestVaultInfoJson:
    @patch("ipor_fusion.cli.vault_fetcher.WithdrawManager")
    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="SomeFuse")
    @patch("ipor_fusion.cli.vault_fetcher.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_fetcher.ERC20")
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_json_output(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        mock_erc20_cls,
        mock_oracle_cls,
        mock_get_name,
        mock_wm_cls,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 12345678
        mock_ctx.web3.eth.get_block.return_value = {"timestamp": 1700000000}
        mock_ctx_cls.from_url.return_value = mock_ctx

        @dataclass
        class FakeBalanceFuse:
            market_id: int
            fuse: str

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.decimals.return_value = 18
        mock_pv.total_assets.return_value = 1000 * 10**18
        mock_pv.total_supply.return_value = 900 * 10**18
        mock_pv.get_total_supply_cap.return_value = 5000 * 10**18
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_access_manager_address.return_value = ADDR_ACCESS
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv.get_fuses.return_value = [ADDR_FUSE_1]
        mock_pv.get_balance_fuses.return_value = [
            FakeBalanceFuse(market_id=14, fuse=ADDR_FUSE_1)
        ]
        mock_pv.get_rewards_claim_manager_address.return_value = ADDR_REWARDS
        mock_pv.withdraw_manager_address.return_value = ADDR_WITHDRAW
        mock_pv.get_instant_withdrawal_fuses.return_value = []
        morpho_sub = bytes.fromhex(
            "32e253d33f1594a67fc6ef51bf7a39cc4bf2d14904998dee769706fcde489ed9"
        )
        mock_pv.get_market_substrates.return_value = [morpho_sub]
        mock_pv.total_assets_in_market.return_value = 500 * 10**18
        mock_pv.convert_to_assets.return_value = 111 * 10**6
        mock_pv_cls.return_value = mock_pv

        mock_erc20 = MagicMock()
        mock_erc20.symbol.return_value = "USDC"
        mock_erc20.decimals.return_value = 6
        mock_erc20_cls.return_value = mock_erc20

        mock_price = MagicMock()
        mock_price.readable.return_value = 1.0
        mock_oracle = MagicMock()
        mock_oracle.get_asset_price.return_value = mock_price
        mock_oracle_cls.return_value = mock_oracle

        mock_wm = MagicMock()
        mock_wm.get_withdraw_window.return_value = 86400
        mock_wm.get_request_fee.return_value = 10**15
        mock_wm.get_withdraw_fee.return_value = 2 * 10**15
        mock_wm.get_shares_to_release.return_value = 50 * 10**18
        mock_wm.get_last_release_funds_timestamp.return_value = 1699999000
        mock_wm.get_pending_requests.return_value = [
            AccountRequest(
                account=ADDR_USER_1,
                shares=100 * 10**18,
                end_withdraw_window_timestamp=1700100000,
                can_withdraw=True,
            ),
        ]
        mock_wm_cls.return_value = mock_wm

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["vault", "info", ADDR_1, "--chain-id", "1", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["vault"] == ADDR_1
        assert data["chain"] == "ethereum"
        assert data["chain_id"] == 1
        assert data["asset"]["symbol"] == "USDC"
        assert data["asset"]["price_usd"] == 1.0
        assert data["total_assets"]["raw"] == 1000 * 10**18
        assert data["managers"]["access"] == ADDR_ACCESS
        assert data["managers"]["rewards"] == ADDR_REWARDS
        assert len(data["fuses"]) == 1
        assert data["fuses"][0]["contract"] == "SomeFuse"
        assert len(data["balance_fuses"]) == 1
        assert data["links"]["etherscan"] == f"https://etherscan.io/address/{ADDR_1}"
        assert (
            data["links"]["ipor_app"] == f"https://app.ipor.io/fusion/ethereum/{ADDR_1}"
        )
        assert data["deployment"] is None
        subs = data["substrates"]["MORPHO (14)"]
        assert len(subs) == 1
        assert "raw" in subs[0]
        assert subs[0].get("error") is None
        assert subs[0].get("substrate_type") == "morpho_market_id"
        wmd = data["withdraw_manager_details"]
        assert wmd is not None
        assert wmd["withdraw_window_seconds"] == 86400
        assert wmd["total_pending_shares"]["raw"] == 100 * 10**18
        assert wmd["shares_to_release"]["raw"] == 50 * 10**18
        assert wmd["last_release_funds_timestamp"] == 1699999000
        assert wmd["request_fee_percent"] == 0.1
        assert wmd["withdraw_fee_percent"] == 0.2
        assert len(wmd["pending_requests"]) == 1
        assert wmd["pending_requests"][0]["account"] == ADDR_USER_1
        assert wmd["pending_requests"][0]["can_withdraw"] is True

    @patch("ipor_fusion.cli.vault_fetcher.get_deployment_tx", return_value="0xdeadbeef")
    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="SomeFuse")
    @patch("ipor_fusion.cli.vault_fetcher.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_fetcher.ERC20")
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_json_deployment_info(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        mock_erc20_cls,
        mock_oracle_cls,
        mock_get_name,
        mock_get_deploy_tx,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            etherscan_api_key="test-key",
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 100
        mock_ctx.web3.eth.get_block.return_value = {"timestamp": 1700000000}
        mock_ctx.web3.eth.get_transaction.return_value = {"blockNumber": 18500000}
        mock_ctx_cls.from_url.return_value = mock_ctx

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.decimals.return_value = 18
        mock_pv.total_assets.return_value = 0
        mock_pv.total_supply.return_value = 0
        mock_pv.get_total_supply_cap.return_value = 0
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_access_manager_address.return_value = ADDR_ACCESS
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv.get_fuses.return_value = []
        mock_pv.get_balance_fuses.return_value = []
        mock_pv.get_rewards_claim_manager_address.return_value = None
        mock_pv.withdraw_manager_address.return_value = None
        mock_pv.get_instant_withdrawal_fuses.return_value = []
        mock_pv.get_market_substrates.return_value = []
        mock_pv_cls.return_value = mock_pv

        mock_erc20 = MagicMock()
        mock_erc20.symbol.return_value = "USDC"
        mock_erc20.decimals.return_value = 6
        mock_erc20_cls.return_value = mock_erc20

        mock_oracle = MagicMock()
        mock_oracle.get_asset_price.return_value = None
        mock_oracle_cls.return_value = mock_oracle

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["vault", "info", ADDR_1, "--chain-id", "1", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["deployment"]["block"] == 18500000
        assert data["deployment"]["timestamp"] == 1700000000
        assert data["deployment"]["timestamp_utc"] == "2023-11-14T22:13:20Z"
        assert isinstance(data["deployment"]["age_days"], int)

    @patch("ipor_fusion.cli.vault_health._resolve_token_symbol", return_value="WETH")
    @patch("ipor_fusion.cli.vault_health.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_health.ERC20")
    @patch("ipor_fusion.cli.vault_cmd._resolve_token_symbol", return_value="WETH")
    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="SomeFuse")
    @patch("ipor_fusion.cli.vault_fetcher.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_fetcher.ERC20")
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_json_with_substrates(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        mock_erc20_cls,
        mock_oracle_cls,
        mock_get_name,
        mock_resolve_sym,
        mock_health_erc20_cls,
        mock_health_oracle_cls,
        mock_health_resolve_sym,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 100
        mock_ctx.web3.eth.get_block.return_value = {"timestamp": 1700000000}
        mock_ctx_cls.from_url.return_value = mock_ctx

        @dataclass
        class FakeBalanceFuse:
            market_id: int
            fuse: str

        addr_bytes = bytes.fromhex("00" * 12 + "ab" * 20)

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.decimals.return_value = 18
        mock_pv.total_assets.return_value = 0
        mock_pv.total_supply.return_value = 0
        mock_pv.get_total_supply_cap.return_value = 0
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_access_manager_address.return_value = ADDR_ACCESS
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv.get_fuses.return_value = []
        mock_pv.get_balance_fuses.return_value = [
            FakeBalanceFuse(market_id=7, fuse=ADDR_FUSE_1)
        ]
        mock_pv.get_rewards_claim_manager_address.return_value = None
        mock_pv.withdraw_manager_address.return_value = None
        mock_pv.get_instant_withdrawal_fuses.return_value = []
        mock_pv.get_market_substrates.return_value = [addr_bytes]
        mock_pv.total_assets_in_market.return_value = 0
        mock_pv_cls.return_value = mock_pv

        mock_erc20 = MagicMock()
        mock_erc20.symbol.return_value = "X"
        mock_erc20.decimals.return_value = 18
        mock_erc20.balance_of.return_value = 0
        mock_erc20_cls.return_value = mock_erc20
        mock_health_erc20_cls.return_value = mock_erc20

        mock_oracle_cls.return_value.get_asset_price.return_value = None
        mock_health_oracle_cls.return_value.get_asset_price.return_value = None

        runner = CliRunner()
        result = runner.invoke(
            cli,
            ["vault", "info", ADDR_1, "--chain-id", "1", "--json"],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert "substrates" in data
        assert "ERC20_VAULT_BALANCE (7)" in data["substrates"]
        subs = data["substrates"]["ERC20_VAULT_BALANCE (7)"]
        assert len(subs) == 1
        assert subs[0]["symbol"] == "WETH"
        assert subs[0]["contract"] == "SomeFuse"


class TestMainExceptionHandler:
    def test_keyboard_interrupt(self, tmp_config):
        with patch("ipor_fusion.cli.main.cli", side_effect=KeyboardInterrupt):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 130

    def test_connection_error(self, tmp_config, capsys):
        with patch("ipor_fusion.cli.main.cli", side_effect=ConnectionError("timeout")):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Connection failed" in captured.out

    def test_value_error_with_address(self, tmp_config, capsys):
        with patch(
            "ipor_fusion.cli.main.cli",
            side_effect=ValueError("invalid address checksum"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Invalid address" in captured.out

    def test_generic_exception(self, tmp_config, capsys):
        with patch(
            "ipor_fusion.cli.main.cli",
            side_effect=RuntimeError("something broke"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error:" in captured.out
        assert "something broke" in captured.out

    def test_usage_error_re_raised(self, tmp_config):
        with patch(
            "ipor_fusion.cli.main.cli", side_effect=click.UsageError("bad usage")
        ):
            with pytest.raises(click.UsageError):
                main()

    def test_value_error_without_address(self, tmp_config, capsys):
        with patch(
            "ipor_fusion.cli.main.cli",
            side_effect=ValueError("invalid number format"),
        ):
            with pytest.raises(SystemExit) as exc_info:
                main()
            assert exc_info.value.code == 1
        captured = capsys.readouterr()
        assert "Error:" in captured.out
        assert "invalid number format" in captured.out


class TestGlobalFlags:
    def test_verbose_flag(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["-v", "config", "show"])
        assert result.exit_code == 0

    def test_quiet_flag(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["-q", "config", "show"])
        assert result.exit_code == 0

    def test_verbose_and_quiet_exclusive(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["-v", "-q", "config", "show"])
        assert result.exit_code == 0

    def test_no_color_flag(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["--no-color", "config", "show"])
        assert result.exit_code == 0

    def test_no_color_env_var(self, tmp_config, monkeypatch):
        monkeypatch.setenv("NO_COLOR", "1")
        runner = CliRunner()
        result = runner.invoke(cli, ["config", "show"])
        assert result.exit_code == 0


class TestInstallCompletion:
    def test_bash_explicit(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["install-completion", "bash"])
        assert result.exit_code == 0
        assert "_FUSION_COMPLETE=bash_source" in result.output

    def test_zsh_explicit(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["install-completion", "zsh"])
        assert result.exit_code == 0
        assert "_FUSION_COMPLETE=zsh_source" in result.output

    def test_fish_explicit(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["install-completion", "fish"])
        assert result.exit_code == 0
        assert "_FUSION_COMPLETE=fish_source" in result.output
        assert "| source" in result.output

    def test_auto_detect_zsh(self, tmp_config, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/zsh")
        runner = CliRunner()
        result = runner.invoke(cli, ["install-completion"])
        assert result.exit_code == 0
        assert "zsh_source" in result.output

    def test_auto_detect_bash_default(self, tmp_config, monkeypatch):
        monkeypatch.setenv("SHELL", "/bin/sh")
        runner = CliRunner()
        result = runner.invoke(cli, ["install-completion"])
        assert result.exit_code == 0
        assert "bash_source" in result.output


class TestMarketDetail:
    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="AaveV3Fuse")
    @patch("ipor_fusion.cli.vault_cmd.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_cmd.ERC20")
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_market_detail_json(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        mock_erc20_cls,
        mock_oracle_cls,
        mock_get_name,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 12345678
        mock_ctx_cls.from_url.return_value = mock_ctx

        @dataclass
        class FakeBalanceFuse:
            market_id: int
            fuse: str

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.get_balance_fuses.return_value = [
            FakeBalanceFuse(market_id=1, fuse=ADDR_FUSE_1),
            FakeBalanceFuse(market_id=14, fuse=ADDR_FUSE_2),
        ]
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv.total_assets_in_market.return_value = 500 * 10**6
        mock_pv.get_market_substrates.return_value = []
        mock_pv_cls.return_value = mock_pv

        mock_erc20 = MagicMock()
        mock_erc20.symbol.return_value = "USDC"
        mock_erc20.decimals.return_value = 6
        mock_erc20_cls.return_value = mock_erc20

        mock_price = MagicMock()
        mock_price.readable.return_value = 1.0
        mock_oracle = MagicMock()
        mock_oracle.get_asset_price.return_value = mock_price
        mock_oracle_cls.return_value = mock_oracle

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "vault",
                "market-detail",
                ADDR_1,
                "--chain-id",
                "1",
                "--market-id",
                "1",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert data["market"] == "AAVE_V3"
        assert data["market_id"] == 1
        assert data["balance"]["raw"] == 500 * 10**6
        assert data["balance"]["formatted"] == "500.0"
        assert data["balance"]["usd"] == 500.0
        assert data["fuse"]["address"] == ADDR_FUSE_1
        assert data["fuse"]["contract"] == "AaveV3Fuse"
        assert data["substrates"] == []

    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="AaveV3Fuse")
    @patch("ipor_fusion.cli.vault_cmd.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_cmd.ERC20")
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_market_detail_text_output(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        mock_erc20_cls,
        mock_oracle_cls,
        mock_get_name,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 12345678
        mock_ctx_cls.from_url.return_value = mock_ctx

        @dataclass
        class FakeBalanceFuse:
            market_id: int
            fuse: str

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.get_balance_fuses.return_value = [
            FakeBalanceFuse(market_id=1, fuse=ADDR_FUSE_1),
        ]
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv.total_assets_in_market.return_value = 500 * 10**6
        mock_pv.get_market_substrates.return_value = []
        mock_pv_cls.return_value = mock_pv

        mock_erc20 = MagicMock()
        mock_erc20.symbol.return_value = "USDC"
        mock_erc20.decimals.return_value = 6
        mock_erc20_cls.return_value = mock_erc20

        mock_price = MagicMock()
        mock_price.readable.return_value = 1.0
        mock_oracle = MagicMock()
        mock_oracle.get_asset_price.return_value = mock_price
        mock_oracle_cls.return_value = mock_oracle

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "vault",
                "market-detail",
                ADDR_1,
                "--chain-id",
                "1",
                "--market-id",
                "1",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "AAVE_V3" in result.output
        assert "500.0 USDC" in result.output
        assert "AaveV3Fuse" in result.output
        assert "(cached)" in result.output

    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="MorphoFuse")
    @patch("ipor_fusion.cli.vault_cmd.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_cmd.ERC20")
    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_market_detail_with_bytes32_and_error_substrates(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        mock_erc20_cls,
        mock_oracle_cls,
        mock_get_name,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 100
        mock_ctx_cls.from_url.return_value = mock_ctx

        @dataclass
        class FakeBalanceFuse:
            market_id: int
            fuse: str

        morpho_market_id = bytes.fromhex(
            "32e253d33f1594a67fc6ef51bf7a39cc4bf2d14904998dee769706fcde489ed9"
        )
        bad_bytes = bytes.fromhex("ff" * 16)

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.get_balance_fuses.return_value = [
            FakeBalanceFuse(market_id=14, fuse=ADDR_FUSE_1),
        ]
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv.total_assets_in_market.return_value = 100 * 10**6
        mock_pv.get_market_substrates.return_value = [morpho_market_id, bad_bytes]
        mock_pv_cls.return_value = mock_pv

        mock_erc20 = MagicMock()
        mock_erc20.symbol.return_value = "USDC"
        mock_erc20.decimals.return_value = 6
        mock_erc20_cls.return_value = mock_erc20

        mock_oracle = MagicMock()
        mock_oracle.get_asset_price.return_value = None
        mock_oracle_cls.return_value = mock_oracle

        # Text output
        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "vault",
                "market-detail",
                ADDR_1,
                "--chain-id",
                "1",
                "--market-id",
                "14",
            ],
        )
        assert result.exit_code == 0, result.output
        assert "morpho_market_id" in result.output
        assert "[encoding error]" in result.output

        # JSON output
        result = runner.invoke(
            cli,
            [
                "vault",
                "market-detail",
                ADDR_1,
                "--chain-id",
                "1",
                "--market-id",
                "14",
                "--json",
            ],
        )
        assert result.exit_code == 0, result.output
        data = json.loads(result.output)
        assert len(data["substrates"]) == 2
        assert "raw" in data["substrates"][0]
        assert data["substrates"][0].get("error") is None
        assert data["substrates"][1].get("error") is True

    @patch("ipor_fusion.cli.vault_cmd.PlasmaVault")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    def test_market_detail_not_found(
        self,
        mock_ctx_cls,
        mock_pv_cls,
        tmp_config,
    ):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            vaults=[VaultEntry(address=ADDR_1, label="Test Vault", chain_id=1)],
        )
        save_config(cfg)

        mock_ctx = MagicMock()
        mock_ctx.web3.eth.block_number = 12345678
        mock_ctx_cls.from_url.return_value = mock_ctx

        @dataclass
        class FakeBalanceFuse:
            market_id: int
            fuse: str

        mock_pv = MagicMock()
        mock_pv.address = ADDR_1
        mock_pv.name.return_value = "Test Vault"
        mock_pv.get_balance_fuses.return_value = [
            FakeBalanceFuse(market_id=1, fuse=ADDR_FUSE_1),
        ]
        mock_pv.underlying_asset_address.return_value = ADDR_2
        mock_pv.get_price_oracle_middleware_address.return_value = ADDR_ORACLE
        mock_pv_cls.return_value = mock_pv

        runner = CliRunner()
        result = runner.invoke(
            cli,
            [
                "vault",
                "market-detail",
                ADDR_1,
                "--chain-id",
                "1",
                "--market-id",
                "999",
            ],
        )
        assert result.exit_code != 0
        assert "Market ID 999 not found" in result.output


class TestListAliasHelp:
    def test_vault_list_help_shows_alias(self, tmp_config):
        runner = CliRunner()
        result = runner.invoke(cli, ["vault", "list", "--help"])
        assert result.exit_code == 0
        assert "alias: ls" in result.output

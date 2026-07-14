"""Tests for the MCP server tool definitions (direct SDK import)."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from web3 import Web3

from ipor_fusion import (
    ContractNotFoundError,
    NotPlasmaVaultError,
    RoleAccount,
)
from ipor_fusion.cli.morpho_api import (
    MorphoApiError,
    MorphoApiMarket,
    VaultAllocation,
    VaultFlowCap,
    VaultV1Info,
    VaultV1MarketAllocation,
    VaultV2Adapter,
    VaultV2Cap,
    VaultV2Info,
)
from ipor_fusion.mcp.server import (
    config_set_etherscan_key,
    config_set_provider,
    config_show,
    market_meta_morpho,
    market_morpho_blue,
    mcp,
    vault_add,
    vault_info,
    vault_list,
    vault_remove,
    vault_role_accounts,
)
from ipor_fusion.readers.morpho import (
    MorphoMarket,
    MorphoMarketParams,
    MorphoMarketRates,
)
from ipor_fusion.types import Period, RoleId


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


# ── vault_role_accounts ───────────────────────────────────────────────


VAULT_ADDR = "0x" + "22" * 20


def _role_account(role_id: int, account: str, delay: int = 0) -> RoleAccount:
    return RoleAccount(
        account=account,  # type: ignore[arg-type]
        role_id=RoleId(role_id),
        is_member=True,
        execution_delay=Period(delay),
    )


def _mock_manager(accounts: list[RoleAccount]) -> MagicMock:
    manager = MagicMock()
    manager.address = "0xAM"
    manager.get_all_role_accounts.return_value = accounts
    manager.get_accounts_with_role.return_value = accounts
    return manager


class TestVaultRoleAccounts:
    @patch("ipor_fusion.mcp.server.resolve_access_manager")
    @patch("ipor_fusion.mcp.server._build_ctx", return_value=(MagicMock(), None))
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_provider(),
    )
    def test_all_roles(self, _load, _ctx, mock_resolve):
        manager = _mock_manager(
            [_role_account(100, "0xBBBB"), _role_account(1, "0xaaaa", delay=60)]
        )
        mock_resolve.return_value = manager

        result = vault_role_accounts(vault_address=VAULT_ADDR)

        manager.get_all_role_accounts.assert_called_once()
        manager.get_accounts_with_role.assert_not_called()
        assert result.role_filter is None
        assert result.access_manager == "0xAM"
        assert result.chain_id == 1  # single-provider fallback
        assert [
            (e.account, e.role_id, e.role_name, e.execution_delay)
            for e in result.accounts
        ] == [
            ("0xaaaa", 1, "OWNER_ROLE", 60),
            ("0xBBBB", 100, "ATOMIST_ROLE", 0),
        ]

    @patch("ipor_fusion.mcp.server.resolve_access_manager")
    @patch("ipor_fusion.mcp.server._build_ctx", return_value=(MagicMock(), None))
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_provider(),
    )
    def test_filtered_by_normalized_name(self, _load, _ctx, mock_resolve):
        manager = _mock_manager([_role_account(100, "0xBBBB")])
        mock_resolve.return_value = manager

        result = vault_role_accounts(vault_address=VAULT_ADDR, role="atomist")

        manager.get_accounts_with_role.assert_called_once_with(100)
        manager.get_all_role_accounts.assert_not_called()
        assert result.role_filter == "ATOMIST_ROLE"

    @patch("ipor_fusion.mcp.server._build_ctx")
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_provider(),
    )
    def test_unknown_role_fails_before_rpc(self, _load, mock_ctx):
        with pytest.raises(ValueError, match="Valid: ADMIN_ROLE"):
            vault_role_accounts(vault_address=VAULT_ADDR, role="archbishop")
        mock_ctx.assert_not_called()

    @patch(
        "ipor_fusion.mcp.server.resolve_access_manager",
        side_effect=NotPlasmaVaultError("not a vault"),
    )
    @patch("ipor_fusion.mcp.server._build_ctx", return_value=(MagicMock(), None))
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_provider(),
    )
    def test_guard_errors_propagate(self, _load, _ctx, _resolve):
        with pytest.raises(NotPlasmaVaultError, match="not a vault"):
            vault_role_accounts(vault_address=VAULT_ADDR)

    def test_description_lists_roles(self):
        # Verifies description= reached FastMCP and guards role-list drift.
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "vault_role_accounts")
        assert tool.description is not None
        assert "ATOMIST_ROLE" in tool.description
        assert "PUBLIC_ROLE" in tool.description


class TestVaultInfoGuards:
    @patch(
        "ipor_fusion.mcp.server.resolve_access_manager",
        side_effect=ContractNotFoundError("No contract found at 0x22... on chain 1."),
    )
    @patch("ipor_fusion.mcp.server._build_ctx", return_value=(MagicMock(), None))
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_provider(),
    )
    def test_no_contract_raises_typed(self, _load, _ctx, mock_resolve):
        with pytest.raises(ContractNotFoundError, match="No contract found"):
            vault_info(vault_address=VAULT_ADDR, chain_id=1)
        # The probe receives the checksummed address.
        mock_resolve.assert_called_once()
        assert mock_resolve.call_args.args[1] == Web3.to_checksum_address(VAULT_ADDR)

    @patch(
        "ipor_fusion.mcp.server.resolve_access_manager",
        side_effect=NotPlasmaVaultError("does not appear to be a Plasma Vault"),
    )
    @patch("ipor_fusion.mcp.server._build_ctx", return_value=(MagicMock(), None))
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_provider(),
    )
    def test_not_a_vault_raises_typed(self, _load, _ctx, _resolve):
        with pytest.raises(NotPlasmaVaultError, match="does not appear"):
            vault_info(vault_address=VAULT_ADDR, chain_id=1)

    @patch(
        "ipor_fusion.mcp.server._fetch_vault_data",
        side_effect=RuntimeError("some sub-call failed"),
    )
    @patch("ipor_fusion.mcp.server.resolve_access_manager")
    @patch("ipor_fusion.mcp.server._build_ctx", return_value=(MagicMock(), None))
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_provider(),
    )
    def test_other_fetch_errors_propagate(self, _load, _ctx, _resolve, _fetch):
        with pytest.raises(RuntimeError, match="some sub-call failed"):
            vault_info(vault_address=VAULT_ADDR, chain_id=1)


# ── market tools ──────────────────────────────────────────────────────


_MARKET_ID = "ad656d430bb3d8c1469bf45c8ad4ebae1b04be04757c69fa424eec78d7b3f4dc"
_LOAN = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
_COLLAT = "0xD2af830E8CBdFed6CC11Bab697bB25496ed6FA62"
_ORACLE = "0x7C65985C35181D51eF7571FA40211B57659B7d80"
_IRM = "0x870Ac11D48B15db9a138Cf899d20F13F79Ba00BC"
_VAULT = "0xf9bDdD4A9b3a45F980E11Fdde96E16364DDBec49"


def _stub_reader_instance():
    reader = MagicMock()
    reader.market_params.return_value.call.return_value = MorphoMarketParams(
        loan_token=_LOAN,
        collateral_token=_COLLAT,
        oracle=_ORACLE,
        irm=_IRM,
        lltv=915 * 10**15,
    )
    reader.market.return_value.call.return_value = MorphoMarket(
        total_supply_assets=1_008_277,
        total_supply_shares=1_008_277_000_000,
        total_borrow_assets=908_170,
        total_borrow_shares=908_170_000_000,
        last_update=1700000000,
        fee=0,
    )
    reader.rates_from.return_value = MorphoMarketRates(
        rate_per_second_wad=10**9,
        utilization=0.9,
        borrow_apy=0.032,
        supply_apy=0.029,
    )
    return reader


def _api_market():
    return MorphoApiMarket(
        market_id="0x" + _MARKET_ID,
        lltv=915 * 10**15,
        loan_token=_LOAN,
        loan_symbol="USDC",
        loan_decimals=6,
        collateral_token=_COLLAT,
        collateral_symbol="WOUSD",
        collateral_decimals=18,
        oracle=_ORACLE,
        irm=_IRM,
        supply_assets=1_008_277,
        borrow_assets=908_170,
        liquidity_assets=100_107,
        utilization=0.9,
        supply_apy=0.029,
        borrow_apy=0.032,
        fee_wad=0,
        timestamp=1700000000,
        vaults=[
            VaultAllocation(
                vault_address=_VAULT,
                vault_name="Yearn OG USDC",
                vault_symbol="ymvOG-USDC",
                asset_symbol="USDC",
                asset_decimals=6,
                total_assets=2_000_000_000,
                supply_assets=0,
                supply_cap=1_500_000_000_000,
                allocators=[_VAULT],
                flow_cap=VaultFlowCap(
                    fee_wei=10**15,
                    max_in=750_000_000_000,
                    max_out=250_000_000_000,
                    admin=_VAULT,
                ),
            )
        ],
    )


class TestMarketMorphoBlue:
    @patch("ipor_fusion.mcp.server.fetch_market")
    @patch("ipor_fusion.mcp.server.MorphoReader")
    @patch("ipor_fusion.mcp.server.Web3Context")
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_provider(),
    )
    def test_returns_market_state_and_vaults(
        self, _load, mock_ctx_cls, mock_reader_cls, mock_fetch
    ):
        mock_ctx_cls.from_url.return_value = MagicMock()
        mock_reader_cls.return_value = _stub_reader_instance()
        mock_fetch.return_value = _api_market()

        result = market_morpho_blue(market_id=_MARKET_ID, chain_id=1)

        assert result.market_id == "0x" + _MARKET_ID
        assert result.chain_id == 1
        assert result.public_allocator is not None  # singleton on Ethereum
        assert result.market_params.loan_token == _LOAN
        assert result.market_params.lltv == str(915 * 10**15)
        assert result.state.liquidity_assets == "100107"
        assert result.rates.utilization == 0.9
        assert result.api_error is None
        assert result.loan_asset is not None
        assert result.loan_asset.symbol == "USDC"
        assert result.vaults is not None and len(result.vaults) == 1
        vault = result.vaults[0]
        assert vault.public_allocator_config is not None
        assert vault.public_allocator_config.max_in == "750000000000"

    @patch("ipor_fusion.mcp.server.fetch_market")
    @patch("ipor_fusion.mcp.server.MorphoReader")
    @patch("ipor_fusion.mcp.server.Web3Context")
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_provider(),
    )
    def test_no_api_skips_fetch(self, _load, mock_ctx_cls, mock_reader_cls, mock_fetch):
        mock_ctx_cls.from_url.return_value = MagicMock()
        mock_reader_cls.return_value = _stub_reader_instance()

        result = market_morpho_blue(market_id=_MARKET_ID, chain_id=1, no_api=True)

        assert mock_fetch.call_count == 0
        assert result.vaults is None
        assert result.loan_asset is None
        assert result.api_error is None

    @patch("ipor_fusion.mcp.server.fetch_market", side_effect=MorphoApiError("down"))
    @patch("ipor_fusion.mcp.server.MorphoReader")
    @patch("ipor_fusion.mcp.server.Web3Context")
    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_provider(),
    )
    def test_api_error_surfaces_in_response(
        self, _load, mock_ctx_cls, mock_reader_cls, _fetch
    ):
        mock_ctx_cls.from_url.return_value = MagicMock()
        mock_reader_cls.return_value = _stub_reader_instance()

        result = market_morpho_blue(market_id=_MARKET_ID, chain_id=1)

        assert result.api_error == "down"
        assert result.vaults is None  # API didn't populate
        # On-chain rates still returned
        assert result.rates.utilization == 0.9

    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_config_with_provider(),
    )
    def test_invalid_market_id_rejected(self, _load):
        try:
            market_morpho_blue(market_id="0xdead", chain_id=1)
        except ValueError as exc:
            assert "invalid Morpho market ID" in str(exc)
        else:
            raise AssertionError("expected ValueError")

    @patch(
        "ipor_fusion.mcp.server.load_config",
        return_value=_empty_config(),
    )
    def test_missing_provider_errors(self, _load):
        try:
            market_morpho_blue(market_id=_MARKET_ID, chain_id=1)
        except ValueError as exc:
            assert "No provider for chain" in str(exc)
        else:
            raise AssertionError("expected ValueError")


def _v1_info():
    return VaultV1Info(
        address=_VAULT,
        name="Test V1",
        symbol="tv1",
        asset_address=_LOAN,
        asset_symbol="USDC",
        asset_decimals=6,
        total_assets=1_000_000_000,
        fee_wad=10**17,
        owner="0x" + "1" * 40,
        curator="0x" + "2" * 40,
        guardian="0x" + "3" * 40,
        fee_recipient="0x" + "4" * 40,
        allocators=[_VAULT],
        allocations=[
            VaultV1MarketAllocation(
                market_id="0x" + _MARKET_ID,
                lltv=915 * 10**15,
                loan_symbol="USDC",
                loan_decimals=6,
                collateral_symbol="WOUSD",
                market_supply_assets=1_000,
                market_borrow_assets=500,
                market_supply_apy=0.03,
                supply_assets=0,
                supply_cap=1_500_000_000_000,
            )
        ],
        public_allocator=VaultFlowCap(fee_wei=0, max_in=0, max_out=0, admin=_VAULT),
        public_allocator_flow_caps={"0x" + _MARKET_ID: (750_000_000_000, 0)},
    )


def _v2_info():
    cap = VaultV2Cap(
        cap_id="0xcap0",
        cap_type="MarketV1",
        id_data="0x",
        absolute_cap=1_500_000_000_000,
        relative_cap_wad=10**18,
        allocation=57,
        market_id="0x" + _MARKET_ID,
        loan_token=_LOAN,
        collateral_token=_COLLAT,
        oracle=_ORACLE,
        irm=_IRM,
        lltv=915 * 10**15,
    )
    adapter = VaultV2Adapter(
        address="0x" + "5" * 40,
        adapter_type="MorphoMarketV1",
        assets=100,
        inner_vault=None,
    )
    return VaultV2Info(
        address=_VAULT,
        name="Yearn OG USDC",
        symbol="yOG-USDC-V2",
        asset_address=_LOAN,
        asset_symbol="USDC",
        asset_decimals=6,
        total_assets=857_498_945_341,
        idle_assets=0,
        liquidity=844_616_692_135,
        share_price=1.018620,
        max_apy=1.71,
        performance_fee=0.0,
        performance_fee_recipient="0x" + "0" * 40,
        management_fee=0.0,
        management_fee_recipient="0x" + "0" * 40,
        owner="0x" + "1" * 40,
        curator="0x" + "2" * 40,
        allocators=["0x" + "3" * 40],
        sentinels=[],
        liquidity_adapter=adapter,
        adapters=[adapter],
        caps=[cap],
    )


class TestMarketMetaMorpho:
    @patch("ipor_fusion.mcp.server.fetch_vault", return_value=_v2_info())
    def test_returns_v2_vault(self, _fetch):
        result = market_meta_morpho(vault_address=_VAULT, chain_id=1)
        assert result.version == "v2"
        assert result.name == "Yearn OG USDC"
        assert result.caps is not None and len(result.caps) == 1
        assert result.caps[0]["loan_token"] == _LOAN
        # v1-only fields should be None
        assert result.fee_wad is None
        assert result.allocations is None

    @patch("ipor_fusion.mcp.server.fetch_vault", return_value=_v1_info())
    def test_returns_v1_vault(self, _fetch):
        result = market_meta_morpho(vault_address=_VAULT, chain_id=1)
        assert result.version == "v1"
        assert result.name == "Test V1"
        assert result.fee_wad == str(10**17)
        assert result.allocations is not None and len(result.allocations) == 1
        # v2-only fields should be None
        assert result.caps is None
        assert result.adapters is None

    @patch(
        "ipor_fusion.mcp.server.fetch_vault",
        side_effect=MorphoApiError("not found"),
    )
    def test_unknown_vault_errors(self, _fetch):
        try:
            market_meta_morpho(vault_address=_VAULT, chain_id=1)
        except ValueError as exc:
            assert "not found" in str(exc)
        else:
            raise AssertionError("expected ValueError")

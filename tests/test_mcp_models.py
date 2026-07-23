"""Contract tests for mcp/models.py.

Models use extra="forbid", so the test below must mirror the full shape that
cli/vault_cmd.py::_build_json_output produces. If you add a top-level key
there, this test will fail until the model is updated. That is the point.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ipor_fusion.core.access import RoleAccount
from ipor_fusion.mcp.models import (
    Amount,
    ConfigShowResponse,
    MetaMorphoVaultResponse,
    MorphoBlueMarketResponse,
    OracleMappingResponse,
    OracleNodeModel,
    Reconciliation,
    RoleAccountsResponse,
    VaultInfoResponse,
    VaultListEntry,
)
from ipor_fusion.readers.oracle_mapping import OracleMapping, OracleNode, OraclePrice
from ipor_fusion.types import Period, RoleId


def _amount(raw: int = 0, formatted: str = "0", usd: float | None = None) -> dict:
    return {"raw": raw, "formatted": formatted, "usd": usd}


def _full_vault_info_dict() -> dict:
    """Mirror of cli/vault_cmd.py::_build_json_output with every optional
    block populated (lending positions, withdraw manager, substrates with
    address+symbol+contract, fuses with optional market info, balance fuses
    with both Morpho and Aave position breakdowns, ERC20 with full token
    detail). Keep in sync with the dict literal at the bottom of
    _build_json_output — the sync itself is enforced by the producer contract
    check in test_cli_commands.py::TestVaultInfoJson::test_json_output."""
    return {
        "vault": "0xVAULT",
        "name": "Test Vault",
        "links": {
            "ipor_app": "https://app.ipor.io/ethereum/0xVAULT",
            "etherscan": "https://etherscan.io/address/0xVAULT",
        },
        "chain": "ethereum",
        "chain_id": 1,
        "block": 12345,
        "is_latest": True,
        "block_timestamp": 1700000000,
        "block_timestamp_utc": "2023-11-14T22:13:20Z",
        "deployment": {
            "deployer": "0xDEPLOYER",
            "deploy_block": 1000,
            "deploy_timestamp": 1600000000,
            "vault_age": "100d",
        },
        "asset": {
            "address": "0xUSDC",
            "symbol": "USDC",
            "decimals": 6,
            "price_usd": 1.0,
        },
        "share_decimals": 18,
        "total_assets": _amount(1_000_000_000, "1000.0", 1000.0),
        "total_supply": _amount(1_000_000_000_000_000_000_000, "1000.0", None),
        "share_price": {"raw": 10**18, "formatted": "1.0"},
        "supply_cap": _amount(2**256 - 1, "unlimited", None),
        "managers": {
            "access": "0xACCESS",
            "price_oracle": "0xORACLE",
            "rewards": "0xREWARDS",
            "withdraw": "0xWITHDRAW",
        },
        "withdraw_manager_details": {
            "withdraw_window_seconds": 86400,
            "request_fee_wad": 0,
            "withdraw_fee_wad": 0,
            "shares_to_release": _amount(0, "0"),
            "last_release_funds_timestamp": 1700000000,
            "last_release_funds_timestamp_utc": "2023-11-14T22:13:20Z",
            "total_pending_shares": _amount(0, "0"),
            "pending_requests": [],
        },
        "fuses": [
            {
                "address": "0xFUSE1",
                "contract": "MorphoSupplyFuse",
                "market_id": 1,
                "market": "MORPHO",
            },
            # market_id/market are omitted (not None) for market-less fuses.
            {"address": "0xFUSE2", "contract": "UniversalTokenSwapperFuse"},
        ],
        "balance_fuses": [
            {
                "market": "MORPHO (1)",
                "market_id": 1,
                "balance": _amount(500_000_000, "500.0", 500.0),
                "fuse": "0xBF1",
                "contract": "MorphoBalanceFuse",
                "pct_of_total": 50.0,
                "depends_on": ["AAVE_V3 (2)"],
                "position_breakdown": [
                    {
                        "morpho_market_id": "0xMID",
                        "collateral_symbol": "WETH",
                        "loan_symbol": "USDC",
                        "collateral": {
                            "raw": 1,
                            "token": "0xWETH",
                            "symbol": "WETH",
                            "decimals": 18,
                            "formatted": "0.000000000000000001",
                            "usd": 0.0,
                        },
                        "borrow": {"raw": 0, "token": "0xUSDC"},
                        "supply": {"raw": 0, "token": "0xUSDC"},
                    }
                ],
            },
            {
                "market": "AAVE_V3 (2)",
                "market_id": 2,
                "balance": _amount(300_000_000, "300.0", 300.0),
                "fuse": "0xBF2",
                "contract": "AaveV3BalanceFuse",
                "pct_of_total": 30.0,
                "position_breakdown": [
                    {
                        "asset": "0xUSDC",
                        "asset_symbol": "USDC",
                        "supply": {"raw": 100, "token": "0xUSDC"},
                        "variable_debt": {"raw": 0, "token": "0xUSDC"},
                        "stable_debt": {"raw": 0, "token": "0xUSDC"},
                    }
                ],
            },
        ],
        "zero_balance_fuses": [
            {
                "market": "UNIVERSAL_TOKEN_SWAPPER (12)",
                "market_id": 12,
                "balance": _amount(0, "0.0", 0.0),
                "fuse": "0xZBF1",
                "contract": "ZeroBalanceFuse",
                "pct_of_total": 0.0,
            },
        ],
        "instant_withdrawal_fuses": [
            {
                "address": "0xIW1",
                "contract": "ERC4626SupplyFuse",
                "market_id": 5,
                "market": "ERC4626",
            },
        ],
        "substrates": {
            "MORPHO (1)": [
                {
                    "address": "0xSUB",
                    "symbol": "WETH",
                    "contract": "WETH",
                    "substrate_type": "erc20",
                }
            ],
        },
        "dependency_graph": {"1": [2]},
        "erc20_balances": [
            {
                "address": "0xUSDC",
                "symbol": "USDC",
                "decimals": 6,
                "balance": _amount(100, "0.0001", 0.0001),
                "price_usd": 1.0,
                "usd_value": 0.0001,
                "note": "ok",
            }
        ],
        "reconciliation": {
            "balance_fuses_total": _amount(800_000_000, "800.0", 800.0),
            "underlying_on_vault": _amount(100, "0.0001", 0.0001),
            "erc20_direct_total": _amount(100, "0.0001", 0.0001),
            "sum": _amount(800_000_100, "800.0001", 800.0001),
            "on_chain_total_assets": _amount(800_000_100, "800.0001", 800.0001),
            "delta": {"raw": 0, "formatted": "0.0", "usd": 0.0, "percent": 0.0},
            "pending_withdrawals": _amount(0, "0", 0.0),
            "implied_market_total": {"raw": 800_000_000, "formatted": "800.0"},
            "market_storage_divergence": 0,
        },
        "lending_health": {
            "markets": [
                {
                    "protocol": "morpho",
                    "market_id": "0xMID",
                    "market_name": "WETH/USDC",
                    "current_ltv": 0.5,
                    "max_ltv": 0.86,
                    "health_factor": 1.72,
                    "total_collateral_usd": 1000.0,
                    "total_debt_usd": 500.0,
                    "ltv_usage_percent": 58.1,
                    "is_warning": False,
                    "is_critical": False,
                }
            ],
            "worst_ltv_usage_percent": 58.1,
        },
        "role_accounts": [
            {
                "account": "0xOWNER",
                "role_id": 1,
                "role_name": "OWNER_ROLE",
                "is_member": True,
                "execution_delay": 0,
            }
        ],
        # `ok` is a list of passing-check lines, not a boolean.
        "health_check": {
            "ok": ["morpho WETH/USDC: LTV 0.50/0.86, health_factor=1.72"],
            "warnings": [],
            "criticals": [],
        },
    }


class TestVaultInfoResponseContract:
    def test_full_dict_validates(self):
        result = VaultInfoResponse.model_validate(_full_vault_info_dict())
        assert result.vault == "0xVAULT"
        assert result.balance_fuses[0].position_breakdown is not None
        assert result.zero_balance_fuses[0].contract == "ZeroBalanceFuse"
        assert result.lending_health is not None
        assert result.reconciliation.delta.percent == 0.0
        assert result.fuses[0].market_id == 1
        assert result.fuses[0].market == "MORPHO"
        assert result.fuses[1].market_id is None
        assert result.health_check.ok == [
            "morpho WETH/USDC: LTV 0.50/0.86, health_factor=1.72"
        ]

    def test_minimal_dict_validates(self):
        """All optional sections set to None/empty."""
        d = _full_vault_info_dict()
        d["name"] = None
        d["deployment"] = None
        d["share_price"] = None
        d["withdraw_manager_details"] = None
        d["dependency_graph"] = None
        d["lending_health"] = None
        d["role_accounts"] = None
        d["balance_fuses"] = []
        d["substrates"] = {}
        d["erc20_balances"] = []
        d["fuses"] = []
        d["instant_withdrawal_fuses"] = []
        VaultInfoResponse.model_validate(d)

    def test_unknown_top_level_key_rejected(self):
        d = _full_vault_info_dict()
        d["unexpected_field"] = "drift"
        with pytest.raises(ValidationError):
            VaultInfoResponse.model_validate(d)

    def test_unknown_amount_field_rejected(self):
        d = _full_vault_info_dict()
        d["total_assets"]["new_metric"] = 42
        with pytest.raises(ValidationError):
            VaultInfoResponse.model_validate(d)

    def test_round_trip_preserves_shape(self):
        original = _full_vault_info_dict()
        model = VaultInfoResponse.model_validate(original)
        round_tripped = model.model_dump(mode="json")
        assert round_tripped["balance_fuses"][0]["balance"]["usd"] == 500.0
        assert round_tripped["reconciliation"]["delta"]["percent"] == 0.0


class TestSimpleResponseContracts:
    def test_config_show_round_trip(self):
        d = {
            "providers": {"1": "https://rpc.example.com"},
            "vaults": [
                {
                    "address": "0xABC",
                    "label": "main",
                    "chain": "ethereum",
                    "chain_id": 1,
                }
            ],
            "etherscan_api_key": "***",
        }
        ConfigShowResponse.model_validate(d)

    def test_amount_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            Amount.model_validate({"raw": 1, "formatted": "1", "extra": "no"})

    def test_vault_list_entry_round_trip(self):
        VaultListEntry.model_validate(
            {"address": "0xA", "label": "x", "chain": "base", "chain_id": 8453}
        )

    def test_morpho_blue_market_full_dict(self):
        d = {
            "market_id": "0x" + "ab" * 32,
            "chain_id": 1,
            "public_allocator": "0xPA",
            "market_params": {
                "loan_token": "0xLOAN",
                "collateral_token": "0xCOL",
                "oracle": "0xORA",
                "irm": "0xIRM",
                "lltv": "915000000000000000",
            },
            "state": {
                "total_supply_assets": "1000",
                "total_supply_shares": "1000000000000",
                "total_borrow_assets": "900",
                "total_borrow_shares": "900000000000",
                "liquidity_assets": "100",
                "fee_wad": "0",
                "last_update": 1700000000,
            },
            "rates": {
                "rate_per_second_wad": "1000000000",
                "utilization": 0.9,
                "borrow_apy": 0.032,
                "supply_apy": 0.029,
            },
            "loan_asset": {
                "address": "0xLOAN",
                "symbol": "USDC",
                "decimals": 6,
            },
            "collateral_asset": {
                "address": "0xCOL",
                "symbol": "WETH",
                "decimals": 18,
            },
            "vaults": [
                {
                    "address": "0xVAULT",
                    "name": "v",
                    "symbol": "v",
                    "asset": {"symbol": "USDC", "decimals": 6},
                    "total_assets": "1000",
                    "supply_assets": "0",
                    "supply_cap": "500",
                    "allocators": ["0xA"],
                    "public_allocator_config": {
                        "fee_wei": "0",
                        "max_in": "100",
                        "max_out": "0",
                        "admin": "0xADMIN",
                    },
                }
            ],
        }
        result = MorphoBlueMarketResponse.model_validate(d)
        assert result.market_id.startswith("0x")
        assert result.vaults is not None
        assert result.vaults[0].public_allocator_config is not None

    def test_morpho_blue_market_minimal_no_api(self):
        """no_api branch: no loan_asset / collateral_asset / vaults."""
        d = {
            "market_id": "0xab",
            "chain_id": 1,
            "market_params": {
                "loan_token": "0xL",
                "collateral_token": "0xC",
                "oracle": "0xO",
                "irm": "0xI",
                "lltv": "0",
            },
            "state": {
                "total_supply_assets": "0",
                "total_supply_shares": "0",
                "total_borrow_assets": "0",
                "total_borrow_shares": "0",
                "liquidity_assets": "0",
                "fee_wad": "0",
                "last_update": 0,
            },
            "rates": {
                "rate_per_second_wad": "0",
                "utilization": 0.0,
                "borrow_apy": 0.0,
                "supply_apy": 0.0,
            },
        }
        MorphoBlueMarketResponse.model_validate(d)

    def test_morpho_blue_unknown_field_rejected(self):
        d = {
            "market_id": "0xab",
            "chain_id": 1,
            "market_params": {
                "loan_token": "0xL",
                "collateral_token": "0xC",
                "oracle": "0xO",
                "irm": "0xI",
                "lltv": "0",
            },
            "state": {
                "total_supply_assets": "0",
                "total_supply_shares": "0",
                "total_borrow_assets": "0",
                "total_borrow_shares": "0",
                "liquidity_assets": "0",
                "fee_wad": "0",
                "last_update": 0,
            },
            "rates": {
                "rate_per_second_wad": "0",
                "utilization": 0.0,
                "borrow_apy": 0.0,
                "supply_apy": 0.0,
            },
            "drift": "no",
        }
        with pytest.raises(ValidationError):
            MorphoBlueMarketResponse.model_validate(d)

    def test_meta_morpho_v2_validates(self):
        d = {
            "version": "v2",
            "chain_id": 1,
            "address": "0xVAULT",
            "name": "v",
            "symbol": "v",
            "asset": {"address": "0xL", "symbol": "USDC", "decimals": 6},
            "total_assets": "1000",
            "idle_assets": "0",
            "liquidity": "1000",
            "share_price": 1.0,
            "max_apy": 0.05,
            "performance_fee": 0.0,
            "performance_fee_recipient": "0xPF",
            "management_fee": 0.0,
            "management_fee_recipient": "0xMF",
            "owner": "0xOWNER",
            "curator": "0xCUR",
            "allocators": ["0xA"],
            "sentinels": [],
            "liquidity_adapter": None,
            "adapters": [],
            "caps": [],
        }
        result = MetaMorphoVaultResponse.model_validate(d)
        assert result.version == "v2"
        assert result.fee_wad is None  # v1-only stays unset

    def test_meta_morpho_v1_validates(self):
        d = {
            "version": "v1",
            "chain_id": 1,
            "address": "0xVAULT",
            "name": "v",
            "symbol": "v",
            "asset": {"address": "0xL", "symbol": "USDC", "decimals": 6},
            "total_assets": "1000",
            "fee_wad": "100000000000000000",
            "owner": "0xOWNER",
            "curator": "0xCUR",
            "guardian": "0xGUARD",
            "fee_recipient": "0xFR",
            "allocators": ["0xA"],
            "public_allocator": None,
            "allocations": [],
        }
        result = MetaMorphoVaultResponse.model_validate(d)
        assert result.version == "v1"
        assert result.adapters is None  # v2-only stays unset

    def test_meta_morpho_unknown_field_rejected(self):
        d = {
            "version": "v1",
            "chain_id": 1,
            "address": "0xVAULT",
            "name": "v",
            "symbol": "v",
            "asset": {},
            "total_assets": "0",
            "owner": "0x",
            "curator": "0x",
            "allocators": [],
            "drift": "no",
        }
        with pytest.raises(ValidationError):
            MetaMorphoVaultResponse.model_validate(d)

    def test_reconciliation_with_no_usd_in_implied_market_total(self):
        """implied_market_total is the only Amount in reconciliation_json that
        omits the 'usd' key. Verify Amount.usd defaults to None."""
        d = {
            "balance_fuses_total": {"raw": 1, "formatted": "1", "usd": 1.0},
            "underlying_on_vault": {"raw": 0, "formatted": "0", "usd": 0.0},
            "erc20_direct_total": {"raw": 0, "formatted": "0", "usd": 0.0},
            "sum": {"raw": 1, "formatted": "1", "usd": 1.0},
            "on_chain_total_assets": {"raw": 1, "formatted": "1", "usd": 1.0},
            "delta": {"raw": 0, "formatted": "0", "usd": 0.0, "percent": 0.0},
            "pending_withdrawals": {"raw": 0, "formatted": "0", "usd": 0.0},
            "implied_market_total": {"raw": 1, "formatted": "1"},
            "market_storage_divergence": 0,
        }
        recon = Reconciliation.model_validate(d)
        assert recon.implied_market_total.usd is None


def _role_account(role_id: int, account: str, delay: int = 0) -> RoleAccount:
    return RoleAccount(
        account=account,  # type: ignore[arg-type]
        role_id=RoleId(role_id),
        is_member=True,
        execution_delay=Period(delay),
    )


class TestRoleAccountsResponse:
    def test_from_role_accounts_maps_and_sorts(self):
        accounts = [
            _role_account(100, "0xBBBB", delay=60),
            _role_account(1, "0xaaaa"),
            _role_account(2, "0xaaaa"),
        ]

        resp = RoleAccountsResponse.from_role_accounts(
            accounts,
            vault="0xVAULT",
            access_manager="0xAM",
            chain_id=1,
            role_filter=None,
        )

        # Sorted by (account.lower(), role_id); role_name resolved via the enum.
        assert [(e.account, e.role_id) for e in resp.accounts] == [
            ("0xaaaa", 1),
            ("0xaaaa", 2),
            ("0xBBBB", 100),
        ]
        assert resp.accounts[2].role_name == "ATOMIST_ROLE"
        assert resp.accounts[2].execution_delay == 60
        assert resp.role_filter is None

    def test_role_filter_echo(self):
        resp = RoleAccountsResponse.from_role_accounts(
            [],
            vault="0xVAULT",
            access_manager="0xAM",
            chain_id=1,
            role_filter="ATOMIST_ROLE",
        )
        assert resp.role_filter == "ATOMIST_ROLE"
        assert resp.accounts == []

    def test_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            RoleAccountsResponse.model_validate(
                {
                    "vault": "0xVAULT",
                    "access_manager": "0xAM",
                    "chain_id": 1,
                    "role_filter": None,
                    "accounts": [],
                    "extra": "no",
                }
            )


def _chainlink_node() -> OracleNode:
    return OracleNode(
        asset="0xUSDC",  # type: ignore[arg-type]
        symbol="USDC",
        decimals=6,
        source="0xFEED",  # type: ignore[arg-type]
        price=OraclePrice(
            raw="99980000",
            decimals=8,
            normalized_wad=str(99_980_000 * 10**10),
        ),
        source_type="ChainlinkAggregator",
        path=["USDC", "Chainlink feed"],
        status="resolved",
        source_detail={
            "description": "USDC / USD",
            "round_id": "1",
            "answer": "99980000",
            "decimals": 8,
            "started_at": 0,
            "updated_at": 1_700_000_000,
            "answered_in_round": "1",
        },
    )


def _erc4626_node() -> OracleNode:
    return OracleNode(
        asset="0xWSR",  # type: ignore[arg-type]
        symbol="wsrUSD",
        decimals=18,
        source="0xFEEDW",  # type: ignore[arg-type]
        price=OraclePrice(raw=str(10**18), decimals=18, normalized_wad=str(10**18)),
        source_type="ERC4626PriceFeed",
        path=["wsrUSD", "convertToAssets(1 share)", "USDC", "Chainlink feed"],
        status="resolved",
        source_detail={"vault": "0xV", "underlying": "0xUSDC"},
        dependencies=[_chainlink_node()],
    )


def _partial_node() -> OracleNode:
    return OracleNode(
        asset="0xNOPE",  # type: ignore[arg-type]
        symbol=None,
        decimals=None,
        source=None,
        price=OraclePrice(raw=None, decimals=None, normalized_wad=None),
        path=["0xNOPE"],
        status="partial",
        reason="no_source_configured",
    )


def _oracle_mapping() -> OracleMapping:
    partial = _partial_node()
    return OracleMapping(
        vault="0xVAULT",  # type: ignore[arg-type]
        vault_name="Reservoir",
        asset={"address": "0xUSDC", "symbol": "USDC", "decimals": 6},
        price_oracle="0xORACLE",  # type: ignore[arg-type]
        block_number=12345,
        asset_source="events",
        status="partially_resolved",
        configured_assets=[_erc4626_node(), partial],
        unresolved=[partial],
    )


class TestOracleMappingResponse:
    def test_from_mapping_maps_recursive_dependencies(self):
        resp = OracleMappingResponse.from_mapping(_oracle_mapping())

        assert resp.vault == "0xVAULT"
        assert resp.block_number == 12345
        assert resp.asset_source == "events"
        assert resp.status == "partially_resolved"
        node = resp.configured_assets[0]
        assert node.source_type == "ERC4626PriceFeed"
        assert node.price.normalized_wad == str(10**18)
        dep = node.dependencies[0]
        assert dep.symbol == "USDC"
        assert dep.source_detail == {
            "description": "USDC / USD",
            "round_id": "1",
            "answer": "99980000",
            "decimals": 8,
            "started_at": 0,
            "updated_at": 1_700_000_000,
            "answered_in_round": "1",
        }
        assert dep.dependencies == []

    def test_partial_node_fields(self):
        resp = OracleMappingResponse.from_mapping(_oracle_mapping())

        partial = resp.configured_assets[1]
        assert partial.status == "partial"
        assert partial.reason == "no_source_configured"
        assert partial.source_detail is None
        assert partial.price.raw is None
        # mirrored into unresolved as the same shape
        assert resp.unresolved[0] == partial

    def test_model_dump_matches_to_dict(self):
        # The MCP structured output and the CLI --json output are built from
        # the same dataclasses; this pins that they serialize identically.
        mapping = _oracle_mapping()

        resp = OracleMappingResponse.from_mapping(mapping)

        assert resp.model_dump(mode="json") == mapping.to_dict()

    def test_rejects_unknown_field(self):
        with pytest.raises(ValidationError):
            OracleMappingResponse.model_validate(
                {
                    "vault": "0xVAULT",
                    "vault_name": None,
                    "asset": {},
                    "price_oracle": "0xORACLE",
                    "block_number": 1,
                    "asset_source": "getConfiguredAssets",
                    "status": "resolved",
                    "configured_assets": [],
                    "unresolved": [],
                    "extra": "no",
                }
            )

    def test_rejects_unknown_status_values(self):
        # status / asset_source are Literal-pinned — typos must not validate
        mapping = _oracle_mapping()
        payload = OracleMappingResponse.from_mapping(mapping).model_dump()

        for key, bad in (("status", "kinda_resolved"), ("asset_source", "guess")):
            with pytest.raises(ValidationError):
                OracleMappingResponse.model_validate({**payload, key: bad})
        node_payload = payload["configured_assets"][0]
        # "unresolved" is mapping-level vocabulary — must not validate on a node
        with pytest.raises(ValidationError):
            OracleNodeModel.model_validate({**node_payload, "status": "unresolved"})

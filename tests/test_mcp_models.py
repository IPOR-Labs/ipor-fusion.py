"""Contract tests for mcp/models.py.

Models use extra="forbid", so the test below must mirror the full shape that
cli/vault_cmd.py::_build_json_output produces. If you add a top-level key
there, this test will fail until the model is updated. That is the point.
"""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from ipor_fusion.mcp.models import (
    Amount,
    ConfigShowResponse,
    Reconciliation,
    VaultInfoResponse,
    VaultListEntry,
)


def _amount(raw: int = 0, formatted: str = "0", usd: float | None = None) -> dict:
    return {"raw": raw, "formatted": formatted, "usd": usd}


def _full_vault_info_dict() -> dict:
    """Mirror of cli/vault_cmd.py::_build_json_output with every optional
    block populated (lending positions, withdraw manager, substrates with
    address+symbol+contract, balance fuses with both Morpho and Aave
    position breakdowns, ERC20 with full token detail). Keep in sync with
    the dict literal at the bottom of _build_json_output."""
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
            {"address": "0xFUSE1", "contract": "MorphoSupplyFuse"},
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
        "instant_withdrawal_fuses": [
            {"address": "0xIW1", "contract": "ERC4626SupplyFuse"},
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
        "health_check": {"ok": True, "warnings": []},
    }


class TestVaultInfoResponseContract:
    def test_full_dict_validates(self):
        result = VaultInfoResponse.model_validate(_full_vault_info_dict())
        assert result.vault == "0xVAULT"
        assert result.balance_fuses[0].position_breakdown is not None
        assert result.lending_health is not None
        assert result.reconciliation.delta.percent == 0.0

    def test_minimal_dict_validates(self):
        """All optional sections set to None/empty."""
        d = _full_vault_info_dict()
        d["name"] = None
        d["deployment"] = None
        d["share_price"] = None
        d["withdraw_manager_details"] = None
        d["dependency_graph"] = None
        d["lending_health"] = None
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

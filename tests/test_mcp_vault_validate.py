"""vault_validate: a thin adapter over the SDK's ``validate_vault``."""

from __future__ import annotations

import asyncio
import dataclasses
from unittest.mock import MagicMock, patch

import pytest
from mcp.server.fastmcp.exceptions import ToolError

from ipor_fusion import NotPlasmaVaultError, UnsupportedChainError
from ipor_fusion.cli.config_store import FusionConfig
from ipor_fusion.errors import UnsupportedVaultVersionError
from ipor_fusion.mcp.models import VaultValidationResponse
from ipor_fusion.mcp.server import mcp, vault_validate
from ipor_fusion.readers.lending_health import LendingMarketHealth, VaultLendingHealth
from ipor_fusion.validation import (
    Erc20Holding,
    Erc20Holdings,
    Finding,
    Reconciliation,
    VaultValidation,
)

VAULT = "0x31744E44d6aF88225C1dBEFbe5Df8308fAeA641B"


def _reconciliation(**overrides: int | float) -> Reconciliation:
    values: dict[str, int | float | None] = {
        f.name: 0 for f in dataclasses.fields(Reconciliation)
    }
    values.update(overrides)
    return Reconciliation(**values)  # type: ignore[arg-type]


def _validation(
    findings: tuple[Finding, ...] = (),
    lending: VaultLendingHealth | None = None,
    erc20: Erc20Holdings | None = None,
) -> VaultValidation:
    return VaultValidation(
        vault=VAULT,
        chain_id=8453,
        block_number=52500581,
        block_timestamp=1791790509,
        asset="0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913",
        asset_symbol="USDC",
        asset_decimals=6,
        asset_price_usd=0.9999,
        findings=findings,
        reconciliation=_reconciliation(
            balance_fuses_total=14912465797,
            balance_fuses_total_usd=14911.0,
            on_chain_total_assets=10_000_000_000,
            delta=-1500000,
            delta_usd=-1.5,
            delta_percent=-0.01,
        ),
        erc20=erc20 or Erc20Holdings(),
        lending_health=lending,
    )


def _market(**overrides: object) -> LendingMarketHealth:
    values: dict[str, object] = {
        "protocol": "aave_v4",
        "market_id": 46,
        "market_name": "AAVE_V4",
        "current_ltv": 0.5,
        "max_ltv": 0.75,
        "health_factor": 1.47,
        "total_collateral_usd": 1000.0,
        "total_debt_usd": 500.0,
        "ltv_usage_percent": 68.1,
    }
    values.update(overrides)
    return LendingMarketHealth(**values)  # type: ignore[arg-type]


class TestResponse:
    @staticmethod
    def test_findings_are_split_by_severity_and_the_verdict_is_the_worst():
        response = VaultValidationResponse.from_validation(
            _validation(
                (
                    Finding("lending_health", "ok", "morpho: LTV 0.10/0.62"),
                    Finding("orphan_fuse", "warning", "WARNING — fuse without market"),
                    Finding(
                        "lending_health", "critical", "CRITICAL — near liquidation"
                    ),
                )
            ),
            "base",
        )
        assert response.verdict == "critical"
        assert [f.check for f in response.criticals] == ["lending_health"]
        assert [f.check for f in response.warnings] == ["orphan_fuse"]
        assert [f.message for f in response.passed] == ["morpho: LTV 0.10/0.62"]

    @staticmethod
    def test_amounts_carry_raw_formatted_and_usd():
        response = VaultValidationResponse.from_validation(_validation(), "base")
        rec = response.reconciliation
        assert rec.balance_fuses_total.raw == 14912465797
        assert rec.balance_fuses_total.formatted == "14912.465797"
        assert rec.balance_fuses_total.usd == 14911.0
        assert rec.delta.formatted == "-1.5"
        assert rec.delta_percent == -0.01
        assert rec.implied_market_total.usd is None
        assert rec.sum.formatted == "0"
        assert response.block_timestamp_utc == "2026-10-12T07:35:09Z"
        assert response.verdict == "ok"
        assert response.asset.symbol == "USDC"

    @staticmethod
    def test_a_round_amount_is_never_in_scientific_notation():
        response = VaultValidationResponse.from_validation(_validation(), "base")
        assert response.reconciliation.on_chain_total_assets.formatted == "10000"

    @staticmethod
    def test_no_lending_position_is_null_not_an_empty_list():
        response = VaultValidationResponse.from_validation(
            _validation(lending=VaultLendingHealth(markets=[])), "base"
        )
        assert response.lending_health is None

    @staticmethod
    def test_lending_markets_and_worst_usage():
        response = VaultValidationResponse.from_validation(
            _validation(lending=VaultLendingHealth(markets=[_market()])), "base"
        )
        assert response.lending_health is not None
        assert response.lending_health.worst_ltv_usage_percent == 68.1
        assert response.lending_health.markets[0].health_factor == 1.47
        assert response.lending_health.markets[0].substrate_id is None

    @staticmethod
    def test_a_morpho_market_id_is_0x_prefixed_like_vault_info():
        market_id = "ab" * 32
        spoke = "0x" + "cd" * 20
        response = VaultValidationResponse.from_validation(
            _validation(
                lending=VaultLendingHealth(
                    markets=[
                        _market(protocol="morpho", substrate_id=market_id),
                        _market(substrate_id=spoke),
                    ]
                )
            ),
            "base",
        )
        assert response.lending_health is not None
        assert [m.substrate_id for m in response.lending_health.markets] == [
            f"0x{market_id}",
            spoke,
        ]

    @staticmethod
    def test_an_unreadable_token_balance_is_null():
        erc20 = Erc20Holdings(
            tokens=(
                Erc20Holding(
                    address="0xabc",
                    symbol="?",
                    decimals=None,
                    balance=None,
                    price_usd=None,
                    usd_value=None,
                    note="balanceOf reverted",
                ),
            ),
            unpriced_tokens=("0xabc",),
        )
        response = VaultValidationResponse.from_validation(
            _validation(erc20=erc20), "base"
        )
        assert response.erc20.tokens[0].balance is None
        assert response.erc20.unpriced_tokens == ["0xabc"]

    @staticmethod
    def test_a_held_token_balance_is_formatted_in_its_own_decimals():
        erc20 = Erc20Holdings(
            tokens=(
                Erc20Holding(
                    address="0xabc",
                    symbol="WETH",
                    decimals=18,
                    balance=25 * 10**17,
                    price_usd=4000.0,
                    usd_value=10000.0,
                    note="",
                ),
            ),
            value_in_asset=10_000_000_000,
            value_usd=10000.0,
        )
        response = VaultValidationResponse.from_validation(
            _validation(erc20=erc20), "base"
        )
        balance = response.erc20.tokens[0].balance
        assert balance is not None
        assert balance.formatted == "2.5"
        assert response.erc20.value_in_asset.formatted == "10000"
        assert response.erc20.value_in_asset.usd == 10000.0


def _config() -> FusionConfig:
    return FusionConfig(providers={"8453": "https://rpc.example.com"})


class TestTool:
    @staticmethod
    def _run(
        block_number: int = 0, validate: MagicMock | None = None
    ) -> tuple[VaultValidationResponse, MagicMock]:
        validate = validate or MagicMock(return_value=_validation())
        ctx = MagicMock()
        with (
            patch("ipor_fusion.mcp.server.load_config", return_value=_config()),
            patch(
                "ipor_fusion.mcp.server._build_ctx",
                return_value=(ctx, block_number or None),
            ),
            patch("ipor_fusion.mcp.server.resolve_access_manager") as probe,
            patch("ipor_fusion.mcp.server.validate_vault", validate),
        ):
            response = vault_validate(VAULT.lower(), 8453, block_number)
        probe.assert_called_once_with(ctx, VAULT)
        return response, validate

    def test_latest_passes_no_block(self):
        response, validate = self._run()
        assert validate.call_args.args[1:] == (VAULT,)
        assert validate.call_args.kwargs == {"block": None, "metadata": True}
        assert response.chain == "base"

    def test_a_block_is_passed_through(self):
        _, validate = self._run(block_number=52500581)
        assert validate.call_args.kwargs == {"block": 52500581, "metadata": True}

    def test_an_unsupported_vault_version_reaches_the_caller(self):
        validate = MagicMock(
            side_effect=UnsupportedVaultVersionError("vault predates getFoo()")
        )
        with pytest.raises(ValueError, match="predates"):
            self._run(validate=validate)

    @staticmethod
    @patch(
        "ipor_fusion.mcp.server.resolve_access_manager",
        side_effect=NotPlasmaVaultError("does not appear to be a Plasma Vault"),
    )
    @patch("ipor_fusion.mcp.server.validate_vault")
    @patch("ipor_fusion.mcp.server._build_ctx", return_value=(MagicMock(), None))
    @patch("ipor_fusion.mcp.server.load_config", return_value=_config())
    def test_not_a_vault_fails_before_the_fetch(_load, _ctx, validate, _probe):
        with pytest.raises(NotPlasmaVaultError, match="does not appear"):
            vault_validate(VAULT, 8453)
        validate.assert_not_called()

    @staticmethod
    @patch("ipor_fusion.mcp.server._build_ctx")
    @patch("ipor_fusion.mcp.server.load_config", return_value=_config())
    def test_unsupported_chain_fails_before_rpc(_load, build_ctx):
        with pytest.raises(UnsupportedChainError, match=r"chain 10 \(optimism\)"):
            vault_validate(VAULT, 10)
        build_ctx.assert_not_called()

    @staticmethod
    @patch("ipor_fusion.mcp.server.load_config")
    def test_negative_block_rejected(load):
        with pytest.raises(ToolError, match="block_number"):
            asyncio.run(
                mcp.call_tool(
                    "vault_validate",
                    {"vault_address": VAULT, "chain_id": 8453, "block_number": -1},
                )
            )
        load.assert_not_called()

    @staticmethod
    def test_output_schema_is_the_response_model():
        tools = asyncio.run(mcp.list_tools())
        tool = next(t for t in tools if t.name == "vault_validate")
        assert tool.outputSchema is not None
        assert set(tool.outputSchema["properties"]) == set(
            VaultValidationResponse.model_fields
        )
        assert tool.outputSchema["properties"]["verdict"]["enum"] == [
            "ok",
            "warning",
            "critical",
        ]

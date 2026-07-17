"""Tests for the `vault oracle-mapping` CLI command."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner

from ipor_fusion.cli import config_store
from ipor_fusion.cli.config_store import FusionConfig, save_config
from ipor_fusion.cli.main import cli
from ipor_fusion.errors import NotPlasmaVaultError
from ipor_fusion.readers.oracle_mapping import OracleMapping, OracleNode, OraclePrice

VAULT = "0x2222222222222222222222222222222222222222"
ORACLE = "0x9999999999999999999999999999999999999999"
USDC = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
WSR = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"
FEED = "0x1111111111111111111111111111111111111111"


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    config_dir = tmp_path / ".fusion"
    monkeypatch.setattr(config_store, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_store, "CONFIG_FILE", config_dir / "config.json")
    save_config(FusionConfig(providers={"1": "https://rpc.example.com"}))


def _resolved_node() -> OracleNode:
    return OracleNode(
        asset=USDC,  # type: ignore[arg-type]
        symbol="USDC",
        decimals=6,
        source=FEED,  # type: ignore[arg-type]
        price=OraclePrice(
            raw="99980000",
            decimals=8,
            normalized_wad=str(99_980_000 * 10**10),
        ),
        source_type="ChainlinkAggregator",
        path=["USDC", "Chainlink feed"],
        status="resolved",
    )


def _partial_node() -> OracleNode:
    return OracleNode(
        asset=WSR,  # type: ignore[arg-type]
        symbol="wsrUSD",
        decimals=18,
        source=None,
        price=OraclePrice(raw=None, decimals=None, normalized_wad=None),
        path=["wsrUSD"],
        status="partial",
        reason="no_source_configured",
    )


def _mapping(nodes: list[OracleNode]) -> OracleMapping:
    return OracleMapping(
        vault=VAULT,  # type: ignore[arg-type]
        vault_name="Reservoir",
        asset={"address": USDC, "symbol": "USDC", "decimals": 6},
        price_oracle=ORACLE,  # type: ignore[arg-type]
        block_number=12345,
        asset_source="getConfiguredAssets",
        configured_assets=nodes,
        unresolved=[n for n in nodes if n.status != "resolved"],
    )


def _fake_ctx(latest_block: int = 12345) -> MagicMock:
    ctx = MagicMock()
    ctx.web3.eth.block_number = latest_block
    ctx.default_block = "latest"
    return ctx


@patch("ipor_fusion.cli.vault_cmd.build_oracle_mapping")
@patch("ipor_fusion.cli.vault_cmd.resolve_access_manager")
@patch("ipor_fusion.cli.vault_cmd.Web3Context")
class TestOracleMappingCommand:
    def test_human_output(self, mock_ctx_cls, _resolve, mock_build, tmp_config):
        mock_ctx_cls.from_url.return_value = _fake_ctx()
        mock_build.return_value = _mapping([_resolved_node(), _partial_node()])

        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code == 0
        assert f"Vault:        {VAULT} (Reservoir)" in result.output
        assert f"Underlying:   USDC ({USDC})" in result.output
        assert f"Price Oracle: {ORACLE}" in result.output
        assert "Block:        12345" in result.output
        assert "Enumerated:   getConfiguredAssets" in result.output
        assert "Configured assets (2):" in result.output
        assert "USDC → Chainlink feed" in result.output
        assert "Price:  0.9998" in result.output
        assert "Status: resolved" in result.output
        assert "Status: partial (no_source_configured)" in result.output
        assert "Unresolved: 1" in result.output
        assert f"wsrUSD ({WSR}): no_source_configured" in result.output

    def test_no_unresolved_summary(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        mock_ctx_cls.from_url.return_value = _fake_ctx()
        mock_build.return_value = _mapping([_resolved_node()])

        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code == 0
        assert "Unresolved: 0" in result.output

    def test_json_output(self, mock_ctx_cls, _resolve, mock_build, tmp_config):
        mock_ctx_cls.from_url.return_value = _fake_ctx()
        mapping = _mapping([_resolved_node(), _partial_node()])
        mock_build.return_value = mapping

        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", VAULT, "--chain-id", "1", "--json"]
        )

        assert result.exit_code == 0
        assert json.loads(result.output) == mapping.to_dict()

    def test_latest_block_resolved_and_pinned(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        ctx = _fake_ctx(latest_block=777)
        mock_ctx_cls.from_url.return_value = ctx
        mock_build.return_value = _mapping([])

        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", VAULT, "--chain-id", "1", "--json"]
        )

        assert result.exit_code == 0
        # latest resolved to a number, passed to the SDK, and pinned on the ctx
        assert mock_build.call_args.args[2] == 777
        assert ctx.default_block == 777

    def test_explicit_block_passed_through(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        ctx = _fake_ctx()
        mock_ctx_cls.from_url.return_value = ctx
        mock_build.return_value = _mapping([])

        result = CliRunner().invoke(
            cli,
            [
                "vault",
                "oracle-mapping",
                VAULT,
                "--chain-id",
                "1",
                "--block-number",
                "500",
                "--json",
            ],
        )

        assert result.exit_code == 0
        assert mock_build.call_args.args[2] == 500
        assert ctx.default_block == 500

    def test_max_depth_passed_through(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        mock_ctx_cls.from_url.return_value = _fake_ctx()
        mock_build.return_value = _mapping([])

        result = CliRunner().invoke(
            cli,
            ["vault", "oracle-mapping", VAULT, "--chain-id", "1", "--max-depth", "3"],
        )

        assert result.exit_code == 0
        assert mock_build.call_args.args[3] == 3

    def test_max_depth_defaults_to_six(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        mock_ctx_cls.from_url.return_value = _fake_ctx()
        mock_build.return_value = _mapping([])

        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code == 0
        assert mock_build.call_args.args[3] == 6

    def test_negative_block_rejected_before_rpc(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        result = CliRunner().invoke(
            cli,
            [
                "vault",
                "oracle-mapping",
                VAULT,
                "--chain-id",
                "1",
                "--block-number",
                "-1",
            ],
        )

        assert result.exit_code != 0
        assert "-1" in result.output
        mock_ctx_cls.from_url.assert_not_called()
        mock_build.assert_not_called()

    def test_bad_address_rejected_before_rpc(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", "0x1234", "--chain-id", "1"]
        )

        assert result.exit_code != 0
        assert "invalid Ethereum address" in result.output
        mock_ctx_cls.from_url.assert_not_called()

    def test_unknown_vault_needs_chain_id(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        result = CliRunner().invoke(cli, ["vault", "oracle-mapping", VAULT])

        assert result.exit_code != 0
        assert "--chain-id" in result.output

    def test_not_a_vault_is_usage_error(
        self, mock_ctx_cls, mock_resolve, mock_build, tmp_config
    ):
        mock_ctx_cls.from_url.return_value = _fake_ctx()
        mock_resolve.side_effect = NotPlasmaVaultError("not a Plasma Vault")

        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code != 0
        assert "not a Plasma Vault" in result.output
        assert "Traceback" not in result.output
        mock_build.assert_not_called()

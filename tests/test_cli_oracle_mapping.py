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
        source_detail={
            "description": "USDC / USD",
            "round_id": "1",
            "answer": "99980000",
            "decimals": 8,
            "started_at": 0,
            "started_at_utc": None,
            "updated_at": 1_700_000_000,
            "updated_at_utc": "2023-11-14T22:13:20Z",
            "answered_in_round": "1",
            "aggregator": None,
            "phase_id": None,
        },
    )


def _dual_xref_node() -> OracleNode:
    return OracleNode(
        asset=WSR,  # type: ignore[arg-type]
        symbol="wstETH",
        decimals=18,
        source=FEED,  # type: ignore[arg-type]
        price=OraclePrice(
            raw=str(2_600 * 10**8),
            decimals=8,
            normalized_wad=str(2_600 * 10**18),
        ),
        source_type="DualCrossReferencePriceFeed",
        path=[
            "wstETH",
            "DualCrossReferencePriceFeed",
            "ASSET_X/ASSET_Y feed",
            "ASSET_Y/USD feed",
        ],
        status="resolved",
        source_detail={
            "asset_x": USDC,
            "asset_x_asset_y_feed": {"address": FEED, "description": "wstETH / stETH"},
            "asset_y_usd_feed": {"address": FEED, "description": "ETH / USD"},
            "derived_price_wad": str(2_600 * 10**18),
        },
    )


def _partial_node() -> OracleNode:
    return OracleNode(
        asset=WSR,  # type: ignore[arg-type]
        symbol="wsrUSD",
        decimals=18,
        source=None,
        price=None,
        path=["wsrUSD"],
        status="partial",
        reason="no_source_configured",
    )


def _rollup(nodes: list[OracleNode]):
    if all(n.status == "resolved" for n in nodes):
        return "resolved"
    if all(n.status == "partial" for n in nodes):
        return "unresolved"
    return "partially_resolved"


def _partials(nodes: list[OracleNode]) -> list[OracleNode]:
    # mirrors the engine's _collect_unresolved: partial nodes, whole tree
    out: list[OracleNode] = []
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if node.status == "partial":
            out.append(node)
        stack.extend(node.dependencies)
    return out


def _mapping(nodes: list[OracleNode]) -> OracleMapping:
    return OracleMapping(
        vault=VAULT,  # type: ignore[arg-type]
        vault_name="Reservoir",
        asset={"address": USDC, "symbol": "USDC", "decimals": 6},
        price_oracle=ORACLE,  # type: ignore[arg-type]
        block_number=12345,
        asset_source="getConfiguredAssets",
        status=_rollup(nodes),
        configured_assets=nodes,
        unresolved=_partials(nodes),
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
        assert "Status:       partially_resolved" in result.output
        assert "Configured assets (2):" in result.output
        assert "USDC → Chainlink feed" in result.output
        # feed description shown for the aggregator node, absent for the
        # source-less partial node
        assert "Feed:   USDC / USD" in result.output
        assert result.output.count("Feed:") == 1
        assert "Price:  0.9998" in result.output
        assert "Status: resolved" in result.output
        assert "Status: partial (no_source_configured)" in result.output
        assert "Unresolved: 1" in result.output
        assert f"wsrUSD ({WSR}): no_source_configured" in result.output

    def test_chainlink_style_without_description_shows_marker(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        # the confirmed tier requires a description, so only chainlink_style
        # leaves can lack one — flagged instead of silently dropping the line
        mock_ctx_cls.from_url.return_value = _fake_ctx()
        node = _resolved_node()
        node.source_type = "chainlink_style"
        node.path = ["USDC", "Chainlink-style feed"]
        assert node.source_detail is not None
        node.source_detail["description"] = None
        mock_build.return_value = _mapping([node])

        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code == 0
        assert "USDC → Chainlink-style feed" in result.output
        assert "Feed:   (no description)" in result.output

    def test_no_feed_line_for_non_aggregator_source_detail(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        # non-aggregator detail payloads (here: ERC4626) carry no description
        # and must not render a Feed line at all
        mock_ctx_cls.from_url.return_value = _fake_ctx()
        node = _resolved_node()
        node.source_type = "ERC4626PriceFeed"
        node.path = ["wsrUSD", "convertToAssets(1 share)", "USDC", "Chainlink feed"]
        node.source_detail = {"vault": FEED, "underlying": USDC}
        mock_build.return_value = _mapping([node])

        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code == 0
        assert "Feed:" not in result.output

    def test_dual_xref_feed_line_composed(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        # both component descriptions on one line, quoted — the descriptions
        # themselves contain " / "
        mock_ctx_cls.from_url.return_value = _fake_ctx()
        mock_build.return_value = _mapping([_dual_xref_node()])

        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code == 0
        assert 'Feed:   "wstETH / stETH" × "ETH / USD"' in result.output

    def test_dual_xref_feed_line_null_component(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        mock_ctx_cls.from_url.return_value = _fake_ctx()
        node = _dual_xref_node()
        assert node.source_detail is not None
        node.source_detail["asset_x_asset_y_feed"]["description"] = None
        mock_build.return_value = _mapping([node])

        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code == 0
        assert 'Feed:   (no description) × "ETH / USD"' in result.output

    def test_no_unresolved_summary(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        mock_ctx_cls.from_url.return_value = _fake_ctx()
        mock_build.return_value = _mapping([_resolved_node()])

        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code == 0
        assert "Status:       resolved" in result.output
        assert "Unresolved: 0" in result.output

    def test_partially_resolved_node_rendering(
        self, mock_ctx_cls, _resolve, mock_build, tmp_config
    ):
        mock_ctx_cls.from_url.return_value = _fake_ctx()
        parent = _resolved_node()
        parent.status = "partially_resolved"
        parent.dependencies = [_partial_node()]
        mock_build.return_value = _mapping([parent])

        result = CliRunner().invoke(
            cli, ["vault", "oracle-mapping", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code == 0
        assert "Status: partially_resolved" in result.output
        # a partially_resolved root is partial success, not total failure
        assert "Status:       partially_resolved" in result.output
        # only the partial dep is mirrored — the demoted parent is not
        assert "Unresolved: 1" in result.output
        assert f"wsrUSD ({WSR}): no_source_configured" in result.output

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

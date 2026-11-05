"""`fusion fuse whitelist` and the `fuse_whitelist` MCP tool: thin adapters
over `fetch_fuse_whitelist_json`."""

import asyncio
import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from mcp.server.fastmcp.exceptions import ToolError

from ipor_fusion.cli.main import cli
from ipor_fusion.errors import AddressNotInRegistryError
from ipor_fusion.fuse_whitelist import FuseWhitelistJson
from ipor_fusion.mcp import server

DOCUMENT: FuseWhitelistJson = {
    "chain_id": 42161,
    "chain": "arbitrum",
    "whitelist": "0xc1e4dE288313B07d51FA49A202d231CcEEB07A9A",
    "market_id": 11,
    "market": "EULER_V2 (11)",
    "fuses": [
        {
            "fuse": "0xF7dF7b8eB5B0c6d02b1b8FEC769dFE65D9a27E3b",
            "state": "active",
            "fuse_type": 36,
            "type_name": "EulerV2BalanceFuse",
            "role": "balance",
            "added_at": 1751503130,
        }
    ],
}


class TestCli:
    @staticmethod
    def _run(*args: str, document=DOCUMENT, error=None):
        fetch = MagicMock(return_value=document, side_effect=error)
        with (
            patch("ipor_fusion.cli.fuse_cmd.load_config"),
            patch(
                "ipor_fusion.cli.fuse_cmd._resolve_provider", return_value="http://rpc"
            ),
            patch("ipor_fusion.cli.fuse_cmd.Web3Context") as ctx_cls,
            patch("ipor_fusion.cli.fuse_cmd.fetch_fuse_whitelist_json", fetch),
        ):
            result = CliRunner().invoke(cli, ["fuse", "whitelist", *args])
        return result, fetch, ctx_cls

    def test_prints_the_market_table(self):
        result, fetch, _ = self._run("--chain", "arbitrum", "--market-id", "11")

        assert result.exit_code == 0, result.output
        assert "EULER_V2 (11)" in result.output
        assert "0xF7dF7b8eB5B0c6d02b1b8FEC769dFE65D9a27E3b" in result.output
        assert "2025-07-03" in result.output
        assert fetch.call_args.kwargs == {"include_deprecated": False}

    def test_json_and_flags_pass_through(self):
        result, fetch, ctx_cls = self._run(
            "--chain",
            "42161",
            "--market-id",
            "11",
            "--block",
            "5",
            "--include-deprecated",
            "--json",
        )

        assert result.exit_code == 0, result.output
        assert json.loads(result.output) == DOCUMENT
        assert fetch.call_args.kwargs == {"include_deprecated": True}
        ctx_cls.from_url.return_value.at_block.assert_called_once_with(5)

    def test_a_chain_without_a_whitelist_is_a_usage_error(self):
        result, _, _ = self._run(
            "--chain",
            "arbitrum",
            "--market-id",
            "11",
            error=AddressNotInRegistryError("no whitelist"),
        )

        assert result.exit_code == 2
        assert "no whitelist" in result.output


class TestTool:
    @staticmethod
    def _call(**arguments: object):
        fetch = MagicMock(return_value=DOCUMENT)
        with (
            patch("ipor_fusion.mcp.server.load_config"),
            patch(
                "ipor_fusion.mcp.server._build_ctx", return_value=(MagicMock(), None)
            ) as build,
            patch("ipor_fusion.mcp.server.fetch_fuse_whitelist_json", fetch),
        ):
            _content, structured = asyncio.run(
                server.mcp.call_tool("fuse_whitelist", arguments)
            )
        return structured, fetch, build

    def test_returns_the_document(self):
        structured, fetch, build = self._call(chain_id=42161, market_id=11)

        assert structured == DOCUMENT
        assert build.call_args.args[1:] == (42161, None)
        assert fetch.call_args.kwargs == {"include_deprecated": False}

    def test_a_negative_market_is_refused(self):
        with pytest.raises(ToolError):
            self._call(chain_id=42161, market_id=-1)

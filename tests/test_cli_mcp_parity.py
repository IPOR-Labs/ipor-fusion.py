"""Every CLI command has an MCP tool with the same arguments (AGENTS.md, "CLI
and MCP"): the same name with dashes as underscores, ``changelog`` as
``server_info``, and the CLI's presentation-only options left out."""

from __future__ import annotations

import asyncio
import inspect
from collections.abc import Iterator
from unittest.mock import patch

import click
import pytest
from click.testing import CliRunner
from mcp.server.fastmcp.exceptions import ToolError

from ipor_fusion.cli.main import cli
from ipor_fusion.mcp import server

# A CLI alias of another command.
_ALIASES = {"vault ls"}
# CLI options that shape the terminal output, not what is read.
_PRESENTATION = {"json_output", "vault_filter", "market_filter"}


def _commands(
    group: click.Group, prefix: str = ""
) -> Iterator[tuple[str, click.Command]]:
    for name, command in group.commands.items():
        full = f"{prefix}{name}"
        if isinstance(command, click.Group):
            yield from _commands(command, f"{full} ")
        elif full not in _ALIASES:
            yield full, command


def _tool_name(command: str) -> str:
    if command == "changelog":
        return "server_info"
    return command.replace(" ", "_").replace("-", "_")


def test_every_command_has_a_tool_and_every_tool_a_command() -> None:
    tools = {tool.name for tool in asyncio.run(server.mcp.list_tools())}
    commands = {_tool_name(name) for name, _ in _commands(cli)}
    assert commands == tools


def test_a_tool_takes_the_arguments_of_its_command() -> None:
    mismatched = {}
    for name, command in _commands(cli):
        cli_params = {p.name for p in command.params} - _PRESENTATION
        tool = getattr(server, _tool_name(name))
        tool_params = set(inspect.signature(tool).parameters)
        if cli_params != tool_params:
            mismatched[name] = (sorted(cli_params), sorted(tool_params))
    assert not mismatched


def test_chain_and_block_are_optional_and_never_a_sentinel() -> None:
    tools = {tool.name: tool for tool in asyncio.run(server.mcp.list_tools())}
    for name, tool in tools.items():
        props = tool.inputSchema["properties"]
        for arg in ("chain_id", "block_number"):
            if arg in props and arg not in tool.inputSchema.get("required", []):
                assert props[arg].get("default") is None, (name, arg)
                assert {"type": "null"} in props[arg].get("anyOf", []), (name, arg)


def test_a_zero_chain_is_rejected_by_both_surfaces() -> None:
    result = CliRunner().invoke(
        cli, ["config", "set-provider", "https://rpc.example.com", "--chain-id", "0"]
    )
    assert result.exit_code == 2
    props = {t.name: t for t in asyncio.run(server.mcp.list_tools())}[
        "config_set_provider"
    ].inputSchema["properties"]["chain_id"]
    assert {"type": "integer", "exclusiveMinimum": 0} in props["anyOf"]


_VAULT = "0x" + "11" * 20
_MARKET = "0x" + "ab" * 32


@pytest.mark.parametrize(
    ("tool", "arguments", "name"),
    [
        ("vault_info", {"vault_address": _VAULT, "block_number": 0}, "block_number"),
        ("vault_validate", {"vault_address": _VAULT, "chain_id": 0}, "chain_id"),
        (
            "market_morpho_blue",
            {"market_id": _MARKET, "chain_id": 1, "block_number": 0},
            "block_number",
        ),
        (
            "config_set_provider",
            {"url": "https://rpc.example.com", "chain_id": 0},
            "chain_id",
        ),
    ],
)
def test_mcp_refuses_the_old_zero_sentinel(
    tool: str, arguments: dict[str, object], name: str
) -> None:
    """0 used to mean latest / auto: read as block or chain 0 it would answer
    silently with genesis data, so it is an error naming the fix."""
    with (
        patch("ipor_fusion.mcp.server.load_config", autospec=True) as load,
        pytest.raises(ToolError, match=f"omit {name}"),
    ):
        asyncio.run(server.mcp.call_tool(tool, arguments))
    load.assert_not_called()


@pytest.mark.parametrize(
    ("tool", "arguments"),
    [
        ("market_morpho_blue", {"market_id": _MARKET, "chain_id": 0}),
        ("market_meta_morpho", {"vault_address": _VAULT, "chain_id": 0}),
    ],
)
def test_a_required_chain_is_positive(tool: str, arguments: dict[str, object]) -> None:
    with (
        patch("ipor_fusion.mcp.server.load_config", autospec=True) as load,
        patch("ipor_fusion.mcp.server.fetch_vault", autospec=True) as fetch,
        pytest.raises(ToolError, match="chain_id"),
    ):
        asyncio.run(server.mcp.call_tool(tool, arguments))
    load.assert_not_called()
    fetch.assert_not_called()


def test_the_block_schema_requires_a_positive_block() -> None:
    props = {t.name: t for t in asyncio.run(server.mcp.list_tools())}[
        "vault_info"
    ].inputSchema["properties"]["block_number"]
    assert {"type": "integer", "minimum": 1} in props["anyOf"]


@pytest.mark.parametrize(
    "args",
    [
        ["vault", "info", _VAULT, "--block-number", "0"],
        ["vault", "validate", _VAULT, "--block-number", "0"],
        ["market", "morpho-blue", _MARKET, "--chain", "1", "--block", "0"],
    ],
)
def test_cli_refuses_block_zero(args: list[str]) -> None:
    result = CliRunner().invoke(cli, args)
    assert result.exit_code == 2
    assert "omit --block-number" in result.output


def test_cli_refuses_chain_zero() -> None:
    result = CliRunner().invoke(cli, ["vault", "info", _VAULT, "--chain-id", "0"])
    assert result.exit_code == 2
    assert "omit --chain-id" in result.output

"""The address-registry MCP tools over a faked registry."""

import asyncio
import json

import pytest
from mcp.server.fastmcp.exceptions import ToolError

import ipor_fusion.registry as registry_module
from ipor_fusion.mcp import server

PROXY = "0x1455717668fA96534f675856347A973fA907e922"


@pytest.fixture(autouse=True)
def _registry(monkeypatch):
    monkeypatch.setattr(registry_module, "_cache", {})
    monkeypatch.setattr(
        registry_module,
        "_download",
        lambda url, _t: json.dumps(
            {"IporFusionFactoryProxy": PROXY} if "-base-" in url else {}
        ).encode(),
    )


def _call(tool: str, **arguments: object) -> dict[str, object]:
    _content, structured = asyncio.run(server.mcp.call_tool(tool, arguments))
    assert isinstance(structured, dict)
    return structured


def test_lookup_across_every_chain():
    result = _call("address_lookup", query="FactoryProxy")
    assert result["match_count"] == 1
    assert result["matches"] == [
        {
            "chain_id": 8453,
            "chain": "base",
            "name": "IporFusionFactoryProxy",
            "address": PROXY,
        }
    ]


def test_names_on_one_chain():
    result = _call("address_names", chain_id=8453)
    assert result == {
        "chain_ids": [8453],
        "names": [{"name": "IporFusionFactoryProxy", "chain_ids": [8453]}],
    }


def test_list_requires_a_chain_and_returns_the_map():
    result = _call("address_list", chain_id=8453)
    assert result["count"] == 1
    assert result["addresses"] == {"IporFusionFactoryProxy": PROXY}
    with pytest.raises(ToolError):
        _call("address_list")


def test_zero_is_no_sentinel_for_all_chains():
    with pytest.raises(ToolError, match="chain_id=0"):
        _call("address_lookup", query="x", chain_id=0)

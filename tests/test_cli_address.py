"""`fusion address` commands over a faked registry."""

import json

import pytest
from click.testing import CliRunner

import ipor_fusion.registry as registry_module
from ipor_fusion.cli.main import cli

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


def _run(*args: str):
    return CliRunner().invoke(cli, ["address", *args])


def test_lookup_prints_the_matches():
    result = _run("lookup", "factoryproxy", "--chain", "base")
    assert result.exit_code == 0, result.output
    assert "IporFusionFactoryProxy" in result.output
    assert PROXY in result.output


def test_lookup_json_is_the_sdk_document():
    result = _run("lookup", PROXY, "--json")
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["query_type"] == "address"
    assert document["matches"] == [
        {
            "chain_id": 8453,
            "chain": "base",
            "name": "IporFusionFactoryProxy",
            "address": PROXY,
        }
    ]


def test_names_and_list():
    names = _run("names", "--json")
    assert json.loads(names.output)["names"] == [
        {"name": "IporFusionFactoryProxy", "chain_ids": [8453]}
    ]
    listed = _run("list", "--chain", "8453", "--json")
    assert json.loads(listed.output)["addresses"] == {"IporFusionFactoryProxy": PROXY}
    table = _run("list", "--chain", "base")
    assert PROXY in table.output


def test_a_chain_without_a_registry_is_a_usage_error():
    result = _run("list", "--chain", "optimism")
    assert result.exit_code == 2
    assert "no Fusion deployment file" in result.output


def test_list_needs_a_chain():
    assert _run("list").exit_code == 2

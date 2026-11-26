"""An injection campaign's payloads as a regression corpus, with a gate.

In 2026-11 an internet client fuzzed IPOR's hosted MCP endpoints with SSRF
URLs, path traversal, SSTI canaries, dunder attribute access and probes
for unregistered tool, resource and prompt names. This server is the
self-hostable sibling built on the same SDK, so the same corpus runs
against every tool here: a payload must be refused or handled as inert
data — never fetched, never evaluated — and an SSTI canary must come back
literal (``{{7*7}}`` stays ``{{7*7}}``, ``49`` appears nowhere).

Each case names the safe outcome it expects:

- ``refused`` — the call errors out, in schema validation or in the
  tool's own argument parsing, before anything uses the value.
- ``inert`` — the body runs and treats the payload as data (a query that
  matches nothing, a config value that is stored, not followed).

Every outbound touchpoint (provider resolution, Morpho API, the ipor-abi
registry, fuse whitelist reads) is a recording fake, so a payload
reaching one fails the case; the local config store is faked too, and
only the config tools may write the payload there.

``TestCoverageGate`` enumerates the registered tools, prompts and
resources and fails when one appears without corpus coverage for a
category — a new tool without abuse tests is red before review.
"""

from __future__ import annotations

import asyncio
import dataclasses
from typing import Any

import pytest
from mcp.server.fastmcp.exceptions import ToolError as McpToolError
from web3 import Web3

from ipor_fusion.cli.config_store import FusionConfig
from ipor_fusion.guide import RESOURCES
from ipor_fusion.mcp import server as mcp_server

VAULT = Web3.to_checksum_address("0x" + "11" * 20)

CATEGORIES = ("ssrf", "traversal", "ssti", "attr_access", "enumeration")

# The campaign's payloads, sanitised from the hosted endpoint's log lines.
PAYLOADS: dict[str, tuple[str, ...]] = {
    "ssrf": (
        "http://169.254.169.254/latest/meta-data/",
        "http://127.0.0.1:8080/",
        "https://ipor.io@127.0.0.1:80/",
        "http://internal.mainnet.ipor.io/",
    ),
    "traversal": (
        "../../../../etc/passwd",
        "..\\..\\..\\etc\\passwd",
        "file:///proc/self/environ",
        "docs.md\x00.txt",
        "docs.md;.txt",
    ),
    "ssti": ("{{7*7}}", "${7*7}", "<%=7*7%>"),
    "attr_access": ("address.__class__", "__class__", "__import__", "__globals__"),
}

# Enumeration probes names, not argument values: its cases are derived from
# the registered names themselves (TestEnumeration), so every tool, resource
# and prompt is covered by construction.
DERIVED = "derived: name probes generated from the registered names"


@dataclasses.dataclass(frozen=True)
class Inject:
    """Deliver each of a category's payloads through one argument."""

    arg: str
    outcome: str  # "refused" | "inert"
    may_call: frozenset[str] = frozenset()
    # Fakes whose recorded arguments may hold the payload: the config store,
    # where config tools write the value by design (stored, never followed).
    stores: frozenset[str] = frozenset()


def refused(arg: str, *may_call: str) -> Inject:
    return Inject(arg, "refused", frozenset(may_call))


def inert(arg: str, *may_call: str, stores: tuple[str, ...] = ()) -> Inject:
    return Inject(arg, "inert", frozenset(may_call), frozenset(stores))


# Valid arguments per tool, so only the injected argument can be at fault.
BASELINE: dict[str, dict[str, Any]] = {
    "server_info": {},
    "vault_info": {"vault_address": VAULT, "chain_id": 1},
    "vault_validate": {"vault_address": VAULT, "chain_id": 1},
    "vault_preflight": {
        "vault_address": VAULT,
        "calldata": "0x",
        "sender": VAULT,
        "chain_id": 1,
    },
    "vault_role_accounts": {"vault_address": VAULT, "chain_id": 1},
    "vault_oracle_mapping": {"vault_address": VAULT, "chain_id": 1},
    "vault_list": {},
    "vault_add": {"address": VAULT, "label": "Test", "chain_id": 1},
    "vault_remove": {"address": VAULT},
    "config_show": {},
    "config_set_provider": {"url": "https://rpc.example.com", "chain_id": 1},
    "config_set_etherscan_key": {"api_key": "a-key"},
    "market_morpho_blue": {"market_id": "0x" + "ab" * 32, "chain_id": 1},
    "market_meta_morpho": {"vault_address": VAULT, "chain_id": 1},
    "address_lookup": {"query": "PlasmaVault", "chain_id": 1},
    "address_names": {"chain_id": 1},
    "address_list": {"chain_id": 1},
    "fuse_whitelist": {"chain_id": 1, "market_id": 1},
}

# What each category means for one tool: the argument its payloads go
# through and the safe outcome, or a reason string starting with "n/a: ".
# TestCoverageGate holds this table to the live registry, both directions,
# and lets "n/a: takes no arguments" stand only for a tool whose schema
# really has no properties.
NO_ARGS = "n/a: takes no arguments — there is nothing to inject"

ABUSE: dict[str, dict[str, Inject | str]] = {
    # `since` must be a dotted version: anything else is refused by
    # read_changelog before it can widen the changelog slice.
    "server_info": {
        "ssrf": refused("since"),
        "traversal": refused("since"),
        "ssti": refused("since"),
        "attr_access": refused("since"),
        "enumeration": DERIVED,
    },
    "vault_info": {
        "ssrf": refused("vault_address"),
        "traversal": refused("vault_address"),
        "ssti": refused("vault_address"),
        "attr_access": refused("vault_address"),
        "enumeration": DERIVED,
    },
    "vault_validate": {
        "ssrf": refused("vault_address"),
        "traversal": refused("vault_address"),
        "ssti": refused("vault_address"),
        "attr_access": refused("vault_address"),
        "enumeration": DERIVED,
    },
    # calldata is parsed first, then sender, then the vault address — each
    # payload is refused before any context is built.
    "vault_preflight": {
        "ssrf": refused("vault_address"),
        "traversal": refused("calldata"),
        "ssti": refused("calldata"),
        "attr_access": refused("sender"),
        "enumeration": DERIVED,
    },
    "vault_role_accounts": {
        "ssrf": refused("vault_address"),
        "traversal": refused("vault_address"),
        "ssti": refused("vault_address"),
        "attr_access": refused("vault_address"),
        "enumeration": DERIVED,
    },
    "vault_oracle_mapping": {
        "ssrf": refused("vault_address"),
        "traversal": refused("vault_address"),
        "ssti": refused("vault_address"),
        "attr_access": refused("vault_address"),
        "enumeration": DERIVED,
    },
    "vault_list": {
        "ssrf": NO_ARGS,
        "traversal": NO_ARGS,
        "ssti": NO_ARGS,
        "attr_access": NO_ARGS,
        "enumeration": DERIVED,
    },
    "vault_add": {
        "ssrf": refused("address"),
        "traversal": refused("address"),
        "ssti": refused("address"),
        "attr_access": refused("address"),
        "enumeration": DERIVED,
    },
    # Removing an address that parses to no saved vault answers "Vault not
    # found." — data, no write, nothing fetched.
    "vault_remove": {
        "ssrf": inert("address"),
        "traversal": inert("address"),
        "ssti": inert("address"),
        "attr_access": inert("address"),
        "enumeration": DERIVED,
    },
    "config_show": {
        "ssrf": NO_ARGS,
        "traversal": NO_ARGS,
        "ssti": NO_ARGS,
        "attr_access": NO_ARGS,
        "enumeration": DERIVED,
    },
    # With chain_id given, the URL is written to the local config and nothing
    # ever connects to it during the call: the Web3 chain-id probe is the
    # documented behaviour of omitting chain_id, not of setting a URL.
    "config_set_provider": {
        "ssrf": inert("url", "save_config", stores=("save_config",)),
        "traversal": inert("url", "save_config", stores=("save_config",)),
        "ssti": inert("url", "save_config", stores=("save_config",)),
        "attr_access": inert("url", "save_config", stores=("save_config",)),
        "enumeration": DERIVED,
    },
    "config_set_etherscan_key": {
        "ssrf": inert("api_key", "save_config", stores=("save_config",)),
        "traversal": inert("api_key", "save_config", stores=("save_config",)),
        "ssti": inert("api_key", "save_config", stores=("save_config",)),
        "attr_access": inert("api_key", "save_config", stores=("save_config",)),
        "enumeration": DERIVED,
    },
    "market_morpho_blue": {
        "ssrf": refused("market_id"),
        "traversal": refused("market_id"),
        "ssti": refused("market_id"),
        "attr_access": refused("market_id"),
        "enumeration": DERIVED,
    },
    "market_meta_morpho": {
        "ssrf": refused("vault_address"),
        "traversal": refused("vault_address"),
        "ssti": refused("vault_address"),
        "attr_access": refused("vault_address"),
        "enumeration": DERIVED,
    },
    # The lookup accepts any string on purpose (substring search): the safe
    # outcome is a registry query that matches nothing, fetched by chain id.
    "address_lookup": {
        "ssrf": inert("query", "fetch_address_registries"),
        "traversal": inert("query", "fetch_address_registries"),
        "ssti": inert("query", "fetch_address_registries"),
        "attr_access": inert("query", "fetch_address_registries"),
        "enumeration": DERIVED,
    },
    # Integer-only tools: the only way to deliver a payload is a wrong-typed
    # value, which schema validation refuses before the body exists.
    "address_names": {
        "ssrf": refused("chain_id"),
        "traversal": refused("chain_id"),
        "ssti": refused("chain_id"),
        "attr_access": refused("chain_id"),
        "enumeration": DERIVED,
    },
    "address_list": {
        "ssrf": refused("chain_id"),
        "traversal": refused("chain_id"),
        "ssti": refused("chain_id"),
        "attr_access": refused("chain_id"),
        "enumeration": DERIVED,
    },
    "fuse_whitelist": {
        "ssrf": refused("market_id"),
        "traversal": refused("chain_id"),
        "ssti": refused("market_id"),
        "attr_access": refused("chain_id"),
        "enumeration": DERIVED,
    },
}

# Every prompt argument is injected with each payload and must come back
# literal. A prompt with no arguments declares () — the gate verifies that
# against the live registration instead of skipping it silently.
PROMPT_STRING_ARGS: dict[str, tuple[str, ...]] = {
    "quickstart": (),
    "deploy_vault": ("chain_id", "asset", "market"),
    "analyze_vault": ("chain_id", "vault_address"),
    "trace_oracle_pricing": ("chain_id", "vault_address", "asset"),
    "explain_fuse": ("chain_id", "fuse"),
}

# Tool names of IPOR's hosted MCP servers that this stdio server never
# registers — the campaign probed such names to map which server it had hit.
HOSTED_ONLY_TOOLS = (
    "vaults_list",
    "fusion_address_lookup",
    "fusion_addresses_list",
    "fusion_address_names",
    "docs_search",
    "docs_get",
    "db_query",
    "knowledge_search",
    "contract_source",
    "decode_transaction",
)


class Outbound:
    """Recording fakes for every outbound touchpoint and the config store."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, str]] = []

    def fake(self, name: str, *, result: Any = None, error: Exception | None = None):
        def _fake(*args: Any, **kwargs: Any) -> Any:
            self.calls.append((name, f"{args!r} {kwargs!r}"))
            if error is not None:
                raise error
            return result

        return _fake

    def assert_contained(
        self, payload: str, may_call: frozenset[str], stores: frozenset[str]
    ) -> None:
        called = {name for name, _ in self.calls}
        assert called <= may_call, f"unexpected outbound calls: {self.calls}"
        for name, described in self.calls:
            if name not in stores:
                assert payload not in described, (name, payload)


@pytest.fixture(name="outbound")
def _outbound(monkeypatch: pytest.MonkeyPatch) -> Outbound:
    recorder = Outbound()
    monkeypatch.setattr(
        mcp_server,
        "load_config",
        lambda: FusionConfig(providers={"1": "https://rpc.example.com"}),
    )
    monkeypatch.setattr(
        mcp_server, "save_config", recorder.fake("save_config", result=None)
    )
    for name in ("_build_ctx", "Web3", "Web3Context"):
        monkeypatch.setattr(
            mcp_server,
            name,
            recorder.fake(name, error=ValueError("no rpc in this test")),
        )
    for name in ("fetch_market", "fetch_vault"):
        monkeypatch.setattr(
            mcp_server,
            name,
            recorder.fake(name, error=ValueError("no morpho api in this test")),
        )
    monkeypatch.setattr(
        mcp_server,
        "fetch_address_registries",
        recorder.fake("fetch_address_registries", result=[]),
    )
    monkeypatch.setattr(
        mcp_server,
        "fetch_address_registry",
        recorder.fake(
            "fetch_address_registry", error=ValueError("no registry in this test")
        ),
    )
    monkeypatch.setattr(
        mcp_server,
        "fetch_fuse_whitelist_json",
        recorder.fake(
            "fetch_fuse_whitelist_json", error=ValueError("no chain in this test")
        ),
    )
    return recorder


def _payload_cases() -> list:
    return [
        pytest.param(
            tool, spec, category, payload, id=f"{tool}-{category}-{payload[:24]!r}"
        )
        for tool, categories in ABUSE.items()
        for category, spec in categories.items()
        if isinstance(spec, Inject)
        for payload in PAYLOADS[category]
    ]


class TestCampaignPayloads:
    """Every corpus payload against every tool, the way a client sends it:
    through ``call_tool``, schema validation included."""

    @staticmethod
    @pytest.mark.parametrize(("tool", "spec", "category", "payload"), _payload_cases())
    def test_payload_is_contained(tool, spec, category, payload, outbound):
        args = {**BASELINE[tool], spec.arg: payload}
        if spec.outcome == "refused":
            with pytest.raises(McpToolError) as refusal:
                asyncio.run(mcp_server.mcp.call_tool(tool, args))
            if category == "ssti":
                assert "49" not in str(refusal.value)
        else:
            result = asyncio.run(mcp_server.mcp.call_tool(tool, args))
            if category == "ssti":
                assert "49" not in str(result)
        outbound.assert_contained(payload, spec.may_call, spec.stores)

    @staticmethod
    def test_a_stored_provider_url_is_never_fetched_on_set(outbound):
        """The SSRF case pinned by name: with chain_id given, setting a
        provider URL writes config and connects to nothing."""
        url = "http://169.254.169.254/latest/meta-data/"
        asyncio.run(
            mcp_server.mcp.call_tool("config_set_provider", {"url": url, "chain_id": 1})
        )
        called = {name for name, _ in outbound.calls}
        assert called == {"save_config"}

    @staticmethod
    def test_lookup_answers_a_payload_query_with_no_match(outbound):
        """The inert outcome, pinned on the body's own answer once."""
        result = mcp_server.address_lookup(query="{{7*7}}", chain_id=1)
        assert result.query == "{{7*7}}"
        assert not result.matches
        outbound.assert_contained(
            "{{7*7}}", frozenset({"fetch_address_registries"}), frozenset()
        )


def _registered_tool_names() -> list[str]:
    return sorted(t.name for t in mcp_server.mcp._tool_manager.list_tools())


def _tool_name_probes() -> list[str]:
    """Hosted-only names the campaign sent, dunders, and a near-miss variant
    of every registered name — none may resolve."""
    names = set(_registered_tool_names())
    probes = set(HOSTED_ONLY_TOOLS) | {"__import__", "__class__", "system"}
    for name in names:
        probes.add(name.upper())
        probes.add(name + "_")
    return sorted(probes - names)


def _resource_probes() -> list[str]:
    uris = [doc.uri for doc in RESOURCES]
    probes = {"fusion:/etc/passwd", "file:///etc/passwd"}
    for uri in uris:
        probes.add("fusion://" + uri.removeprefix("fusion://").upper())
        probes.add(uri + "?x=1")
        probes.add(uri + "/../../../etc/passwd")
    return sorted(probes - set(uris))


def _prompt_name_probes() -> list[str]:
    names = set(PROMPT_STRING_ARGS)
    probes = {"__import__"}
    for name in names:
        probes.add(name.upper())
        probes.add(name + "_")
    return sorted(probes - names)


class TestEnumeration:
    """Name probing: tools, resources and prompts resolve by exact
    registered name only, so every near miss and every hosted-only name the
    campaign sent is refused."""

    @staticmethod
    @pytest.mark.parametrize("name", _tool_name_probes())
    def test_an_unregistered_tool_name_is_refused(name, outbound):
        with pytest.raises(McpToolError, match="Unknown tool"):
            asyncio.run(mcp_server.mcp.call_tool(name, {}))
        outbound.assert_contained(name, frozenset(), frozenset())

    @staticmethod
    @pytest.mark.parametrize("uri", _resource_probes())
    def test_an_unregistered_resource_uri_is_refused(uri, outbound):
        with pytest.raises(ValueError, match="Unknown resource"):
            asyncio.run(mcp_server.mcp.read_resource(uri))
        outbound.assert_contained(uri, frozenset(), frozenset())

    @staticmethod
    @pytest.mark.parametrize("name", _prompt_name_probes())
    def test_an_unregistered_prompt_name_is_refused(name, outbound):
        with pytest.raises(ValueError, match="Unknown prompt"):
            asyncio.run(mcp_server.mcp.get_prompt(name, None))
        outbound.assert_contained(name, frozenset(), frozenset())


_PROMPT_PAYLOADS = [
    pytest.param(payload, category, id=f"{category}-{payload[:24]!r}")
    for category in ("ssti", "traversal", "attr_access")
    for payload in PAYLOADS[category]
]


class TestPromptInjection:
    """Prompt arguments are interpolated into plain f-strings: whatever the
    client sends comes back literal, never evaluated."""

    @staticmethod
    @pytest.mark.parametrize(
        ("name", "args"),
        [(n, a) for n, a in PROMPT_STRING_ARGS.items() if a],
    )
    @pytest.mark.parametrize(("payload", "category"), _PROMPT_PAYLOADS)
    def test_prompt_arguments_stay_literal(name, args, payload, category):
        result = asyncio.run(
            mcp_server.mcp.get_prompt(name, {arg: payload for arg in args})
        )
        text = result.messages[0].content.text  # type: ignore[union-attr]
        assert payload in text
        if category == "ssti":
            assert "49" not in text


class TestCoverageGate:
    """A tool, prompt or resource without abuse coverage for a category
    fails here — not in review."""

    @staticmethod
    def test_every_tool_has_every_category():
        live = set(_registered_tool_names())
        assert set(ABUSE) == live
        assert set(BASELINE) == live
        for tool, categories in ABUSE.items():
            assert tuple(categories) == CATEGORIES, tool
            for category, spec in categories.items():
                if isinstance(spec, Inject):
                    assert spec.outcome in ("refused", "inert"), (tool, category)
                    continue
                declared = spec == DERIVED or spec.startswith("n/a: ")
                assert declared, f"{tool}/{category}: case missing and not declared"

    @staticmethod
    def test_every_injected_argument_is_real():
        """The table cannot drift from a renamed or removed argument, and
        "takes no arguments" must be literally true."""
        schemas = {
            t.name: set(t.parameters.get("properties", {}))
            for t in mcp_server.mcp._tool_manager.list_tools()
        }
        for tool, categories in ABUSE.items():
            for category, spec in categories.items():
                if isinstance(spec, Inject):
                    assert spec.arg in schemas[tool], (tool, category, spec.arg)
                elif spec == NO_ARGS:
                    assert not schemas[tool], (tool, category)
            for arg in BASELINE[tool]:
                assert arg in schemas[tool], (tool, arg)

    @staticmethod
    def test_every_prompt_is_declared():
        live = {p.name: p for p in asyncio.run(mcp_server.mcp.list_prompts())}
        assert set(PROMPT_STRING_ARGS) == set(live)
        for name, injected in PROMPT_STRING_ARGS.items():
            registered = {a.name for a in (live[name].arguments or [])}
            # Every argument is injected; () must mean "really has none".
            assert set(injected) == registered, name

    @staticmethod
    def test_every_resource_is_probed():
        # Probes derive from RESOURCES: equality means every served resource
        # has case-flip, query and traversal variants in TestEnumeration.
        listed = {str(r.uri) for r in asyncio.run(mcp_server.mcp.list_resources())}
        assert listed == {doc.uri for doc in RESOURCES}

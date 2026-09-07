"""Tests for the shipped agent guide and the skill that embeds it."""

import ast
import json
import re
from pathlib import Path

import pytest
from _euler_v2 import BASE_FUSION_FACTORY
from _guide import GUIDE_ADDRESSES
from addresses import BASE_USDC

from ipor_fusion import ERC20, AccessManager, Call, PlasmaVault
from ipor_fusion.core import FusionFactory
from ipor_fusion.fuses import AaveV3SupplyFuse
from ipor_fusion.guide import (
    ARCHITECTURE,
    GLOSSARY,
    INVARIANTS,
    QUICKSTART,
    RESOURCES,
    guide_text,
)

REPO_ROOT = Path(__file__).resolve().parents[1]
SKILL_DIR = REPO_ROOT / "skills" / "ipor-deploy-vault"
SKILL = SKILL_DIR / "SKILL.md"
PLUGIN = REPO_ROOT / ".claude-plugin" / "plugin.json"
MARKETPLACE = REPO_ROOT / ".claude-plugin" / "marketplace.json"
MCP_CONFIG = REPO_ROOT / ".mcp.json"

# Everything in this file is published as-is to whoever installs the package or
# the plugin, so every link must point at a public surface.
PUBLIC_HOSTS = {
    "ipor.io",
    "docs.ipor.io",
    "mcp.ipor.io",
    "github.com",
    "agentskills.io",
    "foundry.paradigm.xyz",
    "localhost",
}
PUBLIC_REPOS = {
    "ipor-fusion.py",
    "ipor-fusion",
    "ipor-abi",
    "ipor-fusion-alpha-example",
}
_URL = re.compile(r"https?://([A-Za-z0-9.-]+)(?::\d+)?(/[^\s)`>\"']*)?")
_FRONTMATTER = re.compile(r"\A---\n(.*?)\n---\n", re.DOTALL)
_PY_BLOCK = re.compile(r"```python\n(.*?)```", re.DOTALL)


def _public_texts() -> dict[str, str]:
    texts = {doc.uri: doc.text for doc in RESOURCES}
    texts[str(SKILL)] = SKILL.read_text(encoding="utf-8")
    texts[str(PLUGIN)] = PLUGIN.read_text(encoding="utf-8")
    texts[str(MARKETPLACE)] = MARKETPLACE.read_text(encoding="utf-8")
    texts[str(MCP_CONFIG)] = MCP_CONFIG.read_text(encoding="utf-8")
    return texts


class TestResources:
    def test_four_documents_under_the_fusion_scheme(self):
        assert RESOURCES == (GLOSSARY, ARCHITECTURE, INVARIANTS, QUICKSTART)
        assert [doc.uri for doc in RESOURCES] == [
            "fusion://glossary",
            "fusion://architecture",
            "fusion://invariants",
            "fusion://quickstart",
        ]

    @pytest.mark.parametrize("doc", RESOURCES, ids=lambda d: d.name)
    def test_document_is_markdown_with_a_title(self, doc):
        assert doc.mime_type == "text/markdown"
        assert doc.text.startswith("# IPOR Fusion ")
        assert doc.description

    def test_guide_text_by_name(self):
        assert guide_text("invariants") == INVARIANTS.text
        with pytest.raises(KeyError):
            guide_text("changelog")

    def test_walk_addresses_match_the_repository_constants(self):
        # The fuse pair is deployment-specific and exercised by the from-scratch
        # simulation; the factory and the token must simply agree with the
        # constants the rest of the suite runs against.
        assert GUIDE_ADDRESSES["FACTORY_PROXY"] == BASE_FUSION_FACTORY
        assert GUIDE_ADDRESSES["USDC"] == BASE_USDC
        assert {"SUPPLY_FUSE", "BALANCE_FUSE"} <= set(GUIDE_ADDRESSES)

    def test_invariants_name_every_deploy_path_revert(self):
        text = INVARIANTS.text
        for selector, name in [
            ("0x8745fbfd", "DaoFeePackagesArrayEmpty"),
            ("0x9996b315", "AddressEmptyCode"),
            ("0x068ca9d8", "AccessManagedUnauthorized"),
        ]:
            assert selector in text
            assert name in text


class TestPublicSurface:
    @pytest.mark.parametrize("source", sorted(_public_texts()))
    def test_links_only_public_hosts_and_repos(self, source):
        text = _public_texts()[source]
        for match in _URL.finditer(text):
            host, path = match.group(1), match.group(2) or ""
            assert host in PUBLIC_HOSTS, f"{source}: {match.group(0)}"
            if host == "github.com":
                owner, _, repo = path.strip("/").partition("/")
                repo = repo.split("/")[0]
                assert owner == "IPOR-Labs", f"{source}: {match.group(0)}"
                assert repo in PUBLIC_REPOS, f"{source}: {match.group(0)}"


class TestSkill:
    def test_frontmatter_name_matches_directory(self):
        match = _FRONTMATTER.match(SKILL.read_text(encoding="utf-8"))
        assert match, "SKILL.md must start with YAML frontmatter"
        fields = dict(
            line.split(":", 1)
            for line in match.group(1).splitlines()
            if ":" in line and not line.startswith(" ")
        )
        assert fields["name"].strip() == SKILL_DIR.name
        assert len(fields["description"].strip()) > 100

    @pytest.mark.parametrize("doc", [INVARIANTS, QUICKSTART], ids=lambda d: d.name)
    def test_embeds_the_guide_documents_verbatim(self, doc):
        # One text, two carriers: the resource served over MCP and the skill.
        # Anyone editing one is forced by this test to edit the other.
        body = doc.text.split("\n", 1)[1].strip()
        assert body in SKILL.read_text(encoding="utf-8")

    def test_walk_is_valid_python_using_the_public_api(self):
        blocks = _PY_BLOCK.findall(QUICKSTART.text)
        assert len(blocks) == 1
        assert _PY_BLOCK.findall(SKILL.read_text(encoding="utf-8")) == blocks
        tree = ast.parse(blocks[0])
        calls = {
            node.func.attr
            for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        # Every step the walk depends on, checked against the real classes:
        # renaming one in the SDK fails here instead of shipping a guide that
        # tells an agent to call a method that no longer exists.
        api = (FusionFactory, PlasmaVault, AccessManager, ERC20, AaveV3SupplyFuse, Call)
        for step in [
            "clone",
            "grant_role",
            "add_fuses",
            "grant_market_substrates",
            "add_balance_fuse",
            "approve",
            "deposit",
            "execute",
            "send",
        ]:
            assert step in calls, step
            assert any(hasattr(cls, step) for cls in api), step

    def test_lists_every_shipped_resource_uri(self):
        # The skill points a reader at the MCP resources; a new document that
        # never reaches this list is a document nobody finds.
        text = SKILL.read_text(encoding="utf-8")
        assert {r.uri for r in RESOURCES if r.uri in text} == {r.uri for r in RESOURCES}


class TestPluginManifests:
    def test_plugin_version_is_the_package_version(self):
        pyproject = (REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8")
        match = re.search(r'^version = "([^"]+)"$', pyproject, re.MULTILINE)
        assert match
        plugin = json.loads(PLUGIN.read_text(encoding="utf-8"))
        assert plugin["version"] == match.group(1)

    def test_marketplace_lists_the_plugin_at_the_repo_root(self):
        plugin = json.loads(PLUGIN.read_text(encoding="utf-8"))
        marketplace = json.loads(MARKETPLACE.read_text(encoding="utf-8"))
        assert [p["name"] for p in marketplace["plugins"]] == [plugin["name"]]
        assert marketplace["plugins"][0]["source"] == "./"

    def test_mcp_config_ships_the_hosted_and_the_bundled_server(self):
        # The hosted server inspects live vaults; only the bundled one serves
        # the `fusion://` resources and the prompts, so the plugin needs both.
        config = json.loads(MCP_CONFIG.read_text(encoding="utf-8"))
        assert config["mcpServers"] == {
            "ipor-fusion": {"type": "http", "url": "https://mcp.ipor.io/mcp"},
            "ipor-fusion-local": {"type": "stdio", "command": "fusion-mcp"},
        }

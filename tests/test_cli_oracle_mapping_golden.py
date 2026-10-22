"""`fusion vault oracle-mapping --json` and the MCP `vault_oracle_mapping` tool
pinned byte for byte.

The resolver runs for real over a `FakeReader` holding one asset per
`source_detail` kind: a confirmed Chainlink aggregator, a Chainlink-style
leaf, a dual cross-reference feed (resolved and with an unreadable
component), an ERC4626 feed (resolved and over a partial underlying), a Morpho
collateral feed, middleware fallbacks (a manager delegating to a middleware
priced by its feed registry, and one whose price is unreadable), an unknown
custom feed and an asset without a source. The CLI's `--json` output
(`mapping.json`) and the MCP tool's text (`mapping.mcp.json`) live in
`tests/fixtures/oracle_mapping_golden/`; regenerate them with
`UPDATE_GOLDEN=1 uv run pytest tests/test_cli_oracle_mapping_golden.py` only
when an output change is intended (see `tests/_golden.py`).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from unittest.mock import MagicMock, patch

import pytest
from _golden import FIXTURES as GOLDEN_FIXTURES
from _golden import assert_golden, mcp_tool_text
from _oracle_mapping_fake import FakeReader, FakeVault, addr, chainlink_asset
from click.testing import CliRunner
from web3 import Web3

from ipor_fusion.cli import config_store
from ipor_fusion.cli.config_store import FusionConfig, save_config
from ipor_fusion.cli.main import cli
from ipor_fusion.core.context import Web3Context
from ipor_fusion.mcp.models import OracleMappingResponse
from ipor_fusion.readers import oracle_mapping as om
from ipor_fusion.types import ZERO_ADDRESS, Price

FIXTURES = GOLDEN_FIXTURES / "oracle_mapping_golden"

VAULT = addr(0xAA)
ORACLE = addr(0x99)
BLOCK = 21_000_000

USDC = addr(0x01)
DAI = addr(0x02)
WSTETH = addr(0x03)
WSR = addr(0x04)
RUSD = addr(0x05)
COLL = addr(0x06)
WETH = addr(0x07)
BROKEN_XREF = addr(0x08)
STAKED_UNKNOWN = addr(0x09)
WAT = addr(0x0A)
LOST = addr(0x0B)
UNKNOWN_UNDERLYING = addr(0x0C)

MIDDLEWARE = addr(0x51)
REGISTRY = addr(0x61)


def _reader() -> FakeReader:
    r = FakeReader()
    zero = Web3.to_checksum_address(ZERO_ADDRESS)

    chainlink_asset(r, USDC, addr(0x11), symbol="USDC")
    r.feeds[addr(0x11)].update(
        {
            "aggregator": addr(0x71),
            "phase_id": 6,
            "round": (
                6 << 64 | 42,
                99_980_000,
                1_699_999_000,
                1_700_000_000,
                6 << 64 | 42,
            ),
        }
    )

    # Answers latestRoundData but with a synthetic zero updatedAt and no
    # version(): only Chainlink-style.
    r.symbols[DAI] = "DAI"
    r.decimals[DAI] = 18
    r.sources[DAI] = addr(0x12)
    r.prices[DAI] = Price(asset=DAI, amount=100_010_000, decimals=8)
    r.feeds[addr(0x12)] = {
        "round": (0, 100_010_000, 0, 0, 0),
        "feed_decimals": 8,
        "description": "",
    }

    r.symbols[WSTETH] = "wstETH"
    r.decimals[WSTETH] = 18
    r.sources[WSTETH] = addr(0x13)
    r.prices[WSTETH] = Price(asset=WSTETH, amount=2_600 * 10**8, decimals=8)
    r.feeds[addr(0x13)] = {
        "round": (1, 2_600 * 10**8, 0, 0, 1),
        "feed_decimals": 8,
        "asset_x": WSTETH,
        "xy_feed": addr(0x14),
        "y_usd_feed": addr(0x15),
    }
    r.feeds[addr(0x14)] = {
        "round": (7, 13 * 10**17, 1_699_990_000, 1_700_000_000, 7),
        "feed_decimals": 18,
        "description": "wstETH / stETH",
    }
    r.feeds[addr(0x15)] = {
        "round": (9, 2_000 * 10**8, 1_699_990_000, 1_700_000_000, 9),
        "feed_decimals": 8,
        "description": "ETH / USD",
    }

    r.symbols[BROKEN_XREF] = "xETH"
    r.decimals[BROKEN_XREF] = 18
    r.sources[BROKEN_XREF] = addr(0x16)
    r.prices[BROKEN_XREF] = Price(asset=BROKEN_XREF, amount=2_000 * 10**8, decimals=8)
    r.feeds[addr(0x16)] = {
        "asset_x": BROKEN_XREF,
        "xy_feed": addr(0x14),
        "y_usd_feed": addr(0x17),
    }

    r.symbols[WSR] = "wsrUSD"
    r.decimals[WSR] = 18
    r.sources[WSR] = addr(0x18)
    r.prices[WSR] = Price(asset=WSR, amount=104 * 10**16, decimals=18)
    r.feeds[addr(0x18)] = {"vault": addr(0x21)}
    r.vaults[addr(0x21)] = {"asset": RUSD, "decimals": 18, "rate": 104 * 10**16}
    chainlink_asset(r, RUSD, addr(0x19), symbol="rUSD")

    r.symbols[STAKED_UNKNOWN] = "sWAT"
    r.decimals[STAKED_UNKNOWN] = 18
    r.sources[STAKED_UNKNOWN] = addr(0x1A)
    r.prices[STAKED_UNKNOWN] = Price(asset=STAKED_UNKNOWN, amount=10**18, decimals=18)
    r.feeds[addr(0x1A)] = {"vault": addr(0x22)}
    r.vaults[addr(0x22)] = {
        "asset": UNKNOWN_UNDERLYING,
        "decimals": 18,
        "rate": 10**18,
    }
    r.symbols[UNKNOWN_UNDERLYING] = "uWAT"
    r.decimals[UNKNOWN_UNDERLYING] = 18
    r.sources[UNKNOWN_UNDERLYING] = addr(0x1B)
    r.feeds[addr(0x1B)] = {}

    r.symbols[COLL] = "PT-USDe"
    r.decimals[COLL] = 18
    r.sources[COLL] = addr(0x1C)
    r.prices[COLL] = Price(asset=COLL, amount=97 * 10**6, decimals=8)
    r.feeds[addr(0x1C)] = {
        "morpho_oracle": addr(0x31),
        "collateral": COLL,
        "loan": USDC,
    }
    r.morpho_prices[addr(0x31)] = 970_000 * 10**18

    r.symbols[WAT] = "WAT"
    r.decimals[WAT] = 18
    r.sources[WAT] = addr(0x1D)
    r.feeds[addr(0x1D)] = {}

    # A price-oracle manager: zero-source assets delegate to the global
    # middleware, which prices WETH from its feed registry and LOST not at all.
    r.underlying_mw = MIDDLEWARE
    r.symbols[WETH] = "WETH"
    r.decimals[WETH] = 18
    r.sources[WETH] = zero
    r.prices[WETH] = Price(asset=WETH, amount=2_000 * 10**18, decimals=18)
    r.symbols[LOST] = "LOST"
    r.decimals[LOST] = 18
    delegated = FakeReader()
    delegated.symbols[WETH] = "WETH"
    delegated.decimals[WETH] = 18
    delegated.prices[WETH] = Price(asset=WETH, amount=2_000 * 10**8, decimals=8)
    delegated.registry = REGISTRY
    delegated.symbols[LOST] = "LOST"
    delegated.decimals[LOST] = 18
    r.delegated = delegated

    r.configured = [
        USDC,
        DAI,
        WSTETH,
        BROKEN_XREF,
        WSR,
        STAKED_UNKNOWN,
        COLL,
        WAT,
        WETH,
        LOST,
    ]
    return r


@pytest.fixture(autouse=True)
def recorded_oracle(tmp_path, monkeypatch) -> Iterator[None]:
    """A saved provider whose vault prices through `_reader()`."""
    monkeypatch.setattr(config_store, "CONFIG_DIR", tmp_path / ".fusion")
    monkeypatch.setattr(config_store, "CONFIG_FILE", tmp_path / ".fusion" / "c.json")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    save_config(FusionConfig(providers={"1": "https://rpc.example.invalid"}))
    monkeypatch.setattr(
        Web3Context, "from_url", staticmethod(lambda *_, **__: MagicMock())
    )
    with (
        patch.object(om, "PlasmaVault", return_value=FakeVault(ORACLE, USDC, "Golden")),
        patch.object(om, "OracleMappingReader", return_value=_reader()),
        patch("ipor_fusion.cli.vault_cmd.resolve_access_manager"),
        patch("ipor_fusion.mcp.server.resolve_access_manager"),
    ):
        yield


def test_fixture_covers_every_source_detail_kind() -> None:
    document = json.loads((FIXTURES / "mapping.json").read_text("utf-8"))
    nodes = list(document["configured_assets"])
    kinds: set[str] = set()
    statuses: set[str] = set()
    while nodes:
        node = nodes.pop()
        kinds.add(node["source_type"])
        statuses.add(node["status"])
        nodes.extend(node["dependencies"])
    assert kinds == {
        om.TYPE_CHAINLINK,
        om.TYPE_CHAINLINK_STYLE,
        om.TYPE_DUAL_XREF,
        om.TYPE_ERC4626,
        om.TYPE_MORPHO,
        om.TYPE_MIDDLEWARE_FALLBACK,
        om.TYPE_UNKNOWN,
    }
    assert statuses == {"resolved", "partially_resolved", "partial"}
    assert document["status"] == "partially_resolved"
    assert {node["reason"] for node in document["unresolved"]} == {
        "dual_xref_component_unreadable",
        "unsupported_custom_feed",
        "manager_price_unreadable",
        "no_source_configured",
    }


def test_cli_json_matches_fixture() -> None:
    result = CliRunner().invoke(
        cli,
        [
            "vault",
            "oracle-mapping",
            VAULT,
            "--chain-id",
            "1",
            "--block-number",
            str(BLOCK),
            "--json",
        ],
    )

    assert result.exit_code == 0, result.output
    assert_golden(FIXTURES / "mapping.json", result.output)


def test_mcp_text_matches_fixture() -> None:
    text = mcp_tool_text(
        "vault_oracle_mapping",
        {"vault_address": VAULT, "chain_id": 1, "block_number": BLOCK},
        OracleMappingResponse,
    )

    assert_golden(FIXTURES / "mapping.mcp.json", text)
    cli_document = json.loads((FIXTURES / "mapping.json").read_text("utf-8"))
    assert (
        OracleMappingResponse.model_validate(cli_document).model_dump_json(indent=2)
        == text
    )

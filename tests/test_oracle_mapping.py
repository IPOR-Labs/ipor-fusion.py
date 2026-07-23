"""Unit tests for the vault oracle-mapping reader.

The pure logic (event collapse, wad normalization, recursive resolution) is
tested against a duck-typed ``FakeReader`` — no network, no RPC. The
``OracleMappingReader`` probe methods run against a mocked ``Web3Context``
returning ABI-encoded bytes (same pattern as ``test_reader_encoding.py``).
"""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock, patch

from eth_abi import encode
from web3 import Web3

from ipor_fusion.readers import oracle_mapping as om
from ipor_fusion.types import NodeStatus, Price


def addr(n: int) -> str:
    return Web3.to_checksum_address(f"0x{n:040x}")


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


class TestCollapseSources:
    def test_single(self):
        a, s = addr(1), addr(0x11)
        assert om.collapse_sources([(100, a, s)]) == {a: s}

    def test_repoint_latest_block_wins(self):
        a, s1, s2 = addr(1), addr(0x11), addr(0x12)
        events = [(100, a, s1), (200, a, s2)]
        assert om.collapse_sources(events) == {a: s2}

    def test_zero_address_removes_asset(self):
        a, s1 = addr(1), addr(0x11)
        zero = Web3.to_checksum_address(om.ZERO_ADDRESS)
        events = [(100, a, s1), (300, a, zero)]
        assert om.collapse_sources(events) == {}

    def test_out_of_block_order(self):
        a, s1, s2 = addr(1), addr(0x11), addr(0x12)
        # higher-block event listed first; lower-block must not override it
        events = [(200, a, s2), (100, a, s1)]
        assert om.collapse_sources(events) == {a: s2}


class TestNormalizeWad:
    def test_fewer_decimals(self):
        assert om.normalize_wad(100, 6) == str(100 * 10**12)

    def test_exactly_wad(self):
        assert om.normalize_wad(123, 18) == "123"

    def test_more_decimals(self):
        assert om.normalize_wad(10**27, 27) == str(10**18)

    def test_more_decimals_truncates_sub_wad_remainder(self):
        # 20 decimals → divide by 10**2; the trailing "45" is below the 18th
        # decimal and is floored away (lossy by design).
        assert om.normalize_wad(12_345, 20) == "123"


# ---------------------------------------------------------------------------
# Fake reader for resolver tests
# ---------------------------------------------------------------------------


class FakeReader:
    """In-memory stand-in for :class:`oracle_mapping.OracleMappingReader`."""

    def __init__(self) -> None:
        self.symbols: dict[str, str] = {}
        self.decimals: dict[str, int] = {}
        self.prices: dict[str, Price] = {}
        self.sources: dict[str, str] = {}
        self.feeds: dict[str, dict[str, Any]] = {}
        self.vaults: dict[str, dict[str, Any]] = {}
        self.morpho_prices: dict[str, int] = {}
        # oracle variant (middleware-fallback resolution); "_mw" because the
        # exact method name would be shadowed by the attribute
        self.underlying_mw: str | None = None
        self.registry: str | None = None
        self.delegated: FakeReader | None = None
        # enumeration (configured_assets / asset_source_events)
        self.configured: list[str] | None = None
        self.events: list[tuple[int, str, str]] = []
        self.events_to_block: int | None = None

    # token metadata
    def symbol(self, a: str) -> str | None:
        return self.symbols.get(a)

    def token_decimals(self, a: str) -> int | None:
        return self.decimals.get(a)

    # oracle
    def asset_price(self, a: str) -> Price | None:
        return self.prices.get(a)

    def source_of(self, a: str) -> str | None:
        return self.sources.get(a)

    # oracle variant probes
    def underlying_middleware(self) -> str | None:
        return self.underlying_mw

    def chainlink_feed_registry(self) -> str | None:
        return self.registry

    def delegate(self, oracle: str) -> FakeReader:
        assert oracle == self.underlying_mw  # must delegate to the probed middleware
        assert self.delegated is not None  # fail fast on a misconfigured test
        return self.delegated

    # feed probes
    def _f(self, s: str) -> dict[str, Any]:
        return self.feeds.get(s, {})

    def feed_morpho_oracle(self, s: str) -> str | None:
        return self._f(s).get("morpho_oracle")

    def feed_collateral_token(self, s: str) -> str | None:
        return self._f(s).get("collateral")

    def feed_loan_token(self, s: str) -> str | None:
        return self._f(s).get("loan")

    def feed_vault(self, s: str) -> str | None:
        return self._f(s).get("vault")

    def feed_latest_round_data(self, s: str):
        return self._f(s).get("round")

    def feed_decimals(self, s: str) -> int | None:
        return self._f(s).get("feed_decimals")

    def feed_description(self, s: str) -> str | None:
        return self._f(s).get("description")

    def feed_version(self, s: str) -> int | None:
        return self._f(s).get("version")

    def feed_aggregator(self, s: str) -> str | None:
        return self._f(s).get("aggregator")

    def feed_phase_id(self, s: str) -> int | None:
        return self._f(s).get("phase_id")

    def morpho_oracle_price(self, o: str) -> int | None:
        return self.morpho_prices.get(o)

    def feed_asset_x(self, s: str) -> str | None:
        return self._f(s).get("asset_x")

    def feed_asset_x_asset_y_feed(self, s: str) -> str | None:
        return self._f(s).get("xy_feed")

    def feed_asset_y_usd_feed(self, s: str) -> str | None:
        return self._f(s).get("y_usd_feed")

    # erc4626 vault
    def vault_asset(self, v: str) -> str | None:
        return self.vaults.get(v, {}).get("asset")

    def vault_decimals(self, v: str) -> int | None:
        return self.vaults.get(v, {}).get("decimals")

    def vault_convert_to_assets(self, v: str, shares: int) -> int | None:
        return self.vaults.get(v, {}).get("rate")

    # enumeration
    def configured_assets(self) -> list[str] | None:
        return self.configured

    def asset_source_events(self, to_block: int) -> list[tuple[int, str, str]]:
        self.events_to_block = to_block
        return self.events


def _chainlink_asset(r: FakeReader, asset: str, source: str, *, symbol: str) -> None:
    r.symbols[asset] = symbol
    r.decimals[asset] = 6
    r.sources[asset] = source
    r.prices[asset] = Price(asset=asset, amount=99_980_000, decimals=8)
    # full confirmed-tier evidence: description + version on top of the round
    r.feeds[source] = {
        "round": (1, 99_980_000, 0, 1_700_000_000, 1),
        "feed_decimals": 8,
        "description": f"{symbol} / USD",
        "version": 4,
    }


# ---------------------------------------------------------------------------
# Resolver
# ---------------------------------------------------------------------------


class TestResolve:
    def test_chainlink_leaf(self):
        r = FakeReader()
        usdc, feed = addr(1), addr(0x11)
        _chainlink_asset(r, usdc, feed, symbol="USDC")

        node = om.resolve_asset(r, usdc, max_depth=6)

        assert node.status == "resolved"
        assert node.source_type == om.TYPE_CHAINLINK
        assert node.path == ["USDC", "Chainlink feed"]
        assert node.price == om.OraclePrice(
            raw="99980000",
            decimals=8,
            normalized_wad=str(99_980_000 * 10**10),
        )
        assert node.source_detail == {
            "description": "USDC / USD",
            "round_id": "1",
            "answer": "99980000",
            "decimals": 8,
            "started_at": 0,
            "started_at_utc": None,  # synthetic epoch 0 has no UTC form
            "updated_at": 1_700_000_000,
            "updated_at_utc": "2023-11-14T22:13:20Z",
            "answered_in_round": "1",
            "aggregator": None,
            "phase_id": None,
        }
        assert node.dependencies == []

    def test_chainlink_leaf_without_description_reports_null(self):
        # description() is optional in practice (composed feeds may not
        # implement it) — null in the uniform block, still resolved, but the
        # confirmed-tier gate is incomplete → chainlink_style
        r = FakeReader()
        usdc, feed = addr(1), addr(0x11)
        _chainlink_asset(r, usdc, feed, symbol="USDC")
        del r.feeds[feed]["description"]

        node = om.resolve_asset(r, usdc, max_depth=6)

        assert node.status == "resolved"
        assert node.source_type == om.TYPE_CHAINLINK_STYLE
        assert node.reason is None
        assert node.source_detail is not None
        assert node.source_detail["description"] is None
        assert node.source_detail["answer"] == "99980000"

    def test_chainlink_missing_version_demoted_to_style(self):
        # version() is the last gate probe — without it the full
        # AggregatorV3Interface is unproven; demotion withholds the confirmed
        # label only, the node stays resolved with no reason
        r = FakeReader()
        usdc, feed = addr(1), addr(0x11)
        _chainlink_asset(r, usdc, feed, symbol="USDC")
        del r.feeds[feed]["version"]

        node = om.resolve_asset(r, usdc, max_depth=6)

        assert node.source_type == om.TYPE_CHAINLINK_STYLE
        assert node.path == ["USDC", "Chainlink-style feed"]
        assert node.status == "resolved"
        assert node.reason is None

    def test_chainlink_missing_decimals_demoted_to_style(self):
        # decimals() is part of the confirmed-tier gate; without it the answer
        # cannot even be scaled — but the node is still resolved (the
        # authoritative middleware price is what consumers use)
        r = FakeReader()
        usdc, feed = addr(1), addr(0x11)
        _chainlink_asset(r, usdc, feed, symbol="USDC")
        del r.feeds[feed]["feed_decimals"]

        node = om.resolve_asset(r, usdc, max_depth=6)

        assert node.source_type == om.TYPE_CHAINLINK_STYLE
        assert node.status == "resolved"
        assert node.reason is None
        assert node.source_detail is not None
        assert node.source_detail["decimals"] is None

    def test_chainlink_degenerate_round_demoted_to_style(self):
        # zero roundId / updatedAt are synthetic values wrapper feeds return;
        # real aggregators never do — distinct from staleness judgment
        for rnd in [(1, 99_980_000, 0, 0, 1), (0, 99_980_000, 0, 1_700_000_000, 0)]:
            r = FakeReader()
            usdc, feed = addr(1), addr(0x11)
            _chainlink_asset(r, usdc, feed, symbol="USDC")
            r.feeds[feed]["round"] = rnd

            node = om.resolve_asset(r, usdc, max_depth=6)

            assert node.source_type == om.TYPE_CHAINLINK_STYLE
            assert node.status == "resolved"
            assert node.reason is None

    def test_chainlink_empty_description_demoted_to_style(self):
        # description() answering "" is evidence against a real aggregator;
        # the raw value is still echoed in the metadata block
        r = FakeReader()
        usdc, feed = addr(1), addr(0x11)
        _chainlink_asset(r, usdc, feed, symbol="USDC")
        r.feeds[feed]["description"] = ""

        node = om.resolve_asset(r, usdc, max_depth=6)

        assert node.source_type == om.TYPE_CHAINLINK_STYLE
        assert node.status == "resolved"
        assert node.source_detail is not None
        assert node.source_detail["description"] == ""

    def test_chainlink_proxy_evidence_recorded(self):
        # aggregator()/phaseId() answering is evidence, not a gate — recorded
        # in source_detail, the tier is unchanged
        r = FakeReader()
        usdc, feed = addr(1), addr(0x11)
        aggregator = addr(0x71)
        _chainlink_asset(r, usdc, feed, symbol="USDC")
        r.feeds[feed]["aggregator"] = aggregator
        r.feeds[feed]["phase_id"] = 3

        node = om.resolve_asset(r, usdc, max_depth=6)

        assert node.source_type == om.TYPE_CHAINLINK
        assert node.source_detail is not None
        assert node.source_detail["aggregator"] == aggregator
        assert node.source_detail["phase_id"] == 3

    def test_negative_answer_serialized_as_string(self):
        # int256 answers can be negative; serialization must keep the sign
        # (the answer's value is never judged, only echoed)
        r = FakeReader()
        usdc, feed = addr(1), addr(0x11)
        _chainlink_asset(r, usdc, feed, symbol="USDC")
        r.feeds[feed]["round"] = (1, -5, 0, 1_700_000_000, 1)

        node = om.resolve_asset(r, usdc, max_depth=6)

        assert node.source_type == om.TYPE_CHAINLINK
        assert node.source_detail is not None
        assert node.source_detail["answer"] == "-5"

    def test_erc4626_recurses_to_underlying(self):
        r = FakeReader()
        wsr, rusd = addr(1), addr(2)
        feed_w, vault_w, feed_r = addr(0x11), addr(0x21), addr(0x12)

        r.symbols[wsr] = "wsrUSD"
        r.decimals[wsr] = 18
        r.sources[wsr] = feed_w
        r.prices[wsr] = Price(asset=wsr, amount=10**18, decimals=18)
        r.feeds[feed_w] = {"vault": vault_w}
        r.vaults[vault_w] = {"asset": rusd, "decimals": 18, "rate": 1_040_000}
        _chainlink_asset(r, rusd, feed_r, symbol="rUSD")

        node = om.resolve_asset(r, wsr, max_depth=6)

        assert node.status == "resolved"
        assert node.source_type == om.TYPE_ERC4626
        assert node.source_detail is not None
        assert node.source_detail["underlying"] == rusd
        assert node.source_detail["rate"] == "1040000"
        assert [d.asset for d in node.dependencies] == [rusd]
        assert node.path == [
            "wsrUSD",
            "convertToAssets(1 share)",
            "rUSD",
            "Chainlink feed",
        ]

    def test_morpho_collateral_recurses_to_loan(self):
        r = FakeReader()
        coll, loan = addr(1), addr(2)
        feed, m_oracle, feed_loan = addr(0x11), addr(0x31), addr(0x12)

        r.symbols[coll] = "COLL"
        r.decimals[coll] = 18
        r.sources[coll] = feed
        r.prices[coll] = Price(asset=coll, amount=10**8, decimals=8)
        r.feeds[feed] = {"morpho_oracle": m_oracle, "collateral": coll, "loan": loan}
        r.morpho_prices[m_oracle] = 1_030_000
        _chainlink_asset(r, loan, feed_loan, symbol="LOAN")

        node = om.resolve_asset(r, coll, max_depth=6)

        assert node.status == "resolved"
        assert node.source_type == om.TYPE_MORPHO
        assert node.source_detail is not None
        assert node.source_detail["loan_token"] == loan
        assert node.source_detail["morpho_price"] == "1030000"
        assert [d.asset for d in node.dependencies] == [loan]
        assert node.path[:2] == ["COLL", "Morpho collateral/loan oracle"]

    def test_dual_xref_wins_over_chainlink(self):
        r = FakeReader()
        wsteth = addr(1)
        feed, xy_feed, y_usd_feed = addr(0x11), addr(0x12), addr(0x13)
        r.symbols[wsteth] = "wstETH"
        r.decimals[wsteth] = 18
        r.sources[wsteth] = feed
        r.prices[wsteth] = Price(asset=wsteth, amount=2_600 * 10**8, decimals=8)
        r.feeds[feed] = {
            # the dual-xref contract also answers decimals()+latestRoundData();
            # the generic Chainlink probe must not win
            "round": (1, 2_600 * 10**8, 0, 0, 1),
            "feed_decimals": 8,
            "asset_x": wsteth,
            "xy_feed": xy_feed,
            "y_usd_feed": y_usd_feed,
        }
        r.feeds[xy_feed] = {
            "round": (7, 13 * 10**17, 0, 1_700_000_000, 7),
            "feed_decimals": 18,
            "description": "wstETH / stETH",
        }
        r.feeds[y_usd_feed] = {
            "round": (9, 2_000 * 10**8, 0, 1_700_000_000, 9),
            "feed_decimals": 8,
            "description": "ETH / USD",
        }

        node = om.resolve_asset(r, wsteth, max_depth=6)

        assert node.status == "resolved"
        assert node.source_type == om.TYPE_DUAL_XREF
        # the authoritative middleware price rides along with the derivation
        assert node.price == om.OraclePrice(
            raw=str(2_600 * 10**8),
            decimals=8,
            normalized_wad=str(2_600 * 10**18),
        )
        assert node.path == [
            "wstETH",
            "DualCrossReferencePriceFeed",
            "ASSET_X/ASSET_Y feed",
            "ASSET_Y/USD feed",
        ]
        assert node.source_detail is not None
        assert node.source_detail["asset_x"] == wsteth
        assert node.source_detail["asset_x_asset_y_feed"] == {
            "address": xy_feed,
            "description": "wstETH / stETH",
            "round_id": "7",
            "answer": str(13 * 10**17),
            "decimals": 18,
            "started_at": 0,
            "started_at_utc": None,
            "updated_at": 1_700_000_000,
            "updated_at_utc": "2023-11-14T22:13:20Z",
            "answered_in_round": "7",
        }
        assert node.source_detail["asset_y_usd_feed"] == {
            "address": y_usd_feed,
            "description": "ETH / USD",
            "round_id": "9",
            "answer": str(2_000 * 10**8),
            "decimals": 8,
            "started_at": 0,
            "started_at_utc": None,
            "updated_at": 1_700_000_000,
            "updated_at_utc": "2023-11-14T22:13:20Z",
            "answered_in_round": "9",
        }
        # 1.3 X/Y × 2000 Y/USD = 2600 USD, rescaled to 18 decimals
        assert node.source_detail["derived_price_wad"] == str(2_600 * 10**18)
        assert node.dependencies == []

    def test_dual_xref_component_without_description_reports_null(self):
        # dual-xref components legitimately lack description() (the on-chain
        # exemplar's wstETH/stETH adapter does) — null in the block, and no
        # bearing on the node status
        r = FakeReader()
        wsteth = addr(1)
        feed, xy_feed, y_usd_feed = addr(0x11), addr(0x12), addr(0x13)
        r.symbols[wsteth] = "wstETH"
        r.decimals[wsteth] = 18
        r.sources[wsteth] = feed
        r.prices[wsteth] = Price(asset=wsteth, amount=2_600 * 10**8, decimals=8)
        r.feeds[feed] = {
            "asset_x": wsteth,
            "xy_feed": xy_feed,
            "y_usd_feed": y_usd_feed,
        }
        r.feeds[xy_feed] = {
            "round": (7, 13 * 10**17, 0, 1_700_000_000, 7),
            "feed_decimals": 18,
        }
        r.feeds[y_usd_feed] = {
            "round": (9, 2_000 * 10**8, 0, 1_700_000_000, 9),
            "feed_decimals": 8,
            "description": "ETH / USD",
        }

        node = om.resolve_asset(r, wsteth, max_depth=6)

        assert node.status == "resolved"
        assert node.source_detail is not None
        assert node.source_detail["asset_x_asset_y_feed"]["description"] is None
        assert node.source_detail["asset_y_usd_feed"]["description"] == "ETH / USD"

    def test_dual_xref_component_unreadable_is_partial(self):
        r = FakeReader()
        wsteth = addr(1)
        feed, xy_feed, y_usd_feed = addr(0x11), addr(0x12), addr(0x13)
        r.symbols[wsteth] = "wstETH"
        r.decimals[wsteth] = 18
        r.sources[wsteth] = feed
        r.prices[wsteth] = Price(asset=wsteth, amount=2_600 * 10**8, decimals=8)
        r.feeds[feed] = {
            "asset_x": wsteth,
            "xy_feed": xy_feed,
            "y_usd_feed": y_usd_feed,
        }
        r.feeds[xy_feed] = {
            "round": (1, 13 * 10**17, 0, 1_700_000_000, 1),
            "feed_decimals": 18,
        }
        # y_usd_feed unreadable (no round data)

        node = om.resolve_asset(r, wsteth, max_depth=6)

        assert node.source_type == om.TYPE_DUAL_XREF
        assert node.status == "partial"
        assert node.reason == "dual_xref_component_unreadable"
        assert node.source_detail is not None
        # the readable component is still reported
        assert node.source_detail["asset_x_asset_y_feed"]["answer"] == str(13 * 10**17)
        assert node.source_detail["asset_y_usd_feed"]["answer"] is None
        assert node.source_detail["derived_price_wad"] is None
        # the authoritative middleware price is carried regardless
        assert node.price is not None
        assert node.price.raw == str(2_600 * 10**8)

    def test_dual_xref_xy_component_unreadable_is_partial(self):
        r = FakeReader()
        wsteth = addr(1)
        feed, xy_feed, y_usd_feed = addr(0x11), addr(0x12), addr(0x13)
        r.symbols[wsteth] = "wstETH"
        r.decimals[wsteth] = 18
        r.sources[wsteth] = feed
        r.prices[wsteth] = Price(asset=wsteth, amount=2_600 * 10**8, decimals=8)
        r.feeds[feed] = {
            "asset_x": wsteth,
            "xy_feed": xy_feed,
            "y_usd_feed": y_usd_feed,
        }
        # xy_feed unreadable (no round data)
        r.feeds[y_usd_feed] = {
            "round": (1, 2_000 * 10**8, 0, 1_700_000_000, 1),
            "feed_decimals": 8,
        }

        node = om.resolve_asset(r, wsteth, max_depth=6)

        assert node.source_type == om.TYPE_DUAL_XREF
        assert node.status == "partial"
        assert node.reason == "dual_xref_component_unreadable"
        assert node.source_detail is not None
        assert node.source_detail["asset_x_asset_y_feed"]["answer"] is None
        assert node.source_detail["asset_y_usd_feed"]["answer"] == str(2_000 * 10**8)
        assert node.source_detail["derived_price_wad"] is None

    def test_dual_xref_missing_component_decimals_is_partial(self):
        r = FakeReader()
        wsteth = addr(1)
        feed, xy_feed, y_usd_feed = addr(0x11), addr(0x12), addr(0x13)
        r.symbols[wsteth] = "wstETH"
        r.decimals[wsteth] = 18
        r.sources[wsteth] = feed
        r.prices[wsteth] = Price(asset=wsteth, amount=2_600 * 10**8, decimals=8)
        r.feeds[feed] = {
            "asset_x": wsteth,
            "xy_feed": xy_feed,
            "y_usd_feed": y_usd_feed,
        }
        r.feeds[xy_feed] = {
            "round": (1, 13 * 10**17, 0, 1_700_000_000, 1),
            "feed_decimals": 18,
        }
        # answer readable but decimals() missing — the answer alone cannot be
        # scaled, so no derived price may be produced
        r.feeds[y_usd_feed] = {"round": (1, 2_000 * 10**8, 0, 1_700_000_000, 1)}

        node = om.resolve_asset(r, wsteth, max_depth=6)

        assert node.source_type == om.TYPE_DUAL_XREF
        assert node.status == "partial"
        assert node.reason == "dual_xref_component_unreadable"
        assert node.source_detail is not None
        assert node.source_detail["asset_y_usd_feed"]["answer"] == str(2_000 * 10**8)
        assert node.source_detail["asset_y_usd_feed"]["decimals"] is None
        assert node.source_detail["derived_price_wad"] is None

    def test_dual_xref_probe_incomplete_falls_through_to_chainlink_style(self):
        r = FakeReader()
        asset = addr(1)
        feed = addr(0x11)
        r.symbols[asset] = "ODD"
        r.decimals[asset] = 18
        r.sources[asset] = feed
        r.prices[asset] = Price(asset=asset, amount=10**8, decimals=8)
        # ASSET_X() answers but the component-feed getters do not — not a
        # dual-xref feed, the aggregator fallback must still classify it, yet
        # the foreign getter caps the tier at chainlink_style even though the
        # rest of the evidence (round, description, version) is confirmed-grade
        r.feeds[feed] = {
            "asset_x": addr(2),
            "round": (1, 10**8, 0, 1_700_000_000, 1),
            "feed_decimals": 8,
            "description": "ODD / USD",
            "version": 4,
        }

        node = om.resolve_asset(r, asset, max_depth=6)

        assert node.source_type == om.TYPE_CHAINLINK_STYLE
        assert node.path == ["ODD", "Chainlink-style feed"]
        assert node.status == "resolved"
        assert node.reason is None

    def test_custom_unknown_is_partial(self):
        r = FakeReader()
        asset, feed = addr(1), addr(0x11)
        r.symbols[asset] = "WAT"
        r.decimals[asset] = 18
        r.sources[asset] = feed
        r.feeds[feed] = {}  # no probe matches

        node = om.resolve_asset(r, asset, max_depth=6)

        assert node.status == "partial"
        assert node.source_type == om.TYPE_UNKNOWN
        assert node.reason == "unsupported_custom_feed"

    def test_no_source_configured(self):
        r = FakeReader()
        asset = addr(1)
        r.symbols[asset] = "NOPE"
        r.decimals[asset] = 18
        # no entry in r.sources → source_of returns None

        node = om.resolve_asset(r, asset, max_depth=6)

        assert node.status == "partial"
        assert node.reason == "no_source_configured"

    def test_zero_source_with_manager_delegates_to_underlying(self):
        r = FakeReader()
        weth = addr(1)
        mw, feed = addr(0x51), addr(0x11)
        zero = Web3.to_checksum_address(om.ZERO_ADDRESS)
        r.symbols[weth] = "WETH"
        r.decimals[weth] = 18
        r.sources[weth] = zero  # manager: no per-vault override
        r.prices[weth] = Price(asset=weth, amount=2_000 * 10**18, decimals=18)
        r.underlying_mw = mw
        delegated = FakeReader()
        _chainlink_asset(delegated, weth, feed, symbol="WETH")
        r.delegated = delegated

        node = om.resolve_asset(r, weth, max_depth=6)

        assert node.status == "resolved"
        assert node.source_type == om.TYPE_MIDDLEWARE_FALLBACK
        assert node.source == zero
        assert node.source_detail == {
            "delegated_to": mw,
            "chainlink_feed_registry": None,
        }
        # the manager's own (delegated, 18-decimal) price is carried here
        assert node.price is not None
        assert node.price.raw == str(2_000 * 10**18)
        assert node.path == ["WETH", "middleware fallback", "Chainlink feed"]
        dep = node.dependencies[0]
        assert dep.asset == weth
        assert dep.source_type == om.TYPE_CHAINLINK
        assert dep.status == "resolved"
        # the dep re-reads the price from the underlying middleware itself;
        # 8 = old deployed base middleware (newer ones answer 18 — decimals
        # are pass-through either way)
        assert dep.price is not None
        assert dep.price.decimals == 8

    def test_manager_delegation_with_unreadable_price_demoted_to_partial(self):
        # On-chain the manager's zero-source getAssetPrice IS the delegation,
        # so its own read failing is an anomaly — own-price demotion. Doubles
        # as the precedence case: with the dep partial too, the node reports
        # its own `partial` + reason, not dependency-driven partially_resolved.
        r = FakeReader()
        weth = addr(1)
        mw = addr(0x51)
        r.symbols[weth] = "WETH"
        r.decimals[weth] = 18
        # no price on the manager and none on the underlying middleware either
        r.underlying_mw = mw
        delegated = FakeReader()
        delegated.symbols[weth] = "WETH"
        delegated.decimals[weth] = 18
        r.delegated = delegated

        node = om.resolve_asset(r, weth, max_depth=6)

        assert node.status == "partial"
        assert node.reason == "manager_price_unreadable"
        assert node.source_type == om.TYPE_MIDDLEWARE_FALLBACK
        assert node.price is None
        assert node.source_detail is not None
        assert node.source_detail["delegated_to"] == mw
        dep = node.dependencies[0]
        assert dep.status == "partial"
        assert dep.reason == "no_source_configured"

    def test_zero_source_priced_without_manager_is_middleware_fallback(self):
        r = FakeReader()
        weth = addr(1)
        registry = addr(0x61)
        r.symbols[weth] = "WETH"
        r.decimals[weth] = 18
        # no source entry and no manager, but the middleware prices the asset
        r.prices[weth] = Price(asset=weth, amount=2_000 * 10**8, decimals=8)
        r.registry = registry

        node = om.resolve_asset(r, weth, max_depth=6)

        assert node.status == "resolved"
        assert node.source_type == om.TYPE_MIDDLEWARE_FALLBACK
        assert node.path == ["WETH", "middleware fallback"]
        assert node.source_detail == {
            "delegated_to": None,
            "chainlink_feed_registry": registry,
        }
        assert node.dependencies == []
        assert node.price is not None
        assert node.price.raw == str(2_000 * 10**8)

    def test_zero_feed_registry_reported_as_null(self):
        r = FakeReader()
        weth = addr(1)
        r.symbols[weth] = "WETH"
        r.decimals[weth] = 18
        r.prices[weth] = Price(asset=weth, amount=2_000 * 10**8, decimals=8)
        # non-mainnet middleware: the registry getter answers the zero address
        r.registry = Web3.to_checksum_address(om.ZERO_ADDRESS)

        node = om.resolve_asset(r, weth, max_depth=6)

        assert node.status == "resolved"
        assert node.source_type == om.TYPE_MIDDLEWARE_FALLBACK
        assert node.source_detail == {
            "delegated_to": None,
            "chainlink_feed_registry": None,
        }

    def test_zero_underlying_middleware_treated_as_absent(self):
        r = FakeReader()
        asset = addr(1)
        r.symbols[asset] = "NOPE"
        r.decimals[asset] = 18
        r.underlying_mw = Web3.to_checksum_address(om.ZERO_ADDRESS)
        # no price either → genuinely unconfigured, not middleware fallback

        node = om.resolve_asset(r, asset, max_depth=6)

        assert node.status == "partial"
        assert node.reason == "no_source_configured"

    def test_middleware_fallback_delegation_respects_max_depth(self):
        r = FakeReader()
        weth = addr(1)
        mw, feed = addr(0x51), addr(0x11)
        r.symbols[weth] = "WETH"
        r.decimals[weth] = 18
        r.prices[weth] = Price(asset=weth, amount=2_000 * 10**18, decimals=18)
        r.underlying_mw = mw
        delegated = FakeReader()
        _chainlink_asset(delegated, weth, feed, symbol="WETH")
        r.delegated = delegated

        node = om.resolve_asset(r, weth, max_depth=0)

        assert node.status == "partially_resolved"
        dep = node.dependencies[0]
        assert dep.status == "partial"
        assert dep.reason == "max_depth_exceeded"
        # the underlying middleware's price is still carried on the capped dep
        assert dep.price is not None
        assert dep.price.raw == "99980000"

    def test_middleware_fallback_recurses_through_layered_middlewares(self):
        # manager → global middleware that itself has no source for the asset
        # but a readable price (its internal FeedRegistry fallback)
        r = FakeReader()
        weth = addr(1)
        mw, registry = addr(0x51), addr(0x61)
        r.symbols[weth] = "WETH"
        r.decimals[weth] = 18
        r.prices[weth] = Price(asset=weth, amount=2_000 * 10**18, decimals=18)
        r.underlying_mw = mw
        delegated = FakeReader()
        delegated.symbols[weth] = "WETH"
        delegated.decimals[weth] = 18
        delegated.prices[weth] = Price(asset=weth, amount=2_000 * 10**8, decimals=8)
        delegated.registry = registry
        r.delegated = delegated

        node = om.resolve_asset(r, weth, max_depth=6)

        assert node.status == "resolved"
        dep = node.dependencies[0]
        assert dep.source_type == om.TYPE_MIDDLEWARE_FALLBACK
        assert dep.status == "resolved"
        assert dep.source_detail == {
            "delegated_to": None,
            "chainlink_feed_registry": registry,
        }
        assert node.path == ["WETH", "middleware fallback", "middleware fallback"]

    def test_cycle_detected(self):
        r = FakeReader()
        a = addr(1)
        feed, vault = addr(0x11), addr(0x21)
        r.symbols[a] = "LOOP"
        r.decimals[a] = 18
        r.sources[a] = feed
        r.prices[a] = Price(asset=a, amount=10**18, decimals=18)
        r.feeds[feed] = {"vault": vault}
        r.vaults[vault] = {"asset": a, "decimals": 18, "rate": 10**18}

        node = om.resolve_asset(r, a, max_depth=6)

        assert node.status == "partially_resolved"
        dep = node.dependencies[0]
        assert dep.status == "partial"
        assert dep.reason == "cycle_detected"
        # authoritative price is carried even on a cycle-capped node
        assert dep.price is not None
        assert dep.price.raw == str(10**18)

    def test_max_depth_exceeded(self):
        r = FakeReader()
        a, b = addr(1), addr(2)
        feed_a, vault_a, feed_b = addr(0x11), addr(0x21), addr(0x12)
        r.symbols[a] = "A"
        r.decimals[a] = 18
        r.sources[a] = feed_a
        r.feeds[feed_a] = {"vault": vault_a}
        r.vaults[vault_a] = {"asset": b, "decimals": 18, "rate": 10**18}
        _chainlink_asset(r, b, feed_b, symbol="B")

        node = om.resolve_asset(r, a, max_depth=0)

        assert node.status == "partially_resolved"
        dep = node.dependencies[0]
        assert dep.status == "partial"
        assert dep.reason == "max_depth_exceeded"
        # authoritative price is carried even on a depth-capped node
        assert dep.price is not None
        assert dep.price.raw == "99980000"

    def test_erc4626_underlying_unreadable(self):
        r = FakeReader()
        wsr = addr(1)
        feed, vault = addr(0x11), addr(0x21)
        r.symbols[wsr] = "wsrUSD"
        r.decimals[wsr] = 18
        r.sources[wsr] = feed
        r.feeds[feed] = {"vault": vault}
        r.vaults[vault] = {"decimals": 18, "rate": 10**18}  # no "asset" → unreadable

        node = om.resolve_asset(r, wsr, max_depth=6)

        assert node.source_type == om.TYPE_ERC4626
        assert node.status == "partial"
        assert node.reason == "erc4626_underlying_unreadable"
        assert node.dependencies == []

    def test_morpho_loan_token_unreadable(self):
        r = FakeReader()
        coll = addr(1)
        feed, m_oracle = addr(0x11), addr(0x31)
        r.symbols[coll] = "COLL"
        r.decimals[coll] = 18
        r.sources[coll] = feed
        r.feeds[feed] = {"morpho_oracle": m_oracle, "collateral": coll}  # no "loan"
        r.morpho_prices[m_oracle] = 1_030_000

        node = om.resolve_asset(r, coll, max_depth=6)

        assert node.source_type == om.TYPE_MORPHO
        assert node.status == "partial"
        assert node.reason == "morpho_loan_token_unreadable"
        assert node.dependencies == []

    def test_erc4626_rate_unreadable_demoted_to_partial(self):
        # no derived price without the share→asset rate; the resolved
        # underlying dep is still attached (readable parts kept)
        r = FakeReader()
        wsr, rusd = addr(1), addr(2)
        feed_w, vault_w, feed_r = addr(0x11), addr(0x21), addr(0x12)
        r.symbols[wsr] = "wsrUSD"
        r.decimals[wsr] = 18
        r.sources[wsr] = feed_w
        r.prices[wsr] = Price(asset=wsr, amount=10**18, decimals=18)
        r.feeds[feed_w] = {"vault": vault_w}
        r.vaults[vault_w] = {"asset": rusd, "decimals": 18}  # no "rate"
        _chainlink_asset(r, rusd, feed_r, symbol="rUSD")

        node = om.resolve_asset(r, wsr, max_depth=6)

        assert node.source_type == om.TYPE_ERC4626
        assert node.status == "partial"
        assert node.reason == "erc4626_rate_unreadable"
        assert node.source_detail is not None
        assert node.source_detail["rate"] is None
        dep = node.dependencies[0]
        assert dep.asset == rusd
        assert dep.status == "resolved"

    def test_partial_leaf_demotes_every_ancestor(self):
        # A → B (ERC4626 hops) → C (no source): C partial, both ancestors
        # partially_resolved — propagation is transitive via direct children.
        r = FakeReader()
        a, b, c = addr(1), addr(2), addr(3)
        feed_a, vault_a = addr(0x11), addr(0x21)
        feed_b, vault_b = addr(0x12), addr(0x22)
        for asset, sym, feed, vault, under in (
            (a, "A", feed_a, vault_a, b),
            (b, "B", feed_b, vault_b, c),
        ):
            r.symbols[asset] = sym
            r.decimals[asset] = 18
            r.sources[asset] = feed
            r.prices[asset] = Price(asset=asset, amount=10**18, decimals=18)
            r.feeds[feed] = {"vault": vault}
            r.vaults[vault] = {"asset": under, "decimals": 18, "rate": 10**18}
        r.symbols[c] = "C"
        r.decimals[c] = 18

        node = om.resolve_asset(r, a, max_depth=6)

        assert node.status == "partially_resolved"
        mid = node.dependencies[0]
        assert mid.status == "partially_resolved"
        leaf = mid.dependencies[0]
        assert leaf.status == "partial"
        assert leaf.reason == "no_source_configured"


def _bare_node(status: NodeStatus, reason: str | None = None, deps: list | None = None):
    return om.OracleNode(
        asset=addr(1),
        symbol=None,
        decimals=None,
        source=None,
        price=None,
        status=status,
        reason=reason,
        dependencies=deps or [],
    )


class TestCollectUnresolved:
    def test_collects_nested_partials(self):
        tree = [
            _bare_node(
                "resolved",
                deps=[_bare_node("partial", reason="cycle_detected")],
            ),
            _bare_node("resolved"),
        ]
        out = om._collect_unresolved(tree)
        assert len(out) == 1
        assert out[0].reason == "cycle_detected"

    def test_partially_resolved_parent_not_collected(self):
        # demoted parents self-describe; only own-failure nodes are mirrored
        tree = [
            _bare_node(
                "partially_resolved",
                deps=[_bare_node("partial", reason="max_depth_exceeded")],
            ),
        ]
        out = om._collect_unresolved(tree)
        assert [n.status for n in out] == ["partial"]


class TestMappingStatus:
    def test_all_resolved(self):
        roots = [_bare_node("resolved"), _bare_node("resolved")]
        assert om._mapping_status(roots) == "resolved"

    def test_empty_is_vacuously_resolved(self):
        assert om._mapping_status([]) == "resolved"

    def test_mix_is_partially_resolved(self):
        roots = [_bare_node("resolved"), _bare_node("partial", reason="x")]
        assert om._mapping_status(roots) == "partially_resolved"

    def test_partially_resolved_root_keeps_mapping_out_of_unresolved(self):
        # a partially_resolved root still explains its own feed — unresolved
        # is reserved for total failure
        roots = [_bare_node("partially_resolved"), _bare_node("partial", reason="x")]
        assert om._mapping_status(roots) == "partially_resolved"

    def test_all_roots_partial_is_unresolved(self):
        roots = [_bare_node("partial", reason="x"), _bare_node("partial", reason="y")]
        assert om._mapping_status(roots) == "unresolved"


# ---------------------------------------------------------------------------
# to_dict — the JSON shape the CLI --json / MCP response is built from
# ---------------------------------------------------------------------------


class TestToDict:
    def test_node_shape_is_uniform(self):
        # Every key is always present — resolved leaves carry null reason /
        # empty dependencies rather than omitting the keys.
        r = FakeReader()
        usdc, feed = addr(1), addr(0x11)
        _chainlink_asset(r, usdc, feed, symbol="USDC")

        out = om.resolve_asset(r, usdc, max_depth=6).to_dict()

        assert set(out) == {
            "asset",
            "symbol",
            "decimals",
            "source",
            "source_type",
            "price",
            "path",
            "status",
            "reason",
            "source_detail",
            "dependencies",
        }
        assert out["status"] == "resolved"
        assert out["price"] == {
            "raw": "99980000",
            "decimals": 8,
            "normalized_wad": str(99_980_000 * 10**10),
        }
        assert out["reason"] is None
        assert out["dependencies"] == []
        assert out["source_detail"]["answer"] == "99980000"

    def test_partial_node_carries_reason_and_null_source_detail(self):
        r = FakeReader()
        asset = addr(1)
        r.symbols[asset] = "NOPE"
        r.decimals[asset] = 18

        out = om.resolve_asset(r, asset, max_depth=6).to_dict()

        assert out["status"] == "partial"
        assert out["reason"] == "no_source_configured"
        assert out["source_detail"] is None
        assert out["price"] is None

    def test_dependencies_serialized_recursively(self):
        r = FakeReader()
        wsr, rusd = addr(1), addr(2)
        feed_w, vault_w, feed_r = addr(0x11), addr(0x21), addr(0x12)
        r.symbols[wsr] = "wsrUSD"
        r.decimals[wsr] = 18
        r.sources[wsr] = feed_w
        r.prices[wsr] = Price(asset=wsr, amount=10**18, decimals=18)
        r.feeds[feed_w] = {"vault": vault_w}
        r.vaults[vault_w] = {"asset": rusd, "decimals": 18, "rate": 1_040_000}
        _chainlink_asset(r, rusd, feed_r, symbol="rUSD")

        out = om.resolve_asset(r, wsr, max_depth=6).to_dict()

        assert [d["asset"] for d in out["dependencies"]] == [rusd]
        assert out["dependencies"][0]["source_type"] == om.TYPE_CHAINLINK

    def test_mapping_to_dict(self):
        node = _bare_node("partial", reason="no_source_configured")
        mapping = om.OracleMapping(
            vault=addr(0xAA),
            vault_name="V",
            asset={"address": addr(1), "symbol": "USDC", "decimals": 6},
            price_oracle=addr(0x99),
            block_number=123,
            asset_source="getConfiguredAssets",
            status="unresolved",
            configured_assets=[node],
            unresolved=[node],
        )

        out = mapping.to_dict()

        assert out["vault"] == addr(0xAA)
        assert out["vault_name"] == "V"
        assert out["asset"] == {"address": addr(1), "symbol": "USDC", "decimals": 6}
        assert out["price_oracle"] == addr(0x99)
        assert out["block_number"] == 123
        assert out["asset_source"] == "getConfiguredAssets"
        assert out["status"] == "unresolved"
        assert out["configured_assets"] == [node.to_dict()]
        assert out["unresolved"] == [node.to_dict()]


# ---------------------------------------------------------------------------
# OracleMappingReader — probe decoding against a mocked Web3Context
# ---------------------------------------------------------------------------

ORACLE = Web3.to_checksum_address("0x9999999999999999999999999999999999999999")


def _mock_reader() -> tuple[om.OracleMappingReader, MagicMock]:
    ctx = MagicMock()
    return om.OracleMappingReader(ctx, ORACLE), ctx


class TestOracleMappingReader:
    def test_configured_assets(self):
        reader, ctx = _mock_reader()
        a1, a2 = addr(1), addr(2)
        ctx.call.return_value = encode(["address[]"], [[a1, a2]])

        assert reader.configured_assets() == [a1, a2]

    def test_safe_returns_none_on_revert(self):
        reader, ctx = _mock_reader()
        ctx.call.side_effect = RuntimeError("execution reverted")

        assert reader.configured_assets() is None
        assert reader.source_of(addr(1)) is None
        assert reader.asset_price(addr(1)) is None
        # for the variant probes a revert is the normal answer (plain
        # middleware / manager, respectively) — None is the detection signal
        assert reader.underlying_middleware() is None
        assert reader.chainlink_feed_registry() is None

    def test_oracle_reads(self):
        reader, ctx = _mock_reader()
        src = addr(0x11)
        ctx.call.return_value = encode(["address"], [src])
        assert reader.source_of(addr(1)) == src

        ctx.call.return_value = encode(["uint256", "uint256"], [10**8, 8])
        price = reader.asset_price(addr(1))
        assert price is not None
        assert price.amount == 10**8
        assert price.decimals == 8

    def test_token_metadata(self):
        reader, ctx = _mock_reader()
        ctx.call.return_value = encode(["string"], ["USDC"])
        assert reader.symbol(addr(1)) == "USDC"

        ctx.call.return_value = encode(["uint256"], [6])
        assert reader.token_decimals(addr(1)) == 6

    def test_feed_probes(self):
        reader, ctx = _mock_reader()
        feed = addr(0x11)

        ctx.call.return_value = encode(["uint8"], [8])
        assert reader.feed_decimals(feed) == 8

        ctx.call.return_value = encode(["string"], ["ETH / USD"])
        assert reader.feed_description(feed) == "ETH / USD"

        ctx.call.return_value = encode(
            ["uint80", "int256", "uint256", "uint256", "uint80"],
            [1, 99_980_000, 0, 1_700_000_000, 1],
        )
        assert reader.feed_latest_round_data(feed) == (
            1,
            99_980_000,
            0,
            1_700_000_000,
            1,
        )

        ctx.call.return_value = encode(["uint256"], [4])
        assert reader.feed_version(feed) == 4

        ctx.call.return_value = encode(["uint16"], [3])
        assert reader.feed_phase_id(feed) == 3

        target = addr(0x21)
        ctx.call.return_value = encode(["address"], [target])
        assert reader.feed_vault(feed) == target
        assert reader.feed_morpho_oracle(feed) == target
        assert reader.feed_collateral_token(feed) == target
        assert reader.feed_loan_token(feed) == target
        assert reader.feed_asset_x(feed) == target
        assert reader.feed_asset_x_asset_y_feed(feed) == target
        assert reader.feed_asset_y_usd_feed(feed) == target
        assert reader.feed_aggregator(feed) == target

        ctx.call.return_value = encode(["uint256"], [1_030_000])
        assert reader.morpho_oracle_price(addr(0x31)) == 1_030_000

    def test_oracle_variant_probes(self):
        reader, ctx = _mock_reader()
        target = addr(0x51)
        ctx.call.return_value = encode(["address"], [target])

        assert reader.underlying_middleware() == target
        assert reader.chainlink_feed_registry() == target

    def test_delegate_binds_new_reader_to_oracle(self):
        other = addr(0x52)
        ctx = _FakeLogCtx([])
        delegated = om.OracleMappingReader(ctx, ORACLE).delegate(other)

        # the one reader method whose target contract is observable from
        # outside (get_logs receives it explicitly); the block is irrelevant
        delegated.asset_source_events(1337)

        assert ctx.captured["contract_address"] == other

    def test_erc4626_vault_reads(self):
        reader, ctx = _mock_reader()
        vault = addr(0x21)

        underlying = addr(2)
        ctx.call.return_value = encode(["address"], [underlying])
        assert reader.vault_asset(vault) == underlying

        ctx.call.return_value = encode(["uint8"], [18])
        assert reader.vault_decimals(vault) == 18

        ctx.call.return_value = encode(["uint256"], [1_040_000])
        assert reader.vault_convert_to_assets(vault, 10**18) == 1_040_000


# ---------------------------------------------------------------------------
# Asset enumeration (manager call vs event-replay fallback) + block pinning
# ---------------------------------------------------------------------------


class _FakeLogCtx:
    """Minimal Web3Context stand-in for OracleMappingReader.asset_source_events."""

    def __init__(self, logs: list[dict[str, Any]]):
        self._logs = logs
        self.captured: dict[str, Any] = {}

    def get_logs(self, *, contract_address, topics, from_block, to_block):
        self.captured = {
            "contract_address": contract_address,
            "topics": topics,
            "from_block": from_block,
            "to_block": to_block,
        }
        return self._logs


def _log(block: int, asset: str, source: str) -> dict[str, Any]:
    return {
        "blockNumber": block,
        "data": encode(["address", "address"], [asset, source]),
    }


class TestAssetSourceEvents:
    def test_pins_to_block_and_decodes(self):
        oracle, a, s = addr(0x99), addr(1), addr(0x11)
        ctx = _FakeLogCtx([_log(100, a, s)])
        reader = om.OracleMappingReader(ctx, oracle)

        out = reader.asset_source_events(12345)

        # historical correctness: the log scan is capped at the target block
        assert ctx.captured["to_block"] == 12345
        assert ctx.captured["from_block"] == 0
        assert ctx.captured["topics"] == [om.ASSET_PRICE_SOURCE_UPDATED_TOPIC]
        assert out == [(100, a, s)]


class _EnumReader:
    """Stand-in exposing only what _enumerate_assets touches."""

    def __init__(self, configured, events):
        self._configured = configured
        self._events = events
        self.events_to_block = None

    def configured_assets(self):
        return self._configured

    def asset_source_events(self, to_block):
        self.events_to_block = to_block
        return self._events


class TestEnumerateAssets:
    def test_manager_path(self):
        a1, a2 = addr(1), addr(2)
        reader = _EnumReader([a1, a2], [])

        assets, source = om._enumerate_assets(reader, 999)

        assert source == "getConfiguredAssets"
        assert assets == [a1, a2]
        assert reader.events_to_block is None  # fallback not taken

    def test_event_fallback_collapses_and_pins_block(self):
        a1, a2 = addr(1), addr(2)
        s1, s2 = addr(0x11), addr(0x12)
        zero = Web3.to_checksum_address(om.ZERO_ADDRESS)
        # a1 re-pointed (s2 wins); a2 removed via zero-address source
        events = [(100, a1, s1), (200, a1, s2), (150, a2, zero)]
        reader = _EnumReader(None, events)

        assets, source = om._enumerate_assets(reader, 12345)

        assert source == "events"
        assert reader.events_to_block == 12345  # fallback pinned to target block
        assert assets == [a1]


# ---------------------------------------------------------------------------
# build_oracle_mapping integration (PlasmaVault + reader patched; resolver runs real)
# ---------------------------------------------------------------------------


class _Ret:
    """Wraps a value as a Call-like object exposing .call()."""

    def __init__(self, value: Any):
        self._value = value

    def call(self) -> Any:
        return self._value


class _FakeVault:
    """Stand-in for PlasmaVault — only the three reads build_oracle_mapping makes."""

    def __init__(
        self, oracle: str, asset: str, name: str, *, name_raises: bool = False
    ):
        self._oracle = oracle
        self._asset = asset
        self._name = name
        self._name_raises = name_raises

    def get_price_oracle_middleware_address(self) -> _Ret:
        return _Ret(self._oracle)

    def underlying_asset_address(self) -> _Ret:
        return _Ret(self._asset)

    def name(self) -> _Ret:
        if self._name_raises:
            raise RuntimeError("name() reverted")
        return _Ret(self._name)


class TestBuildOracleMapping:
    def test_assembles_tree_and_mirrors_unresolved(self):
        oracle, vault = addr(0x99), addr(0xAA)
        usdc, wsr, nosrc = addr(1), addr(2), addr(3)
        feed_c, feed_w, vault_w = addr(0x11), addr(0x12), addr(0x21)

        r = FakeReader()
        r.configured = [usdc, wsr, nosrc]  # manager path
        _chainlink_asset(r, usdc, feed_c, symbol="USDC")
        # wsr: ERC4626 feed → vault → usdc (resolved leaf)
        r.symbols[wsr] = "wsrUSD"
        r.decimals[wsr] = 18
        r.sources[wsr] = feed_w
        r.prices[wsr] = Price(asset=wsr, amount=10**18, decimals=18)
        r.feeds[feed_w] = {"vault": vault_w}
        r.vaults[vault_w] = {"asset": usdc, "decimals": 18, "rate": 1_040_000}
        # nosrc: no source configured → partial
        r.symbols[nosrc] = "NOPE"
        r.decimals[nosrc] = 18

        with (
            patch.object(
                om, "PlasmaVault", return_value=_FakeVault(oracle, usdc, "Reservoir")
            ),
            patch.object(om, "OracleMappingReader", return_value=r),
        ):
            out = om.build_oracle_mapping(object(), vault, 123)

        assert out.vault == vault
        assert out.vault_name == "Reservoir"
        assert out.price_oracle == oracle
        assert out.block_number == 123
        assert out.asset_source == "getConfiguredAssets"
        assert out.asset == {"address": usdc, "symbol": "USDC", "decimals": 6}
        assert {n.asset for n in out.configured_assets} == {usdc, wsr, nosrc}
        # two roots resolved, one partial → mapping-level roll-up is a mix
        assert out.status == "partially_resolved"
        # only the no-source asset is unresolved, mirrored to the top level
        assert [n.reason for n in out.unresolved] == ["no_source_configured"]
        assert out.unresolved[0].asset == nosrc

    def test_isolates_per_asset_resolution_error(self):
        oracle, vault = addr(0x99), addr(0xAA)
        good, poison = addr(1), addr(2)
        feed_c = addr(0x11)

        r = FakeReader()
        r.configured = [good, poison]
        _chainlink_asset(r, good, feed_c, symbol="GOOD")

        original = om.resolve_asset

        def flaky(reader, asset, max_depth):
            if asset == poison:
                raise RuntimeError("boom inside resolver")
            return original(reader, asset, max_depth)

        with (
            patch.object(om, "PlasmaVault", return_value=_FakeVault(oracle, good, "V")),
            patch.object(om, "OracleMappingReader", return_value=r),
            patch.object(om, "resolve_asset", side_effect=flaky),
        ):
            out = om.build_oracle_mapping(object(), vault, 0)

        by_asset = {n.asset: n for n in out.configured_assets}
        assert by_asset[good].status == "resolved"  # other assets unaffected
        assert by_asset[poison].status == "partial"
        assert by_asset[poison].reason == "resolution_error"
        # the failed asset is mirrored into unresolved
        assert any(n.asset == poison for n in out.unresolved)

    def test_vault_name_unreadable_is_tolerated(self):
        oracle, vault = addr(0x99), addr(0xAA)
        usdc, feed_c = addr(1), addr(0x11)

        r = FakeReader()
        r.configured = [usdc]
        _chainlink_asset(r, usdc, feed_c, symbol="USDC")
        fake_vault = _FakeVault(oracle, usdc, "ignored", name_raises=True)

        with (
            patch.object(om, "PlasmaVault", return_value=fake_vault),
            patch.object(om, "OracleMappingReader", return_value=r),
        ):
            out = om.build_oracle_mapping(object(), vault, 7)

        # a failing name() must not abort the map — vault_name falls back to None
        assert out.vault_name is None
        assert out.block_number == 7
        assert {n.asset for n in out.configured_assets} == {usdc}

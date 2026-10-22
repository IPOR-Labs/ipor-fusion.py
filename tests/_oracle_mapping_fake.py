"""In-memory stand-ins for the oracle mapping's chain reads: `FakeReader`
answers what `OracleMappingReader` would, `FakeVault` the three reads
`build_oracle_mapping` makes of the vault. No network, no RPC."""

from __future__ import annotations

from typing import Generic, TypedDict, TypeVar

from web3 import Web3

from ipor_fusion.types import Price

T = TypeVar("T")


def addr(n: int) -> str:
    return Web3.to_checksum_address(f"0x{n:040x}")


class Feed(TypedDict, total=False):
    """What a price feed answers; an absent key is a getter that reverts."""

    morpho_oracle: str
    collateral: str
    loan: str
    vault: str
    round: tuple[int, int, int, int, int]
    feed_decimals: int
    description: str | None
    version: int
    aggregator: str
    phase_id: int
    asset_x: str
    xy_feed: str
    y_usd_feed: str


class Erc4626(TypedDict, total=False):
    asset: str
    decimals: int
    rate: int


class FakeReader:
    """In-memory stand-in for :class:`oracle_mapping.OracleMappingReader`."""

    def __init__(self) -> None:
        self.symbols: dict[str, str] = {}
        self.decimals: dict[str, int] = {}
        self.prices: dict[str, Price] = {}
        self.sources: dict[str, str] = {}
        self.feeds: dict[str, Feed] = {}
        self.vaults: dict[str, Erc4626] = {}
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
    def _f(self, s: str) -> Feed:
        return self.feeds.get(s, {})

    def feed_morpho_oracle(self, s: str) -> str | None:
        return self._f(s).get("morpho_oracle")

    def feed_collateral_token(self, s: str) -> str | None:
        return self._f(s).get("collateral")

    def feed_loan_token(self, s: str) -> str | None:
        return self._f(s).get("loan")

    def feed_vault(self, s: str) -> str | None:
        return self._f(s).get("vault")

    def feed_latest_round_data(self, s: str) -> tuple[int, int, int, int, int] | None:
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


def chainlink_asset(r: FakeReader, asset: str, source: str, *, symbol: str) -> None:
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


class Returns(Generic[T]):
    """Wraps a value as a Call-like object exposing .call(ctx)."""

    def __init__(self, value: T):
        self._value = value

    def call(self, ctx: object) -> T:
        return self._value


class FakeVault:
    """Stand-in for PlasmaVault — only the three reads build_oracle_mapping makes."""

    def __init__(
        self, oracle: str, asset: str, name: str, *, name_raises: bool = False
    ):
        self._oracle = oracle
        self._asset = asset
        self._name = name
        self._name_raises = name_raises

    def read_price_oracle_address(self, ctx: object) -> str:
        return self._oracle

    def underlying_asset_address(self) -> Returns[str]:
        return Returns(self._asset)

    def name(self) -> Returns[str]:
        if self._name_raises:
            raise RuntimeError("name() reverted")
        return Returns(self._name)

"""Resolve how a Plasma Vault prices every configured asset at a given block.

Powers the ``fusion vault oracle-mapping`` CLI command and the
``vault_oracle_mapping`` MCP tool. For each asset the vault's price oracle
knows about, this walks the *source* price feed, classifies its type by
interface probing, reads the effective price, and recursively resolves any
feed that derives its price from another asset (ERC4626 wrappers, Morpho
collateral feeds). Unknown feeds are reported as partial, never dropped.
A zero source is not a dead end: the oracle may be a manager variant that
delegates such assets to an underlying global middleware — that delegation is
followed and reported as ``middleware_fallback``. Every aggregator-compatible
read (Chainlink-tier leaves, dual-cross-reference component feeds) carries
``description()`` and the full ``latestRoundData()`` round in
``source_detail`` — raw values plus ISO-8601 UTC twins of the timestamps,
no staleness judgment; ``description`` is null when the feed does not
implement it.

Chainlink leaves are graded by on-chain evidence: ``ChainlinkAggregator``
only when the full AggregatorV3Interface answers (``latestRoundData`` +
``decimals`` + ``description`` + ``version``) and nothing contradicts it;
``chainlink_style`` when the feed merely quacks like an aggregator — verify
the address yourself if identity matters. Grading only ever withholds the
confirmed label; it cannot prove a contract's identity or operator.

Statuses: a node is ``resolved`` (own feed explained and every dependency
resolved), ``partially_resolved`` (own feed explained, but some descendant is
not resolved), or ``partial`` (the node's own resolution is incomplete —
``reason`` says why). The mapping-level ``status`` rolls up the roots:
``resolved`` when every configured asset resolved (vacuously so for zero
assets), ``unresolved`` when every root is ``partial`` (total failure),
``partially_resolved`` otherwise. The ``unresolved`` array mirrors
``partial`` nodes only; demoted parents self-describe.

Design:
- All network access goes through :class:`OracleMappingReader` (the single
  seam). Every read is "safe": a revert/decode failure yields ``None`` so one
  bad asset never aborts the whole map. The pure logic (classification,
  recursion, event collapse) takes a reader and is unit-testable with a fake.
- SDK primitives are reused for the reads that already exist
  (``PriceOracleMiddleware``, ``ERC20``, ``PlasmaVault``); only the feed-probe
  reads and ``getConfiguredAssets`` — which they lack — are added here.

Adding a feed type:

1. Wrapper class (``ContractWrapper``) exposing its getters.
2. Revert-safe reader probes on :class:`OracleMappingReader` (``None`` on
   failure).
3. ``TYPE_*`` constant + ``_resolve_*`` function; recurse only into
   middleware assets — component *feeds* belong in ``source_detail``.
4. Dispatch entry in ``_classify_and_resolve``, ordered by specificity,
   above the generic Chainlink-tier fallback. Gate on the type-defining
   reads, not a single common-named getter (false-matcher magnet); an
   incomplete gate falls through with no type claim.
5. External doc touchpoints: ``cli/vault_cmd.py`` (command docstring +
   rendering), ``mcp/server.py`` tool docstring and ``mcp/models.py``
   ``source_type`` description (the last two are tripwire-tested).
6. Tests against the fake reader: precedence over the generic aggregator
   fallback, happy path, per-probe degradation to ``partial``, incomplete
   gate falling through, and ABI-decode cases for the new reader probes.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from typing import Any

from eth_abi import decode
from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.oracle import PriceOracleMiddleware
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.types import AssetSource, MappingStatus, NodeStatus, Price

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# keccak256("AssetPriceSourceUpdated(address,address)") — both args are in the
# log data (neither is indexed), so only topic0 is used to filter.
# Base-middleware event only; managers emit AssetPriceSourceAdded/Removed
# instead, but expose getConfiguredAssets(), so the replay never runs on one.
ASSET_PRICE_SOURCE_UPDATED_TOPIC = (
    "0xe6c35d0425da27d8f991ada353619254c33e5094fc7e19154e02feb391937390"
)

# source_type values (also the human label in the path / output). Adding a
# type? Follow the "Adding a feed type" checklist in the module docstring.
TYPE_CHAINLINK = "ChainlinkAggregator"
TYPE_CHAINLINK_STYLE = "chainlink_style"
TYPE_ERC4626 = "ERC4626PriceFeed"
TYPE_MORPHO = "CollateralTokenOnMorphoMarketPriceFeed"
TYPE_DUAL_XREF = "DualCrossReferencePriceFeed"
TYPE_MIDDLEWARE_FALLBACK = "middleware_fallback"
TYPE_UNKNOWN = "custom_unknown"

_WAD = 18


# ---------------------------------------------------------------------------
# Result dataclasses (field names are the JSON keys of the CLI --json / MCP
# output — renaming a field is a client-visible schema change)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OraclePrice:
    """Price block: raw oracle answer plus its 18-decimal rescale.

    Non-null on a node only when the oracle's ``getAssetPrice()`` read
    succeeded.
    """

    raw: str
    decimals: int
    normalized_wad: str


@dataclass(slots=True)
class OracleNode:
    """One asset's resolved price feed (recursive for derived feeds).

    ``status``: ``resolved`` — own feed explained and every dependency
    resolved; ``partially_resolved`` — own feed explained, but some descendant
    is not resolved; ``partial`` — this node's own resolution is incomplete
    (``reason`` says why).
    """

    asset: ChecksumAddress
    symbol: str | None
    decimals: int | None
    source: ChecksumAddress | None
    price: OraclePrice | None
    source_type: str = TYPE_UNKNOWN
    path: list[str] = field(default_factory=list)
    status: NodeStatus = "partial"
    reason: str | None = None
    # Deliberately untyped: the payload differs per source_type. Universal
    # fixed blocks (price) get dataclasses; this is the one dynamic section.
    source_detail: dict[str, Any] | None = None
    dependencies: list[OracleNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready shape; every key always present (null/empty when unused)."""
        return asdict(self)


@dataclass(slots=True)
class OracleAsset:
    """The vault's underlying ERC-20 asset."""

    address: ChecksumAddress
    symbol: str | None
    decimals: int | None


@dataclass(slots=True)
class OracleMapping:
    """Full oracle mapping for a vault at a pinned block.

    ``status`` rolls up the configured-asset roots: ``resolved`` — every root
    resolved (vacuously so with zero assets); ``unresolved`` — every root
    ``partial`` (total failure); ``partially_resolved`` — anything in
    between. The ``unresolved`` list mirrors ``partial`` nodes only — parents
    demoted to ``partially_resolved`` self-describe and are not repeated
    there.
    """

    vault: ChecksumAddress
    vault_name: str | None
    asset: OracleAsset
    price_oracle: ChecksumAddress
    block_number: int
    asset_source: AssetSource
    status: MappingStatus
    configured_assets: list[OracleNode]
    unresolved: list[OracleNode]

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# ---------------------------------------------------------------------------
# Local contract reads (the few the SDK wrappers do not expose)
# ---------------------------------------------------------------------------


def _addr(value: object) -> ChecksumAddress:
    return Web3.to_checksum_address(value)  # type: ignore[arg-type]


class _OracleManager(ContractWrapper):
    def get_configured_assets(self) -> Call[list[ChecksumAddress]]:
        return self._view(
            "getConfiguredAssets()",
            output_types=["address[]"],
            decoder=lambda lst: [_addr(a) for a in lst],
        )

    def get_price_oracle_middleware(self) -> Call[ChecksumAddress]:
        return self._view(
            "getPriceOracleMiddleware()", output_types=["address"], decoder=_addr
        )


class _Erc4626Feed(ContractWrapper):
    def vault(self) -> Call[ChecksumAddress]:
        return self._view("vault()", output_types=["address"], decoder=_addr)


class _MorphoFeed(ContractWrapper):
    def morpho_oracle(self) -> Call[ChecksumAddress]:
        return self._view("morphoOracle()", output_types=["address"], decoder=_addr)

    def collateral_token(self) -> Call[ChecksumAddress]:
        return self._view("collateralToken()", output_types=["address"], decoder=_addr)

    def loan_token(self) -> Call[ChecksumAddress]:
        return self._view("loanToken()", output_types=["address"], decoder=_addr)


class _MorphoOracle(ContractWrapper):
    def price(self) -> Call[int]:
        return self._view("price()", output_types=["uint256"], decoder=int)


class _DualXrefFeed(ContractWrapper):
    def asset_x(self) -> Call[ChecksumAddress]:
        return self._view("ASSET_X()", output_types=["address"], decoder=_addr)

    def asset_x_asset_y_feed(self) -> Call[ChecksumAddress]:
        return self._view(
            "ASSET_X_ASSET_Y_ORACLE_FEED()", output_types=["address"], decoder=_addr
        )

    def asset_y_usd_feed(self) -> Call[ChecksumAddress]:
        return self._view(
            "ASSET_Y_USD_ORACLE_FEED()", output_types=["address"], decoder=_addr
        )


class _Aggregator(ContractWrapper):
    def decimals(self) -> Call[int]:
        return self._view("decimals()", output_types=["uint8"], decoder=int)

    def description(self) -> Call[str]:
        return self._view("description()", output_types=["string"])

    def latest_round_data(self) -> Call[tuple[int, int, int, int, int]]:
        return self._view(
            "latestRoundData()",
            output_types=["uint80", "int256", "uint256", "uint256", "uint80"],
        )

    def version(self) -> Call[int]:
        return self._view("version()", output_types=["uint256"], decoder=int)

    def aggregator(self) -> Call[ChecksumAddress]:
        return self._view("aggregator()", output_types=["address"], decoder=_addr)

    def phase_id(self) -> Call[int]:
        return self._view("phaseId()", output_types=["uint16"], decoder=int)


class _Erc4626Vault(ContractWrapper):
    def asset(self) -> Call[ChecksumAddress]:
        return self._view("asset()", output_types=["address"], decoder=_addr)

    def decimals(self) -> Call[int]:
        return self._view("decimals()", output_types=["uint8"], decoder=int)

    def convert_to_assets(self, shares: int) -> Call[int]:
        return self._view(
            "convertToAssets(uint256)",
            shares,
            output_types=["uint256"],
            decoder=int,
        )


# ---------------------------------------------------------------------------
# Reader — the single network seam (every read is revert-safe → None)
# ---------------------------------------------------------------------------


class OracleMappingReader:
    """Revert-safe on-chain reads against a fixed ``Web3Context`` (and block)."""

    def __init__(self, ctx: Web3Context, oracle: ChecksumAddress):
        self._ctx = ctx
        self._oracle_addr = oracle
        self._oracle = PriceOracleMiddleware(ctx, oracle)
        self._manager = _OracleManager(ctx, oracle)

    @staticmethod
    def _safe(call: Call[Any]) -> Any:
        try:
            return call.call()
        except Exception:
            return None

    # -- enumeration -------------------------------------------------------
    def configured_assets(self) -> list[ChecksumAddress] | None:
        return self._safe(self._manager.get_configured_assets())

    def asset_source_events(
        self, to_block: int | str
    ) -> list[tuple[int, ChecksumAddress, ChecksumAddress]]:
        """Replay ``AssetPriceSourceUpdated`` logs as ``(block, asset, source)``."""
        logs = self._ctx.get_logs(
            contract_address=self._oracle_addr,
            topics=[ASSET_PRICE_SOURCE_UPDATED_TOPIC],
            from_block=0,
            to_block=to_block,
        )
        out: list[tuple[int, ChecksumAddress, ChecksumAddress]] = []
        for log in logs:
            asset, source = decode(["address", "address"], bytes(log["data"]))
            out.append((int(log["blockNumber"]), _addr(asset), _addr(source)))
        return out

    # -- oracle reads (reuse SDK wrappers) ----------------------------------
    def source_of(self, asset: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(self._oracle.get_source_of_asset_price(asset))

    def asset_price(self, asset: ChecksumAddress) -> Price | None:
        return self._safe(self._oracle.get_asset_price(asset))

    # -- oracle variant probes ---------------------------------------------
    def underlying_middleware(self) -> ChecksumAddress | None:
        """Manager variant: the global middleware zero-source assets delegate to."""
        return self._safe(self._manager.get_price_oracle_middleware())

    def chainlink_feed_registry(self) -> ChecksumAddress | None:
        return self._safe(self._oracle.chainlink_feed_registry())

    def delegate(self, oracle: ChecksumAddress) -> OracleMappingReader:
        """Sibling reader bound to another oracle on the same context/block."""
        return OracleMappingReader(self._ctx, oracle)

    # -- token metadata ----------------------------------------------------
    def symbol(self, token: ChecksumAddress) -> str | None:
        return self._safe(ERC20(self._ctx, token).symbol())

    def token_decimals(self, token: ChecksumAddress) -> int | None:
        return self._safe(ERC20(self._ctx, token).decimals())

    # -- feed probes -------------------------------------------------------
    def feed_decimals(self, source: ChecksumAddress) -> int | None:
        return self._safe(_Aggregator(self._ctx, source).decimals())

    def feed_description(self, source: ChecksumAddress) -> str | None:
        return self._safe(_Aggregator(self._ctx, source).description())

    def feed_latest_round_data(
        self, source: ChecksumAddress
    ) -> tuple[int, int, int, int, int] | None:
        return self._safe(_Aggregator(self._ctx, source).latest_round_data())

    def feed_version(self, source: ChecksumAddress) -> int | None:
        return self._safe(_Aggregator(self._ctx, source).version())

    def feed_aggregator(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_Aggregator(self._ctx, source).aggregator())

    def feed_phase_id(self, source: ChecksumAddress) -> int | None:
        return self._safe(_Aggregator(self._ctx, source).phase_id())

    def feed_vault(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_Erc4626Feed(self._ctx, source).vault())

    def feed_morpho_oracle(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_MorphoFeed(self._ctx, source).morpho_oracle())

    def feed_collateral_token(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_MorphoFeed(self._ctx, source).collateral_token())

    def feed_loan_token(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_MorphoFeed(self._ctx, source).loan_token())

    def morpho_oracle_price(self, morpho_oracle: ChecksumAddress) -> int | None:
        return self._safe(_MorphoOracle(self._ctx, morpho_oracle).price())

    def feed_asset_x(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_DualXrefFeed(self._ctx, source).asset_x())

    def feed_asset_x_asset_y_feed(
        self, source: ChecksumAddress
    ) -> ChecksumAddress | None:
        return self._safe(_DualXrefFeed(self._ctx, source).asset_x_asset_y_feed())

    def feed_asset_y_usd_feed(self, source: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_DualXrefFeed(self._ctx, source).asset_y_usd_feed())

    # -- ERC4626 vault reads ----------------------------------------------
    def vault_asset(self, vault: ChecksumAddress) -> ChecksumAddress | None:
        return self._safe(_Erc4626Vault(self._ctx, vault).asset())

    def vault_decimals(self, vault: ChecksumAddress) -> int | None:
        return self._safe(_Erc4626Vault(self._ctx, vault).decimals())

    def vault_convert_to_assets(
        self, vault: ChecksumAddress, shares: int
    ) -> int | None:
        return self._safe(_Erc4626Vault(self._ctx, vault).convert_to_assets(shares))


# ---------------------------------------------------------------------------
# Pure helpers
# ---------------------------------------------------------------------------


def collapse_sources(
    events: Iterable[tuple[int, ChecksumAddress, ChecksumAddress]],
) -> dict[ChecksumAddress, ChecksumAddress]:
    """Collapse the event history into the current ``asset -> source`` map.

    Mirrors on-chain storage semantics: the latest block wins per asset, and
    on a same-block tie the later log wins (log order within a block is
    execution order); a source set to the zero address removes the asset.
    """
    latest: dict[ChecksumAddress, tuple[int, ChecksumAddress]] = {}
    for block, asset, source in events:
        current = latest.get(asset)
        if current is None or block >= current[0]:
            latest[asset] = (block, source)
    return {
        asset: source
        for asset, (_, source) in latest.items()
        if source.lower() != ZERO_ADDRESS
    }


def normalize_wad(amount: int, decimals: int) -> str:
    """Rescale ``amount`` (with ``decimals`` decimals) to an 18-decimal integer."""
    if decimals <= _WAD:
        return str(amount * 10 ** (_WAD - decimals))
    return str(amount // 10 ** (decimals - _WAD))


def _price_block(price: Price | None) -> OraclePrice | None:
    """Format a Price into an :class:`OraclePrice` block; an unread price stays None."""
    if price is None:
        return None
    return OraclePrice(
        raw=str(price.amount),
        decimals=int(price.decimals),
        normalized_wad=normalize_wad(price.amount, int(price.decimals)),
    )


# ---------------------------------------------------------------------------
# Recursive resolution
# ---------------------------------------------------------------------------


def _partial(
    asset: ChecksumAddress,
    symbol: str | None,
    decimals: int | None,
    source: ChecksumAddress | None,
    source_type: str,
    reason: str,
    path: list[str],
    price: Price | None = None,
) -> OracleNode:
    """Build a partial node — uniform shape, ``status: partial`` + a reason."""
    return OracleNode(
        asset=asset,
        symbol=symbol,
        decimals=decimals,
        source=source,
        price=_price_block(price),
        source_type=source_type,
        path=path,
        status="partial",
        reason=reason,
    )


def _utc_twin(epoch: int | None) -> str | None:
    """ISO-8601 UTC rendering of a round timestamp.

    Null for a missing round (``None`` in) and for epoch 0: wrapper/composed
    feeds return synthetic zero timestamps, and rendering 1970-01-01 would
    manufacture a staleness illusion — the raw int keeps full fidelity.
    """
    if not epoch:
        return None
    return datetime.fromtimestamp(epoch, tz=timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _aggregator_detail(
    description: str | None,
    rnd: tuple[int, int, int, int, int] | None,
    feed_decimals: int | None,
) -> dict[str, Any]:
    """Uniform metadata block for one aggregator-compatible feed read.

    Raw values only — timestamps are echoed as-is with no staleness judgment
    (wrapper/composed feeds legitimately return synthetic zeros; their
    ``*_utc`` twins are null then), and ``description`` is null when the feed
    does not implement ``description()``.
    """
    # Round ids are uint80 and exceed 2^53 on proxy feeds (phaseId << 64 | id),
    # so they travel as strings. started_at/updated_at stay ints deliberately:
    # realistic epochs sit far below 2^53, matching the int block_timestamp
    # elsewhere in the output.
    started_at = int(rnd[2]) if rnd else None
    updated_at = int(rnd[3]) if rnd else None
    return {
        "description": description,
        "round_id": str(rnd[0]) if rnd else None,
        "answer": str(rnd[1]) if rnd else None,
        "decimals": feed_decimals,
        "started_at": started_at,
        "started_at_utc": _utc_twin(started_at),
        "updated_at": updated_at,
        "updated_at_utc": _utc_twin(updated_at),
        "answered_in_round": str(rnd[4]) if rnd else None,
    }


def _resolve_chainlink(
    reader: OracleMappingReader,
    node: OracleNode,
    label: str,
    source: ChecksumAddress,
    rnd: tuple[int, int, int, int, int],
    *,
    has_foreign_getter: bool,
) -> OracleNode:
    """Resolve an aggregator-compatible leaf, grading the identity evidence.

    ``ChainlinkAggregator`` is claimed only when the full AggregatorV3Interface
    answers (``latestRoundData`` + ``decimals`` + ``description`` +
    ``version``) and nothing contradicts it: no foreign getter answered, the
    round is not degenerate (zero ``roundId``/``updatedAt`` are synthetic
    values real aggregators never return), and ``description`` is non-empty.
    Anything weaker is ``chainlink_style`` — still ``resolved`` (the structure
    is explained), but the label tells consumers to verify the feed's identity
    themselves if it matters.
    """
    description = reader.feed_description(source)
    feed_decimals = reader.feed_decimals(source)
    confirmed = (
        not has_foreign_getter
        and feed_decimals is not None
        and bool(description)  # unimplemented and empty both demote
        and rnd[0] != 0  # degenerate roundId
        and rnd[3] != 0  # degenerate updatedAt
        # gate evidence only, never part of the metadata block; probed last so
        # the extra read is skipped once anything else already demoted
        and reader.feed_version(source) is not None
    )
    node.source_type = TYPE_CHAINLINK if confirmed else TYPE_CHAINLINK_STYLE
    node.path = [label, "Chainlink feed" if confirmed else "Chainlink-style feed"]
    node.source_detail = {
        **_aggregator_detail(description, rnd, feed_decimals),
        # proxy-deployment evidence — uniform keys, null when unanswered
        "aggregator": reader.feed_aggregator(source),
        "phase_id": reader.feed_phase_id(source),
    }
    node.status = "resolved"
    return node


def _resolve_erc4626(
    reader: OracleMappingReader,
    node: OracleNode,
    label: str,
    vault: ChecksumAddress,
    underlying: ChecksumAddress,
    depth: int,
    visited: set[str],
    max_depth: int,
) -> OracleNode:
    """Resolve an ERC4626 feed: share→underlying rate, then recurse on the underlying."""
    share_decimals = reader.vault_decimals(vault)
    rate = (
        reader.vault_convert_to_assets(vault, 10**share_decimals)
        if share_decimals is not None
        else None
    )
    node.source_type = TYPE_ERC4626
    node.source_detail = {
        "vault": vault,
        "underlying": underlying,
        "share_decimals": share_decimals,
        "rate": str(rate) if rate is not None else None,
    }
    dep = _resolve(reader, underlying, depth + 1, visited, max_depth)
    node.dependencies = [dep]
    node.path = [label, "convertToAssets(1 share)", *dep.path]
    if rate is None:
        # No derived price without the share→asset rate — same "complete data
        # or partial" standard as dual-xref; the dep is still attached.
        node.status = "partial"
        node.reason = "erc4626_rate_unreadable"
        return node
    node.status = "resolved"
    return node


def _resolve_morpho(
    reader: OracleMappingReader,
    node: OracleNode,
    label: str,
    source: ChecksumAddress,
    morpho_oracle: ChecksumAddress,
    loan: ChecksumAddress,
    depth: int,
    visited: set[str],
    max_depth: int,
) -> OracleNode:
    """Resolve a Morpho collateral feed: market price, then recurse on the loan token."""
    collateral = reader.feed_collateral_token(source)
    price = reader.morpho_oracle_price(morpho_oracle)
    node.source_type = TYPE_MORPHO
    node.source_detail = {
        "morpho_oracle": morpho_oracle,
        "collateral_token": collateral,
        "loan_token": loan,
        "morpho_price": str(price) if price is not None else None,
    }
    dep = _resolve(reader, loan, depth + 1, visited, max_depth)
    node.dependencies = [dep]
    node.path = [label, "Morpho collateral/loan oracle", *dep.path]
    node.status = "resolved"
    return node


def _resolve_dual_xref(
    reader: OracleMappingReader,
    node: OracleNode,
    label: str,
    asset_x: ChecksumAddress,
    xy_feed: ChecksumAddress,
    y_usd_feed: ChecksumAddress,
) -> OracleNode:
    """Resolve a dual cross-reference feed: X/USD = (X/Y feed) × (Y/USD feed)."""
    xy_round = reader.feed_latest_round_data(xy_feed)
    xy_decimals = reader.feed_decimals(xy_feed)
    y_usd_round = reader.feed_latest_round_data(y_usd_feed)
    y_usd_decimals = reader.feed_decimals(y_usd_feed)

    derived: str | None = None
    if (
        xy_round
        and y_usd_round
        and xy_decimals is not None
        and y_usd_decimals is not None
    ):
        derived = str(
            int(normalize_wad(xy_round[1], xy_decimals))
            * int(normalize_wad(y_usd_round[1], y_usd_decimals))
            // 10**_WAD
        )

    node.source_type = TYPE_DUAL_XREF
    node.source_detail = {
        "asset_x": asset_x,
        "asset_x_asset_y_feed": {
            "address": xy_feed,
            **_aggregator_detail(
                reader.feed_description(xy_feed), xy_round, xy_decimals
            ),
        },
        "asset_y_usd_feed": {
            "address": y_usd_feed,
            **_aggregator_detail(
                reader.feed_description(y_usd_feed), y_usd_round, y_usd_decimals
            ),
        },
        "derived_price_wad": derived,
    }
    node.path = [
        label,
        "DualCrossReferencePriceFeed",
        "ASSET_X/ASSET_Y feed",
        "ASSET_Y/USD feed",
    ]
    if derived is None:
        node.status = "partial"
        node.reason = "dual_xref_component_unreadable"
        return node
    node.status = "resolved"
    return node


def _resolve_middleware_fallback(
    reader: OracleMappingReader,
    asset: ChecksumAddress,
    symbol: str | None,
    decimals: int | None,
    source: ChecksumAddress | None,
    label: str,
    price: Price | None,
    depth: int,
    visited: set[str],
    max_depth: int,
) -> OracleNode:
    """Explain a zero/unreadable source instead of declaring the asset dead.

    On a ``PriceOracleMiddlewareManager`` a zero source means "no per-vault
    override" — the price comes from the underlying global middleware, so the
    asset is re-resolved there to surface the real feed. Without a manager, a
    readable price means the middleware priced the asset through its own
    internal fallback (the Chainlink FeedRegistry on mainnet).
    """
    underlying_middleware = reader.underlying_middleware()
    if (
        underlying_middleware is not None
        and underlying_middleware.lower() == ZERO_ADDRESS
    ):
        underlying_middleware = None
    if underlying_middleware is None and price is None:
        return _partial(
            asset,
            symbol,
            decimals,
            source,
            TYPE_UNKNOWN,
            "no_source_configured",
            [label],
            price,
        )
    node = OracleNode(
        asset=asset,
        symbol=symbol,
        decimals=decimals,
        source=source,
        price=_price_block(price),
    )
    node.source_type = TYPE_MIDDLEWARE_FALLBACK
    node.status = "resolved"
    if underlying_middleware is not None:
        # Same asset, different oracle — must not be in `visited` yet; runaway
        # manager→manager chains are bounded by max_depth instead.
        dep = _resolve(
            reader.delegate(underlying_middleware),
            asset,
            depth + 1,
            visited,
            max_depth,
        )
        node.dependencies = [dep]
        # dep re-resolves the same asset, so drop its leading label from the path.
        node.path = [label, "middleware fallback", *dep.path[1:]]
        node.source_detail = {
            "delegated_to": underlying_middleware,
            "chainlink_feed_registry": None,
        }
        if price is None:
            # On-chain the manager's zero-source getAssetPrice IS this
            # delegation, so its own read failing while the hop answers means
            # an anomaly (transient read, or a feed answering 0/negative) —
            # surface it instead of reporting resolved with a null price.
            node.status = "partial"
            node.reason = "manager_price_unreadable"
        return node
    # Not a manager, yet the price read succeeded (else the no_source_configured
    # early return above would have fired): the middleware's internal fallback
    # (FeedRegistry on mainnet) priced it — report a leaf.
    registry = reader.chainlink_feed_registry()
    if registry is not None and registry.lower() == ZERO_ADDRESS:
        registry = None
    node.path = [label, "middleware fallback"]
    node.source_detail = {"delegated_to": None, "chainlink_feed_registry": registry}
    return node


def _classify_and_resolve(
    reader: OracleMappingReader,
    node: OracleNode,
    label: str,
    source: ChecksumAddress,
    depth: int,
    visited: set[str],
    max_depth: int,
) -> OracleNode:
    """Classify the source by interface probing and dispatch to its type resolver."""
    # Probe most specific first; Chainlink's interface is shared by the others.
    # Partial getter match = wrong type hypothesis, not a broken feed — fall
    # through (a foreign getter answering caps the leaf at chainlink_style).
    # New types: see the "Adding a feed type" checklist in the module docstring.
    asset_x = reader.feed_asset_x(source)
    if asset_x is not None:
        xy_feed = reader.feed_asset_x_asset_y_feed(source)
        y_usd_feed = reader.feed_asset_y_usd_feed(source)
        if xy_feed is not None and y_usd_feed is not None:
            return _resolve_dual_xref(reader, node, label, asset_x, xy_feed, y_usd_feed)
    morpho_oracle = reader.feed_morpho_oracle(source)
    if morpho_oracle is not None:
        # gate: the type-defining loanToken() must answer too — a lone
        # morphoOracle() hit is a false matcher, not a broken Morpho feed
        loan = reader.feed_loan_token(source)
        if loan is not None:
            return _resolve_morpho(
                reader,
                node,
                label,
                source,
                morpho_oracle,
                loan,
                depth,
                visited,
                max_depth,
            )
    vault = reader.feed_vault(source)
    if vault is not None:
        # gate: vault() is a common method name — only a vault answering
        # asset() defines the ERC4626 hypothesis
        underlying = reader.vault_asset(vault)
        if underlying is not None:
            return _resolve_erc4626(
                reader, node, label, vault, underlying, depth, visited, max_depth
            )
    rnd = reader.feed_latest_round_data(source)
    if rnd is not None:
        has_foreign_getter = (
            asset_x is not None or morpho_oracle is not None or vault is not None
        )
        return _resolve_chainlink(
            reader, node, label, source, rnd, has_foreign_getter=has_foreign_getter
        )
    node.source_type = TYPE_UNKNOWN
    node.status = "partial"
    node.reason = "unsupported_custom_feed"
    node.path = [label, "custom feed (unresolved)"]
    return node


def _resolve(
    reader: OracleMappingReader,
    asset: ChecksumAddress,
    depth: int,
    visited: set[str],
    max_depth: int,
) -> OracleNode:
    """Resolve one asset into a node (recursively for derived feeds)."""
    symbol = reader.symbol(asset)
    decimals = reader.token_decimals(asset)
    label = symbol or asset
    key = asset.lower()

    # The oracle's getAssetPrice is authoritative and independent of how far we
    # can explain the feed, so read it up front — even cycle/depth-capped nodes
    # carry the real price.
    price = reader.asset_price(asset)

    if key in visited:
        return _partial(
            asset,
            symbol,
            decimals,
            None,
            TYPE_UNKNOWN,
            "cycle_detected",
            [label],
            price,
        )
    if depth > max_depth:
        return _partial(
            asset,
            symbol,
            decimals,
            None,
            TYPE_UNKNOWN,
            "max_depth_exceeded",
            [label],
            price,
        )

    source = reader.source_of(asset)
    if source is None or source.lower() == ZERO_ADDRESS:
        return _apply_dependency_status(
            _resolve_middleware_fallback(
                reader,
                asset,
                symbol,
                decimals,
                source,
                label,
                price,
                depth,
                visited,
                max_depth,
            )
        )

    node = OracleNode(
        asset=asset,
        symbol=symbol,
        decimals=decimals,
        source=source,
        price=_price_block(price),
    )
    visited.add(key)
    try:
        return _apply_dependency_status(
            _classify_and_resolve(
                reader, node, label, source, depth, visited, max_depth
            )
        )
    finally:
        visited.discard(key)


def _apply_dependency_status(node: OracleNode) -> OracleNode:
    """Demote a ``resolved`` parent over any non-``resolved`` direct child.

    Children are already final (post-order recursion), so direct children
    suffice; an own ``partial`` outranks the dependency-driven demotion.
    """
    if node.status == "resolved" and any(
        dep.status != "resolved" for dep in node.dependencies
    ):
        node.status = "partially_resolved"
    return node


def resolve_asset(
    reader: OracleMappingReader, asset: ChecksumAddress, max_depth: int
) -> OracleNode:
    """Public entry: resolve a single asset into a node tree."""
    return _resolve(reader, asset, 0, set(), max_depth)


def _collect_unresolved(nodes: Iterable[OracleNode]) -> list[OracleNode]:
    """Flatten the tree to every ``partial`` node (own resolution incomplete).

    ``partially_resolved`` parents are deliberately excluded — they
    self-describe, and listing them would bury the actual failures.
    """
    out: list[OracleNode] = []
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if node.status == "partial":
            out.append(node)
        stack.extend(node.dependencies)
    return out


def _mapping_status(roots: list[OracleNode]) -> MappingStatus:
    """Roll the root statuses up; empty ``roots`` is vacuously ``resolved``.

    ``unresolved`` is reserved for total failure (every root ``partial``) — a
    ``partially_resolved`` root still explains its own feed, so it keeps the
    mapping at ``partially_resolved``.
    """
    if all(node.status == "resolved" for node in roots):
        return "resolved"
    if all(node.status == "partial" for node in roots):
        return "unresolved"
    return "partially_resolved"


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


def _enumerate_assets(
    reader: OracleMappingReader, effective_block: int
) -> tuple[list[ChecksumAddress], AssetSource]:
    """Configured assets via ``getConfiguredAssets`` or event-replay fallback."""
    assets = reader.configured_assets()
    if assets is not None:
        return assets, "getConfiguredAssets"
    events = reader.asset_source_events(effective_block)
    return list(collapse_sources(events)), "events"


def build_oracle_mapping(
    ctx: Web3Context,
    vault_address: ChecksumAddress,
    effective_block: int,
    max_depth: int = 6,
) -> OracleMapping:
    """Build the full oracle mapping for ``vault_address``.

    ``ctx`` must already be pinned to ``effective_block`` (the caller sets
    ``ctx.default_block``); the block is echoed in the output.
    """
    vault = PlasmaVault(ctx, vault_address)
    oracle_addr = vault.get_price_oracle_middleware_address().call()
    asset_addr = vault.underlying_asset_address().call()

    try:
        vault_name = vault.name().call()
    except Exception:
        vault_name = None

    reader = OracleMappingReader(ctx, oracle_addr)
    assets, asset_source = _enumerate_assets(reader, effective_block)

    configured: list[OracleNode] = []
    for asset in assets:
        try:
            configured.append(resolve_asset(reader, asset, max_depth))
        except Exception:
            configured.append(
                _partial(
                    asset,
                    reader.symbol(asset),
                    reader.token_decimals(asset),
                    None,
                    TYPE_UNKNOWN,
                    "resolution_error",
                    [asset],
                )
            )

    return OracleMapping(
        vault=vault_address,
        vault_name=vault_name,
        asset=OracleAsset(
            address=asset_addr,
            symbol=reader.symbol(asset_addr),
            decimals=reader.token_decimals(asset_addr),
        ),
        price_oracle=oracle_addr,
        block_number=effective_block,
        asset_source=asset_source,
        status=_mapping_status(configured),
        configured_assets=configured,
        unresolved=_collect_unresolved(configured),
    )

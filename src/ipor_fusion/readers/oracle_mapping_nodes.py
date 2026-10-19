"""The oracle mapping's result model (field names are the JSON keys of the CLI
``--json`` / MCP output; renaming one is a client-visible schema change), its
``source_type`` labels and the leaf resolvers that never recurse (Chainlink-tier
and dual-cross-reference feeds)."""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from typing import Any

from eth_typing import ChecksumAddress

from ipor_fusion.readers.oracle_mapping_reader import OracleMappingReader
from ipor_fusion.types import (
    ZERO_ADDRESS,
    AssetSource,
    MappingStatus,
    NodeStatus,
    PlasmaVaultAddress,
    Price,
)

# source_type values (also the human label in the path / output). Adding a
# type? Follow the "Adding a feed type" checklist in readers/oracle_mapping.py.
TYPE_CHAINLINK = "ChainlinkAggregator"
TYPE_CHAINLINK_STYLE = "chainlink_style"
TYPE_ERC4626 = "ERC4626PriceFeed"
TYPE_MORPHO = "CollateralTokenOnMorphoMarketPriceFeed"
TYPE_DUAL_XREF = "DualCrossReferencePriceFeed"
TYPE_MIDDLEWARE_FALLBACK = "middleware_fallback"
TYPE_UNKNOWN = "custom_unknown"


# The 18-decimal fixed-point scale normalized prices are rescaled to.
WAD_DECIMALS = 18


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

    vault: PlasmaVaultAddress
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
    if decimals <= WAD_DECIMALS:
        return str(amount * 10 ** (WAD_DECIMALS - decimals))
    return str(amount // 10 ** (decimals - WAD_DECIMALS))


def _price_block(price: Price | None) -> OraclePrice | None:
    """Format a Price into an :class:`OraclePrice` block; an unread price stays None."""
    if price is None:
        return None
    return OraclePrice(
        raw=str(price.amount),
        decimals=int(price.decimals),
        normalized_wad=normalize_wad(price.amount, int(price.decimals)),
    )


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
    return datetime.fromtimestamp(epoch, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


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
            // 10**WAD_DECIMALS
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

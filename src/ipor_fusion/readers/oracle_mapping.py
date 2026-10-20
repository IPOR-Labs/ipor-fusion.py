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
  (``PriceOracleMiddleware``, ``PriceOracleMiddlewareManager``, ``ERC20``,
  ``PlasmaVault``); only the feed-probe reads are added here.

Adding a feed type:

1. Wrapper class (``ContractWrapper``) exposing its getters.
2. Revert-safe reader probes on :class:`OracleMappingReader` (``None`` on
   failure); both live in ``oracle_mapping_reader``.
3. ``TYPE_*`` constant (``oracle_mapping_nodes``) + ``_resolve_*`` function
   (``oracle_mapping_nodes`` for a leaf, here when it recurses); recurse only into
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

from eth_typing import ChecksumAddress

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.readers.oracle_mapping_nodes import (
    TYPE_CHAINLINK,
    TYPE_CHAINLINK_STYLE,
    TYPE_DUAL_XREF,
    TYPE_ERC4626,
    TYPE_MIDDLEWARE_FALLBACK,
    TYPE_MORPHO,
    TYPE_UNKNOWN,
    WAD_DECIMALS,
    ZERO_ADDRESS,
    OracleAsset,
    OracleMapping,
    OracleNode,
    OraclePrice,
    _apply_dependency_status,
    _collect_unresolved,
    _mapping_status,
    _partial,
    _price_block,
    _resolve_chainlink,
    _resolve_dual_xref,
    collapse_sources,
    normalize_wad,
)
from ipor_fusion.readers.oracle_mapping_reader import (
    ASSET_PRICE_SOURCE_UPDATED_TOPIC,
    OracleMappingReader,
)
from ipor_fusion.types import (
    AssetSource,
    PlasmaVaultAddress,
    Price,
)

__all__ = [
    "ASSET_PRICE_SOURCE_UPDATED_TOPIC",
    "TYPE_CHAINLINK",
    "TYPE_CHAINLINK_STYLE",
    "TYPE_DUAL_XREF",
    "TYPE_ERC4626",
    "TYPE_MIDDLEWARE_FALLBACK",
    "TYPE_MORPHO",
    "TYPE_UNKNOWN",
    "WAD_DECIMALS",
    "ZERO_ADDRESS",
    "OracleAsset",
    "OracleMapping",
    "OracleMappingReader",
    "OracleNode",
    "OraclePrice",
    "build_oracle_mapping",
    "collapse_sources",
    "normalize_wad",
    "resolve_asset",
]


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


def resolve_asset(
    reader: OracleMappingReader, asset: ChecksumAddress, max_depth: int
) -> OracleNode:
    """Public entry: resolve a single asset into a node tree."""
    return _resolve(reader, asset, 0, set(), max_depth)


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
    vault: PlasmaVaultAddress,
    *,
    effective_block: int,
    max_depth: int = 6,
) -> OracleMapping:
    """Build the full oracle mapping for ``vault``.

    ``ctx`` must already be pinned to ``effective_block`` (pass
    ``ctx.at_block(effective_block)``); the block is echoed in the output. Raises
    `UnsupportedVaultVersionError` for a vault that exposes no price-oracle
    getter.
    """
    pv = PlasmaVault(vault)
    oracle_addr = pv.read_price_oracle_address(ctx)
    asset_addr = pv.underlying_asset_address().call(ctx)

    try:
        vault_name = pv.name().call(ctx)
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
        vault=vault,
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

"""Resolve how a Plasma Vault prices every configured asset at a given block.

Powers the ``fusion vault oracle-mapping`` CLI command and the
``vault_oracle_mapping`` MCP tool. For each asset the vault's price oracle
knows about, this walks the *source* price feed, classifies its type by
interface probing, reads the effective price, and recursively resolves any
feed that derives its price from another asset (ERC4626 wrappers, Morpho
collateral feeds). Unknown feeds are reported as partial, never dropped.

Design:
- All network access goes through :class:`OracleMappingReader` (the single
  seam). Every read is "safe": a revert/decode failure yields ``None`` so one
  bad asset never aborts the whole map. The pure logic (classification,
  recursion, event collapse) takes a reader and is unit-testable with a fake.
- SDK primitives are reused for the reads that already exist
  (``PriceOracleMiddleware``, ``ERC20``, ``PlasmaVault``); only the feed-probe
  reads and ``getConfiguredAssets`` — which they lack — are added here.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from typing import Any

from eth_abi import decode
from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.oracle import PriceOracleMiddleware
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.types import Price

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# keccak256("AssetPriceSourceUpdated(address,address)") — both args are in the
# log data (neither is indexed), so only topic0 is used to filter.
ASSET_PRICE_SOURCE_UPDATED_TOPIC = (
    "0xe6c35d0425da27d8f991ada353619254c33e5094fc7e19154e02feb391937390"
)

# source_type values (also the human label in the path / output).
TYPE_CHAINLINK = "ChainlinkAggregator"
TYPE_ERC4626 = "ERC4626PriceFeed"
TYPE_MORPHO = "CollateralTokenOnMorphoMarketPriceFeed"
TYPE_UNKNOWN = "custom_unknown"

_WAD = 18


# ---------------------------------------------------------------------------
# Result dataclasses (field names match the historical JSON keys — IL-7779
# reshapes the schema later, keep them identical here)
# ---------------------------------------------------------------------------


@dataclass(slots=True)
class OraclePrice:
    """Uniform price block: raw oracle answer plus its 18-decimal rescale."""

    raw: str | None
    decimals: int | None
    normalized_wad: str | None


@dataclass(slots=True)
class OracleNode:
    """One asset's resolved price feed (recursive for derived feeds)."""

    asset: ChecksumAddress
    symbol: str | None
    decimals: int | None
    source: ChecksumAddress | None
    price: OraclePrice
    source_type: str = TYPE_UNKNOWN
    path: list[str] = field(default_factory=list)
    status: str = "partial"
    reason: str | None = None
    source_detail: dict[str, Any] | None = None
    dependencies: list[OracleNode] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        """JSON-ready shape; every key always present (null/empty when unused)."""
        return asdict(self)


@dataclass(slots=True)
class OracleMapping:
    """Full oracle mapping for a vault at a pinned block."""

    vault: ChecksumAddress
    vault_name: str | None
    asset: dict[str, Any]
    price_oracle: ChecksumAddress
    block_number: int
    asset_source: str
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


class _Aggregator(ContractWrapper):
    def decimals(self) -> Call[int]:
        return self._view("decimals()", output_types=["uint8"], decoder=int)

    def latest_round_data(self) -> Call[tuple[int, int, int, int, int]]:
        return self._view(
            "latestRoundData()",
            output_types=["uint80", "int256", "uint256", "uint256", "uint80"],
        )


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

    # -- token metadata ----------------------------------------------------
    def symbol(self, token: ChecksumAddress) -> str | None:
        return self._safe(ERC20(self._ctx, token).symbol())

    def token_decimals(self, token: ChecksumAddress) -> int | None:
        return self._safe(ERC20(self._ctx, token).decimals())

    # -- feed probes -------------------------------------------------------
    def feed_decimals(self, source: ChecksumAddress) -> int | None:
        return self._safe(_Aggregator(self._ctx, source).decimals())

    def feed_latest_round_data(
        self, source: ChecksumAddress
    ) -> tuple[int, int, int, int, int] | None:
        return self._safe(_Aggregator(self._ctx, source).latest_round_data())

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

    Latest block wins per asset (later log wins on a block tie); a source set to
    the zero address removes the asset. See the IL-7555 plan for the semantics.
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


def _price_block(price: Price | None) -> OraclePrice:
    """Format a Price (or None) into the uniform :class:`OraclePrice` block."""
    if price is None:
        return OraclePrice(raw=None, decimals=None, normalized_wad=None)
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


def _resolve_chainlink(
    reader: OracleMappingReader,
    node: OracleNode,
    label: str,
    source: ChecksumAddress,
) -> OracleNode:
    """Resolve a Chainlink-style leaf feed (latestRoundData + decimals)."""
    rnd = reader.feed_latest_round_data(source)
    feed_decimals = reader.feed_decimals(source)
    node.source_type = TYPE_CHAINLINK
    node.path = [label, "Chainlink feed"]
    node.source_detail = {
        "answer": str(rnd[1]) if rnd else None,
        "answer_decimals": feed_decimals,
        "updated_at": int(rnd[3]) if rnd else None,
    }
    node.status = "resolved"
    return node


def _resolve_erc4626(
    reader: OracleMappingReader,
    node: OracleNode,
    label: str,
    vault: ChecksumAddress,
    depth: int,
    visited: set[str],
    max_depth: int,
) -> OracleNode:
    """Resolve an ERC4626 feed: share→underlying rate, then recurse on the underlying."""
    underlying = reader.vault_asset(vault)
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
    if underlying is None:
        node.status = "partial"
        node.reason = "erc4626_underlying_unreadable"
        node.path = [label, "convertToAssets(1 share)"]
        return node
    dep = _resolve(reader, underlying, depth + 1, visited, max_depth)
    node.dependencies = [dep]
    node.path = [label, "convertToAssets(1 share)", *dep.path]
    node.status = "resolved"
    return node


def _resolve_morpho(
    reader: OracleMappingReader,
    node: OracleNode,
    label: str,
    source: ChecksumAddress,
    morpho_oracle: ChecksumAddress,
    depth: int,
    visited: set[str],
    max_depth: int,
) -> OracleNode:
    """Resolve a Morpho collateral feed: market price, then recurse on the loan token."""
    collateral = reader.feed_collateral_token(source)
    loan = reader.feed_loan_token(source)
    price = reader.morpho_oracle_price(morpho_oracle)
    node.source_type = TYPE_MORPHO
    node.source_detail = {
        "morpho_oracle": morpho_oracle,
        "collateral_token": collateral,
        "loan_token": loan,
        "morpho_price": str(price) if price is not None else None,
    }
    if loan is None:
        node.status = "partial"
        node.reason = "morpho_loan_token_unreadable"
        node.path = [label, "Morpho collateral/loan oracle"]
        return node
    dep = _resolve(reader, loan, depth + 1, visited, max_depth)
    node.dependencies = [dep]
    node.path = [label, "Morpho collateral/loan oracle", *dep.path]
    node.status = "resolved"
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
    morpho_oracle = reader.feed_morpho_oracle(source)
    if morpho_oracle is not None:
        return _resolve_morpho(
            reader, node, label, source, morpho_oracle, depth, visited, max_depth
        )
    vault = reader.feed_vault(source)
    if vault is not None:
        return _resolve_erc4626(reader, node, label, vault, depth, visited, max_depth)
    if reader.feed_latest_round_data(source) is not None:
        return _resolve_chainlink(reader, node, label, source)
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
    visited.add(key)
    try:
        return _classify_and_resolve(
            reader, node, label, source, depth, visited, max_depth
        )
    finally:
        visited.discard(key)


def resolve_asset(
    reader: OracleMappingReader, asset: ChecksumAddress, max_depth: int
) -> OracleNode:
    """Public entry: resolve a single asset into a node tree."""
    return _resolve(reader, asset, 0, set(), max_depth)


def _collect_unresolved(nodes: Iterable[OracleNode]) -> list[OracleNode]:
    """Flatten the tree to every node whose status is not ``resolved``."""
    out: list[OracleNode] = []
    stack = list(nodes)
    while stack:
        node = stack.pop()
        if node.status != "resolved":
            out.append(node)
        stack.extend(node.dependencies)
    return out


# ---------------------------------------------------------------------------
# Top-level builder
# ---------------------------------------------------------------------------


def _enumerate_assets(
    reader: OracleMappingReader, effective_block: int
) -> tuple[list[ChecksumAddress], str]:
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
        asset={
            "address": asset_addr,
            "symbol": reader.symbol(asset_addr),
            "decimals": reader.token_decimals(asset_addr),
        },
        price_oracle=oracle_addr,
        block_number=effective_block,
        asset_source=asset_source,
        configured_assets=configured,
        unresolved=_collect_unresolved(configured),
    )

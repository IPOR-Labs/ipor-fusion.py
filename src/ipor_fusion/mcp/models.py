"""Pydantic response models for MCP tools.

Tool functions in mcp/server.py return instances of these models. FastMCP
serializes them via model_dump() and exposes their JSON schema to the LLM.

Design notes:
- Top-level shapes are strictly typed for LLM schema clarity.
- Runtime imports stay pydantic-only — plus `ipor_fusion.types`, a
  dependency-light typing leaf (shared Literal vocabularies live there).
  Heavier SDK types appear only under TYPE_CHECKING (or deferred inside
  methods), and on-chain addresses are plain `str`, not eth_typing NewTypes.
- Truly dynamic sections (substrates keyed by market label, per-protocol
  position breakdowns) use dict[str, Any] / list[dict[str, Any]] — modelling
  every variant would be brittle without meaningful LLM-side benefit.
- Models use extra="forbid": any new field added to _build_json_output that
  is not declared here will fail VaultInfoResponse.model_validate(). Drift
  is caught at test time by the producer contract check in
  test_cli_commands.py::TestVaultInfoJson::test_json_output, which validates
  the real _build_json_output dict against this model. The samples in
  test_mcp_models.py document the expected shape but are hand-maintained —
  on their own they cannot detect producer drift.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

from pydantic import BaseModel, ConfigDict, Field

from ipor_fusion.types import AssetSource, MappingStatus, NodeStatus

if TYPE_CHECKING:
    from ipor_fusion.core.access import RoleAccount
    from ipor_fusion.readers.oracle_mapping import OracleMapping, OracleNode


class _Base(BaseModel):
    model_config = ConfigDict(extra="forbid")


# ---------------------------------------------------------------------------
# Shared building blocks
# ---------------------------------------------------------------------------


class Amount(_Base):
    """Token amount with both raw and formatted representations."""

    raw: int | str = Field(description="Raw on-chain integer (or 'unlimited').")
    formatted: str = Field(description="Human-readable decimal string.")
    usd: float | None = Field(
        default=None, description="USD value; null when no oracle price."
    )


class AmountWithPercent(Amount):
    percent: float | None = Field(
        default=None, description="Delta as percent of on-chain total."
    )


class AssetInfo(_Base):
    address: str
    symbol: str
    decimals: int
    price_usd: float | None = Field(
        default=None, description="Underlying-asset USD price; null when no oracle."
    )


class Managers(_Base):
    access: str | None
    price_oracle: str | None
    rewards: str | None
    withdraw: str | None


class FuseEntry(_Base):
    address: str
    contract: str = Field(
        description="Contract name resolved via Etherscan; '?' if unknown."
    )
    market_id: int | None = Field(
        default=None,
        description="Market this fuse serves; absent when the fuse exposes none.",
    )
    market: str | None = Field(
        default=None,
        description="Human-readable market name (falls back to the id as string).",
    )


class BalanceFuseEntry(_Base):
    """A balance fuse entry with optional protocol-specific breakdown.

    `position_breakdown` shape depends on the protocol (Morpho per-market or
    Aave V3 per-asset) and is intentionally untyped here — it is rendered
    verbatim from _build_json_output.
    """

    market: str
    market_id: int
    balance: Amount
    fuse: str
    contract: str
    pct_of_total: float | None = Field(default=None)
    depends_on: list[str] | None = Field(default=None)
    position_breakdown: list[dict[str, Any]] | None = Field(default=None)


class ERC20Entry(_Base):
    """ERC-20 balance entry; field set varies by data availability."""

    address: str
    symbol: str
    decimals: int | None = None
    balance: Amount | None = None
    price_usd: float | None = None
    usd_value: float | None = None
    note: str | None = None


class Reconciliation(_Base):
    balance_fuses_total: Amount
    underlying_on_vault: Amount
    erc20_direct_total: Amount
    sum: Amount
    on_chain_total_assets: Amount
    delta: AmountWithPercent
    pending_withdrawals: Amount
    implied_market_total: Amount
    market_storage_divergence: int


class LendingMarketHealth(_Base):
    protocol: str
    market_id: str | int
    market_name: str
    current_ltv: float | None
    max_ltv: float | None
    health_factor: float | None
    total_collateral_usd: float | None
    total_debt_usd: float | None
    ltv_usage_percent: float | None
    is_warning: bool
    is_critical: bool


class LendingHealth(_Base):
    markets: list[LendingMarketHealth]
    worst_ltv_usage_percent: float | None


class HealthCheck(_Base):
    ok: list[str] = Field(
        description="Passing-check lines (one per healthy check), not a boolean."
    )
    warnings: list[str]
    criticals: list[str]


# ---------------------------------------------------------------------------
# Tool response models
# ---------------------------------------------------------------------------


class ActionResult(_Base):
    """Generic 'operation succeeded' message with the human-readable detail."""

    message: str


class VaultListEntry(_Base):
    address: str
    label: str
    chain: str
    chain_id: int


class ConfigShowResponse(_Base):
    providers: dict[str, str] = Field(
        description="Map of chain_id (string) to RPC provider URL."
    )
    vaults: list[VaultListEntry]
    etherscan_api_key: str | None = Field(
        default=None,
        description="Masked ('***') when set, null when unset.",
    )


# ---------------------------------------------------------------------------
# Role-account models
# ---------------------------------------------------------------------------


class RoleAccountEntry(_Base):
    """One confirmed role membership on a vault's AccessManager."""

    account: str
    role_id: int
    role_name: str = Field(
        description="Canonical enum name; UNKNOWN_ROLE_<id> when unmapped."
    )
    is_member: bool
    execution_delay: int = Field(
        description="Execution timelock in seconds (0 = immediate)."
    )


class RoleAccountsResponse(_Base):
    """Role holders on a Plasma Vault's AccessManager."""

    vault: str
    access_manager: str
    chain_id: int
    role_filter: str | None = Field(
        default=None,
        description="Canonical role name when filtered; null = all roles.",
    )
    accounts: list[RoleAccountEntry]

    @classmethod
    def from_role_accounts(
        cls,
        accounts: list[RoleAccount],
        *,
        vault: str,
        access_manager: str,
        chain_id: int,
        role_filter: str | None,
    ) -> RoleAccountsResponse:
        """Build the response from SDK dataclasses (sorted for stable output)."""
        # Deferred import keeps this module's import graph pydantic-only.
        from ipor_fusion.core.access import role_account_sort_key

        entries = [
            RoleAccountEntry(
                account=ra.account,
                role_id=ra.role_id,
                role_name=ra.role_name,
                is_member=ra.is_member,
                execution_delay=ra.execution_delay,
            )
            for ra in sorted(accounts, key=role_account_sort_key)
        ]
        return cls(
            vault=vault,
            access_manager=access_manager,
            chain_id=chain_id,
            role_filter=role_filter,
            accounts=entries,
        )


# ---------------------------------------------------------------------------
# Oracle-mapping models
# ---------------------------------------------------------------------------


class OraclePriceModel(_Base):
    """Price block; non-null only when the getAssetPrice() read succeeded."""

    raw: str = Field(description="Raw getAssetPrice() answer.")
    decimals: int = Field(description="Decimals of the raw answer.")
    normalized_wad: str = Field(
        description="Price rescaled to 18 decimals (integer string)."
    )


class OracleNodeModel(_Base):
    """One asset's resolved price feed (recursive for derived feeds)."""

    asset: str
    symbol: str | None = Field(description="ERC-20 symbol; null when unreadable.")
    decimals: int | None
    source: str | None = Field(
        description="Configured price-feed address; null when none/unreadable."
    )
    source_type: str = Field(
        description="DualCrossReferencePriceFeed, ChainlinkAggregator, "
        "chainlink_style, ERC4626PriceFeed, "
        "CollateralTokenOnMorphoMarketPriceFeed, middleware_fallback, or "
        "custom_unknown. ChainlinkAggregator is claimed only when the full "
        "AggregatorV3Interface answers with sane metadata; chainlink_style "
        "merely answers latestRoundData — verify the address yourself if "
        "identity matters."
    )
    price: OraclePriceModel | None = Field(
        description="Authoritative getAssetPrice() read; null when the read failed."
    )
    path: list[str] = Field(
        description="Human-readable resolution chain, e.g. "
        "['wstETH', 'convertToAssets(1 share)', 'stETH', 'Chainlink feed']."
    )
    status: NodeStatus = Field(
        description="resolved: own feed explained and every dependency "
        "resolved. partially_resolved: own feed explained, but some "
        "descendant is not resolved. partial: this node's own resolution is "
        "incomplete — see reason."
    )
    reason: str | None = Field(
        default=None, description="Why the node is partial; null otherwise."
    )
    source_detail: dict[str, Any] | None = Field(
        default=None,
        description="Type-specific raw reads; null when no probe ran. "
        "Aggregator-compatible reads (Chainlink-tier leaves, dual-xref "
        "component feeds) carry description, round_id, answer, decimals, "
        "started_at, started_at_utc, updated_at, updated_at_utc, "
        "answered_in_round — raw values, no staleness judgment; description "
        "is null when the feed does not implement it, and a *_utc twin is "
        "null for a synthetic epoch-0 timestamp (the raw int keeps "
        "fidelity). Chainlink-tier leaves add aggregator and phase_id "
        "(proxy-deployment evidence; null when unanswered).",
    )
    dependencies: list[OracleNodeModel] = Field(
        default_factory=list,
        description="Nodes this feed derives its price from (empty for leaves).",
    )

    @classmethod
    def from_node(cls, node: OracleNode) -> OracleNodeModel:
        return cls(
            asset=node.asset,
            symbol=node.symbol,
            decimals=node.decimals,
            source=node.source,
            source_type=node.source_type,
            price=OraclePriceModel(
                raw=node.price.raw,
                decimals=node.price.decimals,
                normalized_wad=node.price.normalized_wad,
            )
            if node.price is not None
            else None,
            path=node.path,
            status=node.status,
            reason=node.reason,
            source_detail=node.source_detail,
            dependencies=[cls.from_node(dep) for dep in node.dependencies],
        )


class OracleMappingResponse(_Base):
    """How a Plasma Vault prices every configured asset at a pinned block."""

    vault: str
    vault_name: str | None
    asset: dict[str, Any] = Field(
        description="Underlying asset: {address, symbol, decimals}."
    )
    price_oracle: str
    block_number: int
    asset_source: AssetSource = Field(
        description="How assets were enumerated: 'getConfiguredAssets' or "
        "'events' (AssetPriceSourceUpdated log replay)."
    )
    status: MappingStatus = Field(
        description="Roll-up of the configured-asset roots: resolved when "
        "every root resolved (vacuously for zero assets), unresolved when "
        "every root is partial (total failure), partially_resolved "
        "otherwise."
    )
    configured_assets: list[OracleNodeModel]
    unresolved: list[OracleNodeModel] = Field(
        description="Flat mirror of every partial node (own resolution "
        "incomplete). partially_resolved parents are not repeated here — "
        "they self-describe."
    )

    @classmethod
    def from_mapping(cls, mapping: OracleMapping) -> OracleMappingResponse:
        """Build the response from the SDK dataclass."""
        return cls(
            vault=mapping.vault,
            vault_name=mapping.vault_name,
            asset=mapping.asset,
            price_oracle=mapping.price_oracle,
            block_number=mapping.block_number,
            asset_source=mapping.asset_source,
            status=mapping.status,
            configured_assets=[
                OracleNodeModel.from_node(node) for node in mapping.configured_assets
            ],
            unresolved=[OracleNodeModel.from_node(node) for node in mapping.unresolved],
        )


# ---------------------------------------------------------------------------
# Market tool models
# ---------------------------------------------------------------------------


class MorphoMarketParamsModel(_Base):
    loan_token: str
    collateral_token: str
    oracle: str
    irm: str
    lltv: str = Field(description="LLTV in wad (1e18 = 100%) as a string.")


class MorphoMarketStateModel(_Base):
    total_supply_assets: str
    total_supply_shares: str
    total_borrow_assets: str
    total_borrow_shares: str
    liquidity_assets: str
    fee_wad: str
    last_update: int


class MorphoMarketRatesModel(_Base):
    rate_per_second_wad: str
    utilization: float
    borrow_apy: float
    supply_apy: float


class MorphoLoanAssetModel(_Base):
    address: str
    symbol: str
    decimals: int


class MorphoVaultPublicAllocatorConfig(_Base):
    fee_wei: str
    max_in: str
    max_out: str
    admin: str | None = None


class MorphoSupplyingVault(_Base):
    address: str
    name: str
    symbol: str
    asset: dict[str, Any]
    total_assets: str
    supply_assets: str
    supply_cap: str
    allocators: list[str]
    public_allocator_config: MorphoVaultPublicAllocatorConfig | None = None


class MorphoBlueMarketResponse(_Base):
    """Morpho Blue market parameters, state, APYs, and supplying vaults."""

    market_id: str
    chain_id: int
    public_allocator: str | None = None
    market_params: MorphoMarketParamsModel
    state: MorphoMarketStateModel
    rates: MorphoMarketRatesModel
    api_error: str | None = None
    loan_asset: MorphoLoanAssetModel | None = None
    collateral_asset: MorphoLoanAssetModel | None = None
    vaults: list[MorphoSupplyingVault] | None = None


class MetaMorphoVaultResponse(_Base):
    """Discriminated by `version` ('v1' or 'v2'). Shape varies between versions
    — kept loosely typed to avoid duplicating the Morpho API surface."""

    version: str
    chain_id: int
    address: str
    name: str
    symbol: str
    asset: dict[str, Any]
    total_assets: str
    # v2-only fields
    idle_assets: str | None = None
    liquidity: str | None = None
    share_price: float | None = None
    max_apy: float | None = None
    performance_fee: float | None = None
    performance_fee_recipient: str | None = None
    management_fee: float | None = None
    management_fee_recipient: str | None = None
    sentinels: list[str] | None = None
    liquidity_adapter: dict[str, Any] | None = None
    adapters: list[dict[str, Any]] | None = None
    caps: list[dict[str, Any]] | None = None
    # v1-only fields
    fee_wad: str | None = None
    guardian: str | None = None
    fee_recipient: str | None = None
    public_allocator: dict[str, Any] | None = None
    allocations: list[dict[str, Any]] | None = None
    # shared
    owner: str
    curator: str
    allocators: list[str]


class VaultInfoResponse(_Base):
    """Full on-chain state of a Plasma Vault.

    Top-level fields are typed; deeply nested protocol-specific blocks
    (substrates, position breakdowns inside balance_fuses, dependency_graph,
    deployment, withdraw_manager_details, share_price) keep dict[str, Any]
    typing because their shape is conditional on which protocols and managers
    a given vault uses.
    """

    vault: str
    name: str | None = None
    links: dict[str, str] = Field(
        description="External URLs (ipor_app, etherscan if known)."
    )
    chain: str
    chain_id: int
    block: int
    is_latest: bool = Field(
        default=False,
        description="True when the query targeted the latest block (block is the "
        "resolved height at read time); False for a pinned historical block.",
    )
    block_timestamp: int
    block_timestamp_utc: str
    deployment: dict[str, Any] | None = None
    asset: AssetInfo
    share_decimals: int
    total_assets: Amount
    total_supply: Amount
    share_price: dict[str, Any] | None = None
    supply_cap: Amount
    managers: Managers
    role_accounts: list[RoleAccountEntry] | None = Field(
        default=None,
        description="All confirmed role holders on the AccessManager; "
        "null when the RoleGranted log scan failed (provider without "
        "broad eth_getLogs support).",
    )
    withdraw_manager_details: dict[str, Any] | None = None
    fuses: list[FuseEntry]
    balance_fuses: list[BalanceFuseEntry]
    zero_balance_fuses: list[BalanceFuseEntry] = Field(
        default_factory=list,
        description="Markets backed by a ZeroBalanceFuse (structurally-zero "
        "balance): swap/flash-loan/admin capabilities, not liquidity venues. "
        "Kept separate from balance_fuses so they are not counted as markets.",
    )
    instant_withdrawal_fuses: list[FuseEntry]
    substrates: dict[str, list[dict[str, Any]]] = Field(
        description="Per-market substrate entries; outer keys are human-readable market labels."
    )
    dependency_graph: dict[str, Any] | None = None
    erc20_balances: list[ERC20Entry]
    reconciliation: Reconciliation
    lending_health: LendingHealth | None = None
    health_check: HealthCheck

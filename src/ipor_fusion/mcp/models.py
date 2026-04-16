"""Pydantic response models for MCP tools.

Tool functions in mcp/server.py return instances of these models. FastMCP
serializes them via model_dump() and exposes their JSON schema to the LLM.

Design notes:
- Top-level shapes are strictly typed for LLM schema clarity.
- Truly dynamic sections (substrates keyed by market label, per-protocol
  position breakdowns) use dict[str, Any] / list[dict[str, Any]] — modelling
  every variant would be brittle without meaningful LLM-side benefit.
- Models use extra="forbid": any new field added to _build_json_output that
  is not declared here will fail VaultInfoResponse.model_validate(). This is
  intentional — it forces CLI dict-builder and MCP model to stay in sync,
  catching silent drift and typos at test time. See test_mcp_models.py for
  the contract test that exercises every top-level field.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field


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
    ok: bool
    warnings: list[str]


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
    block: int | str
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
    withdraw_manager_details: dict[str, Any] | None = None
    fuses: list[FuseEntry]
    balance_fuses: list[BalanceFuseEntry]
    instant_withdrawal_fuses: list[FuseEntry]
    substrates: dict[str, list[dict[str, Any]]] = Field(
        description="Per-market substrate entries; outer keys are human-readable market labels."
    )
    dependency_graph: dict[str, Any] | None = None
    erc20_balances: list[ERC20Entry]
    reconciliation: Reconciliation
    lending_health: LendingHealth | None = None
    health_check: HealthCheck

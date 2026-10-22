"""The ``fusion vault info --json`` document: `build_vault_info_json` renders a
fetched `VaultData` (with its validation) as one `VaultInfoJson`."""

# No ``from __future__ import annotations``: a TypedDict records its
# ``NotRequired`` keys in ``__optional_keys__`` only from evaluated annotations.

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from typing import NotRequired, TypeAlias, TypedDict

from eth_typing import ChecksumAddress, HexAddress
from web3.types import Timestamp

from ipor_fusion.chains import CHAIN_NAMES
from ipor_fusion.core.access import RoleAccountJson
from ipor_fusion.core.fee_manager import HighWaterMarkPerformanceFee, RecipientFee
from ipor_fusion.core.plasma_vault import BalanceFuse
from ipor_fusion.dependency_graph import compute_update_groups, compute_update_reach
from ipor_fusion.field_docs import DOCS
from ipor_fusion.readers.aave_v3 import AaveV3PositionBreakdown
from ipor_fusion.readers.hypercore import HyperCorePendingState, HyperCorePerpLeg
from ipor_fusion.readers.lending_health import VaultLendingHealth
from ipor_fusion.readers.morpho import MorphoPositionBreakdown
from ipor_fusion.substrates import (
    SubstrateInfo,
    decode_substrate,
    format_market_label,
    market_name,
)
from ipor_fusion.types import (
    AccessManagerAddress,
    ChainId,
    Decimals,
    Fee,
    FuseAddress,
    HyperCoreActionId,
    HyperCoreAsset,
    HyperCoreTokenIndex,
    MarketId,
    MorphoBlueMarketId,
    Period,
    PlasmaVaultAddress,
    parse_hex_address,
)
from ipor_fusion.validation import (
    Reconciliation,
    VaultValidation,
    _format_amount,
    validate_vault_state,
)
from ipor_fusion.vault_data import (
    VaultData,
)

BLOCK_EXPLORER_URLS: dict[ChainId, str] = {
    ChainId(1): "https://etherscan.io",
    ChainId(42161): "https://arbiscan.io",
    ChainId(8453): "https://basescan.org",
    ChainId(10): "https://optimistic.etherscan.io",
    ChainId(137): "https://polygonscan.com",
    ChainId(56): "https://bscscan.com",
    ChainId(43114): "https://snowtrace.io",
    ChainId(250): "https://ftmscan.com",
}

IPOR_APP_URL = "https://app.ipor.io/fusion"

# Per-block shorthands into the shared documentation table.
_FEE_DOCS = DOCS["fees"]
_HYPERCORE_DOCS = DOCS["hypercore"]
_WM_DOCS = DOCS["withdraw_manager_details"]

UINT256_MAX = 2**256 - 1

# Balance fuses whose ``balanceOf()`` is structurally zero (``pure`` → 0). Their
# market is a registered *capability* (swap / flash-loan / instant-withdrawal /
# admin plumbing), not a *venue* where assets actually sit. Anything backed by a
# real ``*BalanceFuse`` is a venue.
_CAPABILITY_BALANCE_FUSES: frozenset[str] = frozenset({"ZeroBalanceFuse"})


class AmountJson(TypedDict):
    """A raw on-chain integer and its decimal-shifted string."""

    raw: int
    formatted: str


class UsdAmountJson(AmountJson):
    """An amount with its USD value, null when the asset has no oracle price."""

    usd: float | None


class PricedAmountJson(AmountJson):
    """An amount whose ``usd`` key is present only when the asset is priced."""

    usd: NotRequired[float]


class DeltaJson(UsdAmountJson):
    """The reconciliation delta; ``formatted`` is its absolute value."""

    percent: float


class VaultLinksJson(TypedDict):
    ipor_app: str
    etherscan: NotRequired[str]


class DeploymentJson(TypedDict):
    block: int
    timestamp: Timestamp
    timestamp_utc: str
    age_days: int


class DeploymentErrorJson(TypedDict):
    """Why the deployment block could not be found."""

    error: str


class VaultAssetJson(TypedDict):
    address: ChecksumAddress
    symbol: str
    decimals: Decimals
    price_usd: float | None


class SharePriceJson(TypedDict):
    """Assets per whole share, and its USD value when the asset is priced."""

    asset: float
    usd: NotRequired[float]


class ManagersJson(TypedDict):
    access: AccessManagerAddress
    price_oracle: ChecksumAddress
    rewards: ChecksumAddress | None
    withdraw: ChecksumAddress | None
    fee: ChecksumAddress | None


class RecipientFeeJson(TypedDict):
    recipient: ChecksumAddress
    fee_percent: float
    fee_bps: Fee


class HighWaterMarkJson(TypedDict):
    high_water_mark: int
    last_update_timestamp: Timestamp
    last_update_utc: str | None
    update_interval_seconds: Period


class FeesJson(TypedDict):
    """Every fee the vault can charge; null means "not known", never "free".
    Each ``*_note`` carries its sibling's `ipor_fusion.field_docs.DOCS` text."""

    fee_manager: ChecksumAddress | None
    fee_manager_note: str
    ipor_dao_fee_recipient: ChecksumAddress | None
    ipor_dao_fee_recipient_note: str
    deposit_fee_percent: float | None
    deposit_fee_wad: Fee | None
    deposit_fee_percent_note: str
    request_fee_percent: float | None
    request_fee_wad: Fee | None
    request_fee_percent_note: str
    withdraw_fee_percent: float | None
    withdraw_fee_wad: Fee | None
    withdraw_fee_percent_note: str
    performance_fee_percent: float | None
    performance_fee_bps: Fee | None
    performance_fee_percent_note: str
    performance_fee_manager_percent: float | None
    performance_fee_manager_percent_note: str
    performance_fee_recipients: list[RecipientFeeJson] | None
    performance_fee_recipients_note: str
    high_water_mark: HighWaterMarkJson | None
    high_water_mark_note: str
    management_fee_percent: float | None
    management_fee_bps: Fee | None
    management_fee_percent_note: str
    management_fee_manager_percent: float | None
    management_fee_manager_percent_note: str
    management_fee_recipients: list[RecipientFeeJson] | None
    management_fee_recipients_note: str
    management_fee_last_update_timestamp: Timestamp | None
    management_fee_last_update_utc: str | None
    management_fee_last_update_timestamp_note: str
    unrealized_management_fee: PricedAmountJson | None
    unrealized_management_fee_note: str


class PendingRequestJson(TypedDict):
    """One scheduled withdrawal; ``assets`` is absent when its conversion
    read failed."""

    account: ChecksumAddress
    shares: AmountJson
    end_withdraw_window_timestamp: Timestamp
    end_withdraw_window_utc: str
    remaining_seconds: int
    can_withdraw: bool
    assets: NotRequired[PricedAmountJson]


class WithdrawManagerJson(TypedDict):
    """Scheduled-withdrawal state; the exit fees are in `FeesJson`."""

    withdraw_window_seconds: Period
    withdraw_window_seconds_note: str
    fees_note: str
    shares_to_release: AmountJson
    shares_to_release_note: str
    last_release_funds_timestamp: Timestamp
    last_release_funds_utc: str | None
    last_release_funds_timestamp_note: str
    pending_requests: list[PendingRequestJson] | None
    total_pending_shares: AmountJson | None
    total_pending_shares_note: str


class FuseJson(TypedDict):
    """A registered fuse; the market keys are absent when it serves none."""

    address: FuseAddress
    contract: str
    market_id: NotRequired[MarketId]
    market: NotRequired[str]


class BreakdownAmountJson(TypedDict):
    """One position amount; symbol, decimals and ``formatted`` need the
    token's metadata, ``usd`` also its price."""

    raw: int
    token: HexAddress
    symbol: NotRequired[str]
    decimals: NotRequired[Decimals]
    formatted: NotRequired[str]
    usd: NotRequired[float]


class MorphoBreakdownJson(TypedDict):
    morpho_market_id: MorphoBlueMarketId
    collateral_symbol: str | None
    loan_symbol: str | None
    collateral: BreakdownAmountJson
    borrow: BreakdownAmountJson
    supply: BreakdownAmountJson


class AaveBreakdownJson(TypedDict):
    asset: ChecksumAddress
    asset_symbol: str | None
    supply: BreakdownAmountJson
    variable_debt: BreakdownAmountJson
    stable_debt: BreakdownAmountJson


class BalanceFuseJson(TypedDict):
    market: str
    market_id: MarketId
    balance: AmountJson
    fuse: FuseAddress
    contract: str
    pct_of_total: NotRequired[float]
    depends_on: NotRequired[list[str]]
    position_breakdown: NotRequired[list[MorphoBreakdownJson] | list[AaveBreakdownJson]]


# Open-ended: each substrate decoder flattens its own fields (selector,
# chain_id, slippage, ...) next to address or raw, symbol, contract and error.
SubstrateEntryJson: TypeAlias = dict[str, str | bool]


class DependencyGraphJson(TypedDict):
    """The balance dependency graph by market label."""

    edges: dict[str, list[str]]
    update_reach: dict[str, list[str]]
    update_groups: list[list[str]]


class Erc20BalanceJson(TypedDict):
    """An ERC20_VAULT_BALANCE token; the balance keys are absent when the
    token could not be read."""

    address: HexAddress
    symbol: str
    decimals: NotRequired[Decimals]
    balance: NotRequired[AmountJson]
    price_usd: NotRequired[float | None]
    usd_value: NotRequired[float | None]
    note: str


class ReconciliationJson(TypedDict):
    balance_fuses_total: UsdAmountJson
    underlying_on_vault: UsdAmountJson
    rewards_manager_balance: UsdAmountJson
    erc20_direct_total: UsdAmountJson
    sum: UsdAmountJson
    on_chain_total_assets: UsdAmountJson
    delta: DeltaJson
    pending_withdrawals: UsdAmountJson
    implied_market_total: AmountJson
    market_storage_divergence: int


class LendingMarketJson(TypedDict):
    protocol: str
    market_id: MarketId
    market_name: str
    substrate_id: str | None
    current_ltv: float | None
    max_ltv: float
    health_factor: float | None
    total_collateral_usd: float | None
    total_debt_usd: float | None
    ltv_usage_percent: float | None
    is_warning: bool
    is_critical: bool


class UnreadableLendingMarketJson(TypedDict):
    """A lending position whose health could not be read."""

    protocol: str
    market_id: MarketId
    market_name: str
    substrate_id: str | None
    reason: str


class LendingHealthJson(TypedDict):
    markets: list[LendingMarketJson]
    unreadable: list[UnreadableLendingMarketJson]
    worst_ltv_usage_percent: float | None


class HyperCoreSpotLegJson(TypedDict):
    token_index: HyperCoreTokenIndex
    evm_asset: ChecksumAddress
    total_wei: int
    hold_wei: int
    wei_decimals: Decimals | None
    price_usd: float | None
    value_usd: float


class HyperCorePerpLegJson(TypedDict):
    dex: int
    account_value_usd6: int
    value_usd: float


class HyperCorePerpMarketJson(TypedDict):
    asset: HyperCoreAsset
    dex: int
    read_index: int
    coin: str | None
    max_notional_usd6: int
    reduce_only_required: bool


class HyperCorePendingJson(TypedDict):
    """`HyperCorePendingState`, its enums by member name."""

    pending: bool
    settled: bool
    refreshing: bool
    action_class: str
    settlement_mode: str | None
    reported_result: str
    action_id: HyperCoreActionId
    action_nonce: int
    pending_until: Timestamp
    pending_until_utc: str
    enqueued_l1_block: int
    enqueued_evm_block: int
    cached_value_wad: int
    current_l1_block: int
    current_timestamp: Timestamp
    l1_block_available: bool


class HyperCoreJson(TypedDict):
    """HyperCore (market 55) live state. Each ``*_note`` carries its
    sibling's `ipor_fusion.field_docs.DOCS` text."""

    market_id: MarketId
    market: str
    balance_fuse: FuseAddress
    core_user_exists: bool
    perp_dex_bitmap: int
    nav_wad: int
    nav_usd: float
    balance_fuse_value_wad: int | None
    nav_matches_balance_fuse: bool | None
    spot: list[HyperCoreSpotLegJson]
    native_perp: HyperCorePerpLegJson | None
    hip3: list[HyperCorePerpLegJson]
    perp_markets: list[HyperCorePerpMarketJson]
    pending: HyperCorePendingJson | None
    core_user_exists_note: str
    perp_dex_bitmap_note: str
    nav_wad_note: str
    balance_fuse_value_wad_note: str
    nav_matches_balance_fuse_note: str
    spot_note: str
    native_perp_note: str
    hip3_note: str
    perp_markets_note: str
    pending_note: str


class HealthCheckJson(TypedDict):
    """`health_check_lists` of the snapshot's validation."""

    ok: list[str]
    warnings: list[str]
    criticals: list[str]


class VaultInfoJson(TypedDict):
    """The ``fusion vault info --json`` document (`build_vault_info_json`)."""

    vault: PlasmaVaultAddress
    name: str | None
    links: VaultLinksJson
    chain: str
    chain_id: ChainId
    block: int
    is_latest: bool
    block_timestamp: Timestamp
    block_timestamp_utc: str
    deployment: DeploymentJson | DeploymentErrorJson | None
    asset: VaultAssetJson
    share_decimals: Decimals
    total_assets: UsdAmountJson
    total_supply: AmountJson
    share_price: SharePriceJson | None
    supply_cap: AmountJson
    managers: ManagersJson
    role_accounts: list[RoleAccountJson] | None
    fees: FeesJson | None
    withdraw_manager_details: WithdrawManagerJson | None
    fuses: list[FuseJson]
    balance_fuses: list[BalanceFuseJson]
    zero_balance_fuses: list[BalanceFuseJson]
    instant_withdrawal_fuses: list[FuseJson]
    substrates: dict[str, list[SubstrateEntryJson]]
    dependency_graph: DependencyGraphJson | None
    erc20_balances: list[Erc20BalanceJson]
    reconciliation: ReconciliationJson
    lending_health: LendingHealthJson | None
    hypercore: HyperCoreJson | None
    health_check: HealthCheckJson


class _MarketBlocksJson(TypedDict):
    fuses: list[FuseJson]
    balance_fuses: list[BalanceFuseJson]
    zero_balance_fuses: list[BalanceFuseJson]
    instant_withdrawal_fuses: list[FuseJson]
    substrates: dict[str, list[SubstrateEntryJson]]


def _unix_to_iso(timestamp: int) -> str:
    """Unix seconds as the ISO-8601 UTC string every `*_utc` key carries.

    Renders 0 as the epoch. Callers for which 0 is the contract's "never"
    sentinel must guard and emit null themselves.
    """
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _partition_balance_fuses(
    entries: list[BalanceFuseJson],
) -> tuple[list[BalanceFuseJson], list[BalanceFuseJson]]:
    """Split balance-fuse entries into ``(venues, zero_balance)``.

    Venues are real ``*BalanceFuse`` markets where assets can sit. Zero-balance
    fuses are ZeroBalanceFuse-backed markets (swap/flash-loan/admin plumbing)
    with a structurally-zero balance — capabilities, not liquidity markets.
    Splitting keeps both visible while letting a consumer count only venues as
    markets.
    """
    venues = [e for e in entries if e["contract"] not in _CAPABILITY_BALANCE_FUSES]
    zero_balance = [e for e in entries if e["contract"] in _CAPABILITY_BALANCE_FUSES]
    return venues, zero_balance


def _role_accounts_json(data: VaultData) -> list[RoleAccountJson] | None:
    if data.role_accounts is None:
        return None
    return [ra.to_dict() for ra in data.role_accounts]


def _build_share_price_json(data: VaultData) -> SharePriceJson | None:
    if data.total_supply == 0:
        return None
    price = (data.total_assets / 10**data.asset_decimals) / (
        data.total_supply / 10**data.share_decimals
    )
    result: SharePriceJson = {
        "asset": round(price, 10),
    }
    if data.asset_price_usd is not None:
        result["usd"] = round(price * data.asset_price_usd, 10)
    return result


def _build_deployment_json(
    data: VaultData,
) -> DeploymentJson | DeploymentErrorJson | None:
    if data.deployment_block is None or data.deployment_timestamp is None:
        if data.deployment_error:
            return {"error": data.deployment_error}
        return None
    return {
        "block": data.deployment_block,
        "timestamp": data.deployment_timestamp,
        "timestamp_utc": _unix_to_iso(data.deployment_timestamp),
        "age_days": (
            datetime.now(tz=UTC)
            - datetime.fromtimestamp(data.deployment_timestamp, tz=UTC)
        ).days,
    }


def _wad_to_percent(value: int | None) -> float | None:
    """WAD (1e18 = 100%) to a percent float. Pairs with `*_wad`.

    Deliberately unrounded: both divisors are powers of ten, so every value
    in the plausible fee range already reprs exactly, and rounding would only
    flatten a tiny-but-real fee to 0.0 — the one case worth not lying about.
    """
    return None if value is None else value / 1e16


def _bps_to_percent(value: int | None) -> float | None:
    """Basis points (10000 = 100%) to a percent float. Pairs with `*_bps`."""
    return None if value is None else value / 100


def _build_recipient_fees_json(
    recipients: list[RecipientFee] | None,
) -> list[RecipientFeeJson] | None:
    if recipients is None:
        return None
    return [
        {
            "recipient": rf.recipient,
            "fee_percent": rf.fee_value / 100,
            "fee_bps": rf.fee_value,
        }
        for rf in recipients
    ]


def _build_high_water_mark_json(
    hwm: HighWaterMarkPerformanceFee | None,
) -> HighWaterMarkJson | None:
    if hwm is None:
        return None
    return {
        "high_water_mark": hwm.high_water_mark,
        "last_update_timestamp": hwm.last_update,
        "last_update_utc": (
            _unix_to_iso(hwm.last_update) if hwm.last_update > 0 else None
        ),
        "update_interval_seconds": hwm.update_interval,
    }


def _priced_amount_json(
    raw: int, decimals: int, price_usd: float | None
) -> PricedAmountJson:
    amount: PricedAmountJson = {"raw": raw, "formatted": _format_amount(raw, decimals)}
    if price_usd is not None:
        amount["usd"] = round((raw / 10**decimals) * price_usd, 2)
    return amount


def _build_fees_json(data: VaultData) -> FeesJson | None:
    """Every fee the vault can charge, entry and exit sides included.

    Exit fees live here rather than in `withdraw_manager_details` so that a
    single object answers "what does this vault cost?". Keys are always
    present, and `null` uniformly means "not known": either the vault has no
    withdraw manager, or the getter is unsupported on this deployment (the
    deposit fee and high-water mark postdate some FeeManagers). A fee that is
    genuinely not charged reads 0, never null.
    """
    if (fees := data.fee_data) is None:
        return None
    wmd = data.withdraw_manager_data
    request_fee = wmd.request_fee if wmd else None
    withdraw_fee = wmd.withdraw_fee if wmd else None

    unrealized = (
        None
        if fees.unrealized_management_fee is None
        else _priced_amount_json(
            fees.unrealized_management_fee, data.asset_decimals, data.asset_price_usd
        )
    )

    return {
        "fee_manager": fees.fee_manager,
        "fee_manager_note": _FEE_DOCS["fee_manager"],
        "ipor_dao_fee_recipient": fees.ipor_dao_fee_recipient,
        "ipor_dao_fee_recipient_note": _FEE_DOCS["ipor_dao_fee_recipient"],
        "deposit_fee_percent": _wad_to_percent(fees.deposit_fee_wad),
        "deposit_fee_wad": fees.deposit_fee_wad,
        "deposit_fee_percent_note": _FEE_DOCS["deposit_fee_percent"],
        "request_fee_percent": _wad_to_percent(request_fee),
        "request_fee_wad": request_fee,
        "request_fee_percent_note": _FEE_DOCS["request_fee_percent"],
        "withdraw_fee_percent": _wad_to_percent(withdraw_fee),
        "withdraw_fee_wad": withdraw_fee,
        "withdraw_fee_percent_note": _FEE_DOCS["withdraw_fee_percent"],
        "performance_fee_percent": _bps_to_percent(fees.performance_fee_vault_bps),
        "performance_fee_bps": fees.performance_fee_vault_bps,
        "performance_fee_percent_note": _FEE_DOCS["performance_fee_percent"],
        "performance_fee_manager_percent": _bps_to_percent(
            fees.performance_fee_manager_bps
        ),
        "performance_fee_manager_percent_note": _FEE_DOCS[
            "performance_fee_manager_percent"
        ],
        "performance_fee_recipients": _build_recipient_fees_json(
            fees.performance_fee_recipients
        ),
        "performance_fee_recipients_note": _FEE_DOCS["performance_fee_recipients"],
        "high_water_mark": _build_high_water_mark_json(fees.high_water_mark),
        "high_water_mark_note": _FEE_DOCS["high_water_mark"],
        "management_fee_percent": _bps_to_percent(fees.management_fee_vault_bps),
        "management_fee_bps": fees.management_fee_vault_bps,
        "management_fee_percent_note": _FEE_DOCS["management_fee_percent"],
        "management_fee_manager_percent": _bps_to_percent(
            fees.management_fee_manager_bps
        ),
        "management_fee_manager_percent_note": _FEE_DOCS[
            "management_fee_manager_percent"
        ],
        "management_fee_recipients": _build_recipient_fees_json(
            fees.management_fee_recipients
        ),
        "management_fee_recipients_note": _FEE_DOCS["management_fee_recipients"],
        "management_fee_last_update_timestamp": fees.management_fee_last_update,
        # Falsy covers both 0 (never accrued) and None (read failed); the
        # timestamp itself keeps the two apart.
        "management_fee_last_update_utc": (
            _unix_to_iso(fees.management_fee_last_update)
            if fees.management_fee_last_update
            else None
        ),
        "management_fee_last_update_timestamp_note": _FEE_DOCS[
            "management_fee_last_update_timestamp"
        ],
        "unrealized_management_fee": unrealized,
        "unrealized_management_fee_note": _FEE_DOCS["unrealized_management_fee"],
    }


def _build_withdraw_manager_json(data: VaultData) -> WithdrawManagerJson | None:
    if data.withdraw_manager_data is None:
        return None
    wmd = data.withdraw_manager_data
    sdec = data.share_decimals
    adec = data.asset_decimals

    pending = wmd.pending_requests
    total_pending_shares = sum((r.shares for r in pending or []), 0)

    requests_json: list[PendingRequestJson] = []
    for req in pending or []:
        assets = wmd.pending_request_assets.get(req.account)
        entry: PendingRequestJson = {
            "account": req.account,
            "shares": {
                "raw": req.shares,
                "formatted": _format_amount(req.shares, sdec),
            },
            "end_withdraw_window_timestamp": req.end_withdraw_window_timestamp,
            "end_withdraw_window_utc": _unix_to_iso(req.end_withdraw_window_timestamp),
            "remaining_seconds": max(
                0, req.end_withdraw_window_timestamp - data.block_timestamp
            ),
            "can_withdraw": req.can_withdraw,
        }
        if assets is not None:
            entry["assets"] = _priced_amount_json(assets, adec, data.asset_price_usd)
        requests_json.append(entry)

    return {
        "withdraw_window_seconds": wmd.withdraw_window,
        "withdraw_window_seconds_note": _WM_DOCS["withdraw_window_seconds"],
        "fees_note": _WM_DOCS["fees"],
        "shares_to_release": {
            "raw": wmd.shares_to_release,
            "formatted": _format_amount(wmd.shares_to_release, sdec),
        },
        "shares_to_release_note": _WM_DOCS["shares_to_release"],
        "last_release_funds_timestamp": wmd.last_release_funds_timestamp,
        "last_release_funds_utc": (
            _unix_to_iso(wmd.last_release_funds_timestamp)
            if wmd.last_release_funds_timestamp > 0
            else None
        ),
        "last_release_funds_timestamp_note": _WM_DOCS["last_release_funds_timestamp"],
        "pending_requests": None if pending is None else requests_json,
        "total_pending_shares": None
        if pending is None
        else {
            "raw": total_pending_shares,
            "formatted": _format_amount(total_pending_shares, sdec),
        },
        "total_pending_shares_note": _WM_DOCS["total_pending_shares"],
    }


def _build_breakdown_amount_json(
    data: VaultData, raw: int, token_address: HexAddress
) -> BreakdownAmountJson:
    """Build the JSON entry for one breakdown amount (collateral/borrow/supply/debt).

    Always includes raw + token. When ERC-20 metadata is resolvable, adds
    symbol, decimals, and `formatted` (decimal-shifted human string). When the
    oracle has a USD price for the token, adds `usd` (rounded to 2 decimals).
    """
    entry: BreakdownAmountJson = {"raw": raw, "token": token_address}
    key = parse_hex_address(token_address)
    if symbol := data.token_symbols.get(key):
        entry["symbol"] = symbol
    decimals = data.token_decimals.get(key)
    if decimals is not None:
        entry["decimals"] = decimals
        entry["formatted"] = _format_amount(raw, decimals)
        prices_usd = data.token_prices_usd
        if prices_usd and (price := prices_usd.get(key)) is not None:
            entry["usd"] = round((raw / 10**decimals) * price, 2)
    return entry


def _build_dependency_graph_json(data: VaultData) -> DependencyGraphJson | None:
    if not data.dependency_graph:
        return None

    edges = {
        format_market_label(mid): [format_market_label(d) for d in deps]
        for mid, deps in data.dependency_graph.items()
    }

    reach = compute_update_reach(data.dependency_graph)
    reach_json = {
        format_market_label(mid): sorted(format_market_label(r) for r in reachable)
        for mid, reachable in reach.items()
    }

    groups = compute_update_groups(data.dependency_graph)
    groups_json = [sorted(format_market_label(m) for m in group) for group in groups]

    return {
        "edges": edges,
        "update_reach": reach_json,
        "update_groups": groups_json,
    }


def _lending_substrate_ref(substrate_id: str | None) -> str | None:
    """0x-prefixed Morpho market id or Aave V4 Spoke; None for Aave V3 rows."""
    if substrate_id is None:
        return None
    return substrate_id if substrate_id.startswith("0x") else f"0x{substrate_id}"


def _erc20_balances_json(validation: VaultValidation) -> list[Erc20BalanceJson]:
    entries: list[Erc20BalanceJson] = []
    for token in validation.erc20.tokens:
        if token.decimals is None or token.balance is None:
            entries.append(
                {"address": token.address, "symbol": token.symbol, "note": token.note}
            )
            continue
        entries.append(
            {
                "address": token.address,
                "symbol": token.symbol,
                "decimals": token.decimals,
                "balance": {
                    "raw": token.balance,
                    "formatted": _format_amount(token.balance, token.decimals),
                },
                "price_usd": token.price_usd,
                "usd_value": token.usd_value,
                "note": token.note,
            }
        )
    return entries


def _amount_json(raw: int, decimals: int, usd: float | None) -> UsdAmountJson:
    return {"raw": raw, "formatted": _format_amount(raw, decimals), "usd": usd}


def _reconciliation_json(recon: Reconciliation, decimals: int) -> ReconciliationJson:
    return {
        "balance_fuses_total": _amount_json(
            recon.balance_fuses_total, decimals, recon.balance_fuses_total_usd
        ),
        "underlying_on_vault": _amount_json(
            recon.underlying_on_vault, decimals, recon.underlying_on_vault_usd
        ),
        "rewards_manager_balance": _amount_json(
            recon.rewards_manager_balance,
            decimals,
            recon.rewards_manager_balance_usd,
        ),
        "erc20_direct_total": _amount_json(
            recon.erc20_direct_total, decimals, recon.erc20_direct_total_usd
        ),
        "sum": _amount_json(recon.sum, decimals, recon.sum_usd),
        "on_chain_total_assets": _amount_json(
            recon.on_chain_total_assets, decimals, recon.on_chain_total_assets_usd
        ),
        "delta": {
            "raw": recon.delta,
            "formatted": _format_amount(abs(recon.delta), decimals),
            "usd": recon.delta_usd,
            "percent": recon.delta_percent,
        },
        "pending_withdrawals": _amount_json(
            recon.pending_withdrawals, decimals, recon.pending_withdrawals_usd
        ),
        "implied_market_total": {
            "raw": recon.implied_market_total,
            "formatted": _format_amount(recon.implied_market_total, decimals),
        },
        "market_storage_divergence": recon.market_storage_divergence,
    }


def _lending_health_json(health: VaultLendingHealth | None) -> LendingHealthJson | None:
    if not health or not health.has_lending_positions:
        return None
    return {
        "markets": [
            {
                "protocol": m.protocol,
                "market_id": m.market_id,
                "market_name": m.market_name,
                "substrate_id": _lending_substrate_ref(m.substrate_id),
                "current_ltv": m.current_ltv,
                "max_ltv": m.max_ltv,
                "health_factor": m.health_factor,
                "total_collateral_usd": m.total_collateral_usd,
                "total_debt_usd": m.total_debt_usd,
                "ltv_usage_percent": m.ltv_usage_percent,
                "is_warning": m.is_warning,
                "is_critical": m.is_critical,
            }
            for m in health.markets
        ],
        "unreadable": [
            {
                "protocol": u.protocol,
                "market_id": u.market_id,
                "market_name": u.market_name,
                "substrate_id": _lending_substrate_ref(u.substrate_id),
                "reason": u.reason,
            }
            for u in health.unreadable
        ],
        "worst_ltv_usage_percent": health.worst_ltv_usage,
    }


def health_check_lists(
    validation: VaultValidation,
) -> tuple[list[str], list[str], list[str]]:
    """``(ok, warnings, criticals)`` for ``fusion vault info``, with the
    uncovered-ERC20 finding split into one line per row."""
    ok: list[str] = []
    warnings: list[str] = []
    criticals: list[str] = []
    for finding in validation.findings:
        if finding.severity == "ok":
            ok.append(finding.message)
        elif finding.severity == "critical":
            criticals.append(finding.message)
        else:
            warnings.extend(finding.message.split("\n"))
    return ok, warnings, criticals


def _fuse_entry(
    addr: FuseAddress,
    contract: str | None,
    fuse_markets: dict[FuseAddress, MarketId],
) -> FuseJson:
    entry: FuseJson = {"address": addr, "contract": contract or "?"}
    if (mid := fuse_markets.get(addr)) is not None:
        entry["market_id"] = mid
        label = market_name(mid)
        entry["market"] = label if label != "UNKNOWN" else str(mid)
    return entry


def _symbol(data: VaultData, token: HexAddress) -> str | None:
    return data.token_symbols.get(parse_hex_address(token)) or None


def _morpho_breakdown_json(
    data: VaultData, positions: list[MorphoPositionBreakdown]
) -> list[MorphoBreakdownJson]:
    return [
        {
            "morpho_market_id": pb.market_id,
            "collateral_symbol": _symbol(data, pb.collateral_token),
            "loan_symbol": _symbol(data, pb.loan_token),
            "collateral": _build_breakdown_amount_json(
                data, int(pb.collateral), pb.collateral_token
            ),
            "borrow": _build_breakdown_amount_json(
                data, int(pb.borrow_assets), pb.loan_token
            ),
            "supply": _build_breakdown_amount_json(
                data, int(pb.supply_assets), pb.loan_token
            ),
        }
        for pb in positions
    ]


def _aave_breakdown_json(
    data: VaultData, positions: list[AaveV3PositionBreakdown]
) -> list[AaveBreakdownJson]:
    return [
        {
            "asset": pb.asset,
            "asset_symbol": _symbol(data, pb.asset),
            "supply": _build_breakdown_amount_json(data, int(pb.supply), pb.asset),
            "variable_debt": _build_breakdown_amount_json(
                data, int(pb.variable_debt), pb.asset
            ),
            "stable_debt": _build_breakdown_amount_json(
                data, int(pb.stable_debt), pb.asset
            ),
        }
        for pb in positions
    ]


def _balance_fuse_entry(
    data: VaultData,
    bf: BalanceFuse,
    assets_in_market: int,
    contract_name: str,
) -> BalanceFuseJson:
    market_label = market_name(bf.market_id)
    entry: BalanceFuseJson = {
        "market": market_label if market_label != "UNKNOWN" else str(bf.market_id),
        "market_id": bf.market_id,
        "balance": {
            "raw": assets_in_market,
            "formatted": _format_amount(assets_in_market, data.asset_decimals),
        },
        "fuse": bf.fuse,
        "contract": contract_name,
    }
    if data.total_assets > 0:
        entry["pct_of_total"] = round(assets_in_market / data.total_assets * 100, 2)
    if data.dependency_graph and bf.market_id in data.dependency_graph:
        deps = data.dependency_graph[bf.market_id]
        entry["depends_on"] = [format_market_label(d) for d in deps]
    if data.morpho_positions and bf.market_id in data.morpho_positions:
        entry["position_breakdown"] = _morpho_breakdown_json(
            data, data.morpho_positions[bf.market_id]
        )
    if data.aave_positions and bf.market_id in data.aave_positions:
        entry["position_breakdown"] = _aave_breakdown_json(
            data, data.aave_positions[bf.market_id]
        )
    return entry


def _substrate_entry(
    data: VaultData,
    sub_info: SubstrateInfo,
    contract_futs: dict[HexAddress, Future[str]],
) -> SubstrateEntryJson:
    entry: SubstrateEntryJson
    if sub_info.address:
        entry = {"address": sub_info.address}
        if symbol := data.token_symbols.get(sub_info.address):
            entry["symbol"] = symbol
        if contract := contract_futs[sub_info.address].result():
            entry["contract"] = contract
    else:
        entry = {"raw": sub_info.raw_hex}
        if sub_info.is_error:
            entry["error"] = True
    if sub_info.type_label:
        entry["substrate_type"] = sub_info.type_label
    if sub_info.extra:
        entry.update(sub_info.extra)
    return entry


def _substrates_json(
    pool: ThreadPoolExecutor,
    data: VaultData,
    resolve_name: Callable[[HexAddress], str],
) -> dict[str, list[SubstrateEntryJson]]:
    """Decoded substrates per balance-fuse market, with the snapshot's symbols
    and contract names resolved concurrently for every substrate address."""
    market_substrates = data.market_substrates or {}
    decoded: list[tuple[str, list[SubstrateInfo]]] = []
    addresses: set[HexAddress] = set()
    for bf in data.balance_fuses:
        if subs := market_substrates.get(bf.market_id):
            infos = [decode_substrate(sub, market_id=bf.market_id) for sub in subs]
            decoded.append((format_market_label(bf.market_id), infos))
            addresses.update(info.address for info in infos if info.address)

    contract_futs = {addr: pool.submit(resolve_name, addr) for addr in addresses}
    return {
        market_str: [_substrate_entry(data, info, contract_futs) for info in infos]
        for market_str, infos in decoded
    }


def _links_json(
    chain_id: ChainId, chain_label: str, vault_address: PlasmaVaultAddress
) -> VaultLinksJson:
    links: VaultLinksJson = {
        "ipor_app": f"{IPOR_APP_URL}/{chain_label}/{vault_address}"
    }
    if explorer_base := BLOCK_EXPLORER_URLS.get(chain_id):
        links["etherscan"] = f"{explorer_base}/address/{vault_address}"
    return links


def _total_assets_json(data: VaultData) -> UsdAmountJson:
    usd: float | None = None
    if data.asset_price_usd is not None:
        usd = (data.total_assets / 10**data.asset_decimals) * data.asset_price_usd
    return {
        "raw": data.total_assets,
        "formatted": _format_amount(data.total_assets, data.asset_decimals),
        "usd": usd,
    }


def _supply_cap_json(data: VaultData) -> AmountJson:
    return {
        "raw": data.supply_cap,
        "formatted": (
            "unlimited"
            if data.supply_cap == UINT256_MAX
            else _format_amount(data.supply_cap, data.share_decimals)
        ),
    }


def _managers_json(data: VaultData) -> ManagersJson:
    return {
        "access": data.access_manager,
        "price_oracle": data.price_oracle,
        "rewards": data.rewards_manager,
        "withdraw": data.withdraw_manager,
        "fee": data.fee_data.fee_manager if data.fee_data else None,
    }


def _markets_json(
    data: VaultData, resolve_name: Callable[[HexAddress], str]
) -> _MarketBlocksJson:
    """The fuse, balance-fuse and substrate blocks, in document order, with
    the contract names looked up concurrently."""
    with ThreadPoolExecutor() as pool:
        fuse_name_futs = {addr: pool.submit(resolve_name, addr) for addr in data.fuses}
        instant_name_futs = {
            addr: pool.submit(resolve_name, addr) for addr in data.instant_fuses
        }
        bf_contract_futs = [
            pool.submit(resolve_name, bf.fuse) for bf in data.balance_fuses
        ]
        fuse_markets = data.fuse_markets or {}
        balance_fuses, zero_balance_fuses = _partition_balance_fuses(
            [
                _balance_fuse_entry(
                    data,
                    bf,
                    data.market_balances[bf.market_id],
                    contract_fut.result() or "?",
                )
                for bf, contract_fut in zip(
                    data.balance_fuses, bf_contract_futs, strict=True
                )
            ]
        )
        markets: _MarketBlocksJson = {
            "fuses": [
                _fuse_entry(addr, fuse_name_futs[addr].result(), fuse_markets)
                for addr in data.fuses
            ],
            "balance_fuses": balance_fuses,
            "zero_balance_fuses": zero_balance_fuses,
            "instant_withdrawal_fuses": [
                _fuse_entry(addr, instant_name_futs[addr].result(), fuse_markets)
                for addr in data.instant_fuses
            ],
            "substrates": _substrates_json(pool, data, resolve_name),
        }
    return markets


def _no_name(_address: HexAddress) -> str:
    return ""


def build_vault_info_json(
    data: VaultData,
    *,
    resolve_name: Callable[[HexAddress], str] | None = None,
) -> VaultInfoJson:
    """Everything known about the vault as one JSON-serializable dict: the
    ``fusion vault info --json`` document, computed from the snapshot alone.

    ``data`` comes from `fetch_vault_data` (with ``metadata=True`` for token
    symbols); set its ``deployment_*`` fields first (`fetch_deployment_info`)
    for the ``deployment`` block. ``resolve_name`` names the fuses and
    substrate contracts (address to contract name, ``""`` when unknown), for
    example `ipor_fusion.explorer.get_contract_name` bound to the chain, an
    API key and ``ctx.cache``; without it no contract is named.
    """
    chain_id = data.chain_id
    chain_label = CHAIN_NAMES.get(chain_id, str(chain_id))
    if resolve_name is None:
        resolve_name = _no_name

    markets = _markets_json(data, resolve_name)
    validation = validate_vault_state(data)
    ok, warnings, criticals = health_check_lists(validation)

    return {
        "vault": data.vault,
        "name": data.vault_name or None,
        "links": _links_json(chain_id, chain_label, data.vault),
        "chain": chain_label,
        "chain_id": chain_id,
        "block": data.block_number,
        "is_latest": data.is_latest,
        "block_timestamp": data.block_timestamp,
        "block_timestamp_utc": _unix_to_iso(data.block_timestamp),
        "deployment": _build_deployment_json(data),
        "asset": {
            "address": data.asset,
            "symbol": data.asset_symbol,
            "decimals": data.asset_decimals,
            "price_usd": data.asset_price_usd,
        },
        "share_decimals": data.share_decimals,
        "total_assets": _total_assets_json(data),
        "total_supply": {
            "raw": data.total_supply,
            "formatted": _format_amount(data.total_supply, data.share_decimals),
        },
        "share_price": _build_share_price_json(data),
        "supply_cap": _supply_cap_json(data),
        "managers": _managers_json(data),
        "role_accounts": _role_accounts_json(data),
        "fees": _build_fees_json(data),
        "withdraw_manager_details": _build_withdraw_manager_json(data),
        "fuses": markets["fuses"],
        "balance_fuses": markets["balance_fuses"],
        "zero_balance_fuses": markets["zero_balance_fuses"],
        "instant_withdrawal_fuses": markets["instant_withdrawal_fuses"],
        "substrates": markets["substrates"],
        "dependency_graph": _build_dependency_graph_json(data),
        "erc20_balances": _erc20_balances_json(validation),
        "reconciliation": _reconciliation_json(
            validation.reconciliation, data.asset_decimals
        ),
        "lending_health": _lending_health_json(data.lending_health),
        "hypercore": _build_hypercore_json(data),
        "health_check": {"ok": ok, "warnings": warnings, "criticals": criticals},
    }


def _wad_to_usd(wad: int) -> float:
    return wad / 10**18


def _hypercore_pending_json(pending: HyperCorePendingState) -> HyperCorePendingJson:
    return {
        "pending": pending.pending,
        "settled": pending.settled,
        "refreshing": pending.refreshing,
        "action_class": pending.action_class.name,
        "settlement_mode": (
            pending.settlement_mode.name if pending.settlement_mode else None
        ),
        "reported_result": pending.reported_result.name,
        "action_id": pending.action_id,
        "action_nonce": pending.action_nonce,
        "pending_until": pending.pending_until,
        "pending_until_utc": _unix_to_iso(pending.pending_until),
        "enqueued_l1_block": pending.enqueued_l1_block,
        "enqueued_evm_block": pending.enqueued_evm_block,
        "cached_value_wad": pending.cached_value_wad,
        "current_l1_block": pending.current_l1_block,
        "current_timestamp": pending.current_timestamp,
        "l1_block_available": pending.l1_block_available,
    }


def _perp_leg_json(leg: HyperCorePerpLeg) -> HyperCorePerpLegJson:
    return {
        "dex": leg.dex,
        "account_value_usd6": leg.account_value,
        "value_usd": _wad_to_usd(leg.value_wad),
    }


def _build_hypercore_json(data: VaultData) -> HyperCoreJson | None:
    state = data.hypercore
    if state is None:
        return None
    nav = state.nav
    return {
        "market_id": MarketId(int(state.market_id)),
        "market": market_name(state.market_id),
        "balance_fuse": state.balance_fuse,
        "core_user_exists": nav.core_user_exists,
        "perp_dex_bitmap": nav.perp_dex_bitmap,
        "nav_wad": nav.value_wad,
        "nav_usd": _wad_to_usd(nav.value_wad),
        "balance_fuse_value_wad": state.balance_fuse_value_wad,
        "nav_matches_balance_fuse": state.nav_matches_balance_fuse,
        "spot": [
            {
                "token_index": leg.token_index,
                "evm_asset": leg.evm_asset,
                "total_wei": leg.total,
                "hold_wei": leg.hold,
                "wei_decimals": leg.wei_decimals,
                "price_usd": leg.price.readable() if leg.price else None,
                "value_usd": _wad_to_usd(leg.value_wad),
            }
            for leg in nav.spot
        ],
        "native_perp": _perp_leg_json(nav.native_perp) if nav.native_perp else None,
        "hip3": [_perp_leg_json(leg) for leg in nav.hip3],
        "perp_markets": [
            {
                "asset": pm.asset,
                "dex": pm.dex,
                "read_index": pm.read_index,
                "coin": pm.coin,
                "max_notional_usd6": pm.max_notional_usd6,
                "reduce_only_required": pm.reduce_only_required,
            }
            for pm in state.perp_markets
        ],
        "pending": _hypercore_pending_json(state.pending) if state.pending else None,
        "core_user_exists_note": _HYPERCORE_DOCS["core_user_exists"],
        "perp_dex_bitmap_note": _HYPERCORE_DOCS["perp_dex_bitmap"],
        "nav_wad_note": _HYPERCORE_DOCS["nav_wad"],
        "balance_fuse_value_wad_note": _HYPERCORE_DOCS["balance_fuse_value_wad"],
        "nav_matches_balance_fuse_note": _HYPERCORE_DOCS["nav_matches_balance_fuse"],
        "spot_note": _HYPERCORE_DOCS["spot"],
        "native_perp_note": _HYPERCORE_DOCS["native_perp"],
        "hip3_note": _HYPERCORE_DOCS["hip3"],
        "perp_markets_note": _HYPERCORE_DOCS["perp_markets"],
        "pending_note": _HYPERCORE_DOCS["pending"],
    }

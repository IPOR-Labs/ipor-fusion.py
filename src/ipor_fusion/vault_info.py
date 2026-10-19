"""The ``fusion vault info --json`` document: `build_vault_info_json` renders a
fetched `VaultData` (with its validation) as one JSON-serializable dict."""

from __future__ import annotations

from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from datetime import UTC, datetime
from functools import partial
from typing import Any

from eth_typing import HexAddress

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.fee_manager import HighWaterMarkPerformanceFee, RecipientFee
from ipor_fusion.core.plasma_vault import BalanceFuse, PlasmaVault
from ipor_fusion.explorer import get_contract_name
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
    ChainId,
    FuseAddress,
    MarketId,
    PlasmaVaultAddress,
    parse_hex_address,
)
from ipor_fusion.validation import (
    Erc20Holdings,
    Reconciliation,
    VaultValidation,
    _format_amount,
    read_erc20_holdings,
    validate_vault_state,
)
from ipor_fusion.vault_data import (
    VaultData,
    _safe_call,
    resolve_token_decimals,
    resolve_token_symbol,
)

BLOCK_EXPLORER_URLS: dict[int, str] = {
    1: "https://etherscan.io",
    42161: "https://arbiscan.io",
    8453: "https://basescan.org",
    10: "https://optimistic.etherscan.io",
    137: "https://polygonscan.com",
    56: "https://bscscan.com",
    43114: "https://snowtrace.io",
    250: "https://ftmscan.com",
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


def _unix_to_iso(timestamp: int) -> str:
    """Unix seconds as the ISO-8601 UTC string every `*_utc` key carries.

    Renders 0 as the epoch. Callers for which 0 is the contract's "never"
    sentinel must guard and emit null themselves.
    """
    return datetime.fromtimestamp(timestamp, tz=UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _partition_balance_fuses(entries: list[dict]) -> tuple[list[dict], list[dict]]:
    """Split balance-fuse entries into ``(venues, zero_balance)``.

    Venues are real ``*BalanceFuse`` markets where assets can sit. Zero-balance
    fuses are ZeroBalanceFuse-backed markets (swap/flash-loan/admin plumbing)
    with a structurally-zero balance — capabilities, not liquidity markets.
    Splitting keeps both visible while letting a consumer count only venues as
    markets.
    """
    venues = [e for e in entries if e.get("contract") not in _CAPABILITY_BALANCE_FUSES]
    zero_balance = [
        e for e in entries if e.get("contract") in _CAPABILITY_BALANCE_FUSES
    ]
    return venues, zero_balance


def _role_accounts_json(data: VaultData) -> list[dict[str, Any]] | None:
    if data.role_accounts is None:
        return None
    return [ra.to_dict() for ra in data.role_accounts]


def _build_share_price_json(data: VaultData) -> dict | None:
    if data.total_supply == 0:
        return None
    price = (data.total_assets / 10**data.asset_decimals) / (
        data.total_supply / 10**data.share_decimals
    )
    result: dict = {
        "asset": round(price, 10),
    }
    if data.asset_price_usd is not None:
        result["usd"] = round(price * data.asset_price_usd, 10)
    return result


def _build_deployment_json(data: VaultData) -> dict | None:
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
) -> list[dict] | None:
    if recipients is None:
        return None
    return [
        {
            "recipient": rf.recipient,
            "fee_percent": _bps_to_percent(rf.fee_value),
            "fee_bps": rf.fee_value,
        }
        for rf in recipients
    ]


def _build_high_water_mark_json(hwm: HighWaterMarkPerformanceFee | None) -> dict | None:
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


def _build_fees_json(data: VaultData) -> dict | None:
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

    unrealized: dict | None = None
    if fees.unrealized_management_fee is not None:
        unrealized = {
            "raw": fees.unrealized_management_fee,
            "formatted": _format_amount(
                fees.unrealized_management_fee, data.asset_decimals
            ),
        }
        if data.asset_price_usd is not None:
            unrealized["usd"] = round(
                (fees.unrealized_management_fee / 10**data.asset_decimals)
                * data.asset_price_usd,
                2,
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


def _build_withdraw_manager_json(
    data: VaultData, plasma_vault: PlasmaVault
) -> dict | None:
    if data.withdraw_manager_data is None:
        return None
    wmd = data.withdraw_manager_data
    sdec = data.share_decimals
    adec = data.asset_decimals

    pending = wmd.pending_requests
    total_pending_shares = sum((r.shares for r in pending or []), 0)

    requests_json = []
    for req in pending or []:
        assets: int | None = _safe_call(
            lambda s=req.shares: plasma_vault.convert_to_assets(s).call()  # type: ignore[misc]
        )
        entry: dict = {
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
            entry["assets"] = {
                "raw": assets,
                "formatted": _format_amount(assets, adec),
            }
            if data.asset_price_usd is not None:
                entry["assets"]["usd"] = round(
                    (assets / 10**adec) * data.asset_price_usd, 2
                )
        requests_json.append(entry)

    result: dict = {
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
    return result


def _build_breakdown_amount_json(
    ctx: Web3Context,
    raw: int,
    token_address: HexAddress,
    prices_usd: dict[HexAddress, float] | None,
) -> dict[str, Any]:
    """Build the JSON entry for one breakdown amount (collateral/borrow/supply/debt).

    Always includes raw + token. When ERC-20 metadata is resolvable, adds
    symbol, decimals, and `formatted` (decimal-shifted human string). When the
    oracle has a USD price for the token, adds `usd` (rounded to 2 decimals).
    """
    entry: dict[str, Any] = {"raw": raw, "token": token_address}
    if symbol := resolve_token_symbol(ctx, token_address):
        entry["symbol"] = symbol
    decimals = resolve_token_decimals(ctx, token_address)
    if decimals is not None:
        entry["decimals"] = decimals
        entry["formatted"] = _format_amount(raw, decimals)
        if (
            prices_usd
            and (price := prices_usd.get(parse_hex_address(token_address))) is not None
        ):
            entry["usd"] = round((raw / 10**decimals) * price, 2)
    return entry


def _build_dependency_graph_json(data: VaultData) -> dict | None:
    if not data.dependency_graph:
        return None

    from ipor_fusion.dependency_graph import (
        compute_update_groups,
        compute_update_reach,
    )

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


def _erc20_balances_json(validation: VaultValidation) -> list[dict[str, Any]]:
    entries: list[dict[str, Any]] = []
    for token in validation.erc20.tokens:
        entry: dict[str, Any] = {"address": token.address, "symbol": token.symbol}
        if token.decimals is not None and token.balance is not None:
            entry["decimals"] = token.decimals
            entry["balance"] = {
                "raw": token.balance,
                "formatted": _format_amount(token.balance, token.decimals),
            }
            entry["price_usd"] = token.price_usd
            entry["usd_value"] = token.usd_value
        entry["note"] = token.note
        entries.append(entry)
    return entries


def _amount_json(raw: int, decimals: int, usd: float | None) -> dict[str, Any]:
    return {"raw": raw, "formatted": _format_amount(raw, decimals), "usd": usd}


def _reconciliation_json(recon: Reconciliation, decimals: int) -> dict[str, Any]:
    return {
        "balance_fuses_total": _amount_json(
            recon.balance_fuses_total, decimals, recon.balance_fuses_total_usd
        ),
        "underlying_on_vault": _amount_json(
            recon.underlying_on_vault, decimals, recon.underlying_on_vault_usd
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


def _lending_health_json(health: VaultLendingHealth | None) -> dict[str, Any] | None:
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
) -> dict[str, Any]:
    entry: dict[str, Any] = {"address": addr, "contract": contract or "?"}
    if (mid := fuse_markets.get(addr)) is not None:
        entry["market_id"] = mid
        label = market_name(mid)
        entry["market"] = label if label != "UNKNOWN" else str(mid)
    return entry


def _morpho_breakdown_json(
    ctx: Web3Context, data: VaultData, positions: list[MorphoPositionBreakdown]
) -> list[dict[str, Any]]:
    prices = data.token_prices_usd
    return [
        {
            "morpho_market_id": pb.market_id,
            "collateral_symbol": resolve_token_symbol(ctx, pb.collateral_token) or None,
            "loan_symbol": resolve_token_symbol(ctx, pb.loan_token) or None,
            "collateral": _build_breakdown_amount_json(
                ctx, int(pb.collateral), pb.collateral_token, prices
            ),
            "borrow": _build_breakdown_amount_json(
                ctx, int(pb.borrow_assets), pb.loan_token, prices
            ),
            "supply": _build_breakdown_amount_json(
                ctx, int(pb.supply_assets), pb.loan_token, prices
            ),
        }
        for pb in positions
    ]


def _aave_breakdown_json(
    ctx: Web3Context, data: VaultData, positions: list[AaveV3PositionBreakdown]
) -> list[dict[str, Any]]:
    prices = data.token_prices_usd
    return [
        {
            "asset": pb.asset,
            "asset_symbol": resolve_token_symbol(ctx, pb.asset) or None,
            "supply": _build_breakdown_amount_json(
                ctx, int(pb.supply), pb.asset, prices
            ),
            "variable_debt": _build_breakdown_amount_json(
                ctx, int(pb.variable_debt), pb.asset, prices
            ),
            "stable_debt": _build_breakdown_amount_json(
                ctx, int(pb.stable_debt), pb.asset, prices
            ),
        }
        for pb in positions
    ]


def _balance_fuse_entry(
    ctx: Web3Context,
    data: VaultData,
    bf: BalanceFuse,
    assets_in_market: int,
    contract_name: str,
) -> dict[str, Any]:
    market_label = market_name(bf.market_id)
    entry: dict[str, Any] = {
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
            ctx, data, data.morpho_positions[bf.market_id]
        )
    if data.aave_positions and bf.market_id in data.aave_positions:
        entry["position_breakdown"] = _aave_breakdown_json(
            ctx, data, data.aave_positions[bf.market_id]
        )
    return entry


def _substrate_entry(
    sub_info: SubstrateInfo,
    symbol_futs: dict[HexAddress, Future[str]],
    contract_futs: dict[HexAddress, Future[str]],
) -> dict[str, Any]:
    entry: dict[str, Any]
    if sub_info.address:
        entry = {"address": sub_info.address}
        if symbol := symbol_futs[sub_info.address].result():
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
    ctx: Web3Context,
    pool: ThreadPoolExecutor,
    data: VaultData,
    resolve_name: Callable[[HexAddress], str],
) -> dict[str, list[dict[str, Any]]]:
    """Decoded substrates per balance-fuse market, with symbols and contract
    names resolved concurrently for every substrate address."""
    market_substrates = data.market_substrates or {}
    decoded: list[tuple[str, list[SubstrateInfo]]] = []
    addresses: set[HexAddress] = set()
    for bf in data.balance_fuses:
        if subs := market_substrates.get(bf.market_id):
            infos = [decode_substrate(sub, market_id=bf.market_id) for sub in subs]
            decoded.append((format_market_label(bf.market_id), infos))
            addresses.update(info.address for info in infos if info.address)

    symbol_futs = {
        addr: pool.submit(resolve_token_symbol, ctx, addr) for addr in addresses
    }
    contract_futs = {addr: pool.submit(resolve_name, addr) for addr in addresses}
    return {
        market_str: [
            _substrate_entry(info, symbol_futs, contract_futs) for info in infos
        ]
        for market_str, infos in decoded
    }


def _links_json(
    chain_id: ChainId, chain_label: str, vault_address: PlasmaVaultAddress
) -> dict[str, str]:
    links = {"ipor_app": f"{IPOR_APP_URL}/{chain_label}/{vault_address}"}
    if explorer_base := BLOCK_EXPLORER_URLS.get(chain_id):
        links["etherscan"] = f"{explorer_base}/address/{vault_address}"
    return links


def _total_assets_json(data: VaultData) -> dict[str, Any]:
    usd: float | None = None
    if data.asset_price_usd is not None:
        usd = (data.total_assets / 10**data.asset_decimals) * data.asset_price_usd
    return {
        "raw": data.total_assets,
        "formatted": _format_amount(data.total_assets, data.asset_decimals),
        "usd": usd,
    }


def _supply_cap_json(data: VaultData) -> dict[str, Any]:
    return {
        "raw": data.supply_cap,
        "formatted": (
            "unlimited"
            if data.supply_cap == UINT256_MAX
            else _format_amount(data.supply_cap, data.asset_decimals)
        ),
    }


def _managers_json(data: VaultData) -> dict[str, Any]:
    return {
        "access": data.access_manager,
        "price_oracle": data.price_oracle_addr,
        "rewards": data.rewards_manager,
        "withdraw": data.withdraw_manager,
        "fee": data.fee_data.fee_manager if data.fee_data else None,
    }


def _markets_json(
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    data: VaultData,
    resolve_name: Callable[[HexAddress], str],
) -> tuple[dict[str, Any], Erc20Holdings]:
    """The fuse, balance-fuse and substrate blocks, in document order, plus the
    vault's ERC20 holdings, read concurrently with the name lookups."""
    with ThreadPoolExecutor() as pool:
        # Runs its own fan-out; submitted first so it overlaps the name lookups.
        erc20_fut = pool.submit(
            read_erc20_holdings,
            ctx,
            plasma_vault,
            data,
            partial(resolve_token_symbol, ctx),
        )
        fuse_name_futs = {addr: pool.submit(resolve_name, addr) for addr in data.fuses}
        instant_name_futs = {
            addr: pool.submit(resolve_name, addr) for addr in data.instant_fuses
        }
        bf_contract_futs = [
            pool.submit(resolve_name, bf.fuse) for bf in data.balance_fuses
        ]
        bf_balance_futs = [
            pool.submit(plasma_vault.total_assets_in_market(bf.market_id).call)
            for bf in data.balance_fuses
        ]
        fuse_markets = data.fuse_markets or {}
        balance_fuses, zero_balance_fuses = _partition_balance_fuses(
            [
                _balance_fuse_entry(
                    ctx, data, bf, balance_fut.result(), contract_fut.result() or "?"
                )
                for bf, balance_fut, contract_fut in zip(
                    data.balance_fuses, bf_balance_futs, bf_contract_futs, strict=True
                )
            ]
        )
        markets = {
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
            "substrates": _substrates_json(ctx, pool, data, resolve_name),
        }
    return markets, erc20_fut.result()


def build_vault_info_json(
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    data: VaultData,
    *,
    chain_id: ChainId,
    chain_label: str,
    resolve_name: Callable[[HexAddress], str] | None = None,
) -> dict[str, Any]:
    """Everything known about the vault as one JSON-serializable dict: the
    ``fusion vault info --json`` document.

    ``data`` comes from `fetch_vault_data`; set its ``deployment_*`` fields
    first (`fetch_deployment_info`) for the ``deployment`` block. The vault is
    validated on the way (``health_check``, ``reconciliation``), reading each
    market's balance and the ERC20 holdings. ``resolve_name`` names the fuses
    and substrate contracts (address to contract name, ``""`` when unknown);
    the default reads `ipor_fusion.explorer.get_contract_name` without an
    API key, so only names already in the on-disk cache.
    """
    if resolve_name is None:
        resolve_name = partial(get_contract_name, chain_id)

    markets, erc20 = _markets_json(ctx, plasma_vault, data, resolve_name)
    # Venues only, once per market: zero-balance fuses never hold assets.
    market_balances: dict[MarketId, int] = {}
    for bfj in markets["balance_fuses"]:
        market_balances.setdefault(bfj["market_id"], bfj["balance"]["raw"])
    validation = validate_vault_state(ctx, plasma_vault, data, market_balances, erc20)
    ok, warnings, criticals = health_check_lists(validation)

    return {
        "vault": plasma_vault.address,
        "name": data.vault_name or None,
        "links": _links_json(chain_id, chain_label, plasma_vault.address),
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
        "withdraw_manager_details": _build_withdraw_manager_json(data, plasma_vault),
        **markets,
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


def _hypercore_pending_json(pending: HyperCorePendingState) -> dict[str, Any]:
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


def _perp_leg_json(leg: HyperCorePerpLeg) -> dict[str, Any]:
    return {
        "dex": leg.dex,
        "account_value_usd6": leg.account_value,
        "value_usd": _wad_to_usd(leg.value_wad),
    }


def _build_hypercore_json(data: VaultData) -> dict[str, Any] | None:
    state = data.hypercore
    if state is None:
        return None
    nav = state.nav
    return {
        "market_id": int(state.market_id),
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
        **{f"{key}_note": text for key, text in _HYPERCORE_DOCS.items()},
    }

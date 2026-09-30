from __future__ import annotations

import logging
from collections.abc import Callable, Sequence
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from functools import partial
from typing import Any, TypeVar

import requests
from eth_abi.exceptions import DecodingError
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted, Web3RPCError
from web3.types import ChecksumAddress, HexStr

from ipor_fusion.chains import ensure_supported_chain
from ipor_fusion.cli.config_store import (
    load_contract_cache,
    load_deployment_cache,
    update_contract_cache,
    update_deployment_cache,
)
from ipor_fusion.cli.explorer import get_deployment_tx
from ipor_fusion.core.access import AccessManager, RoleAccount, role_account_sort_key
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.fee_manager import (
    FeeAccount,
    FeeManager,
    HighWaterMarkPerformanceFee,
    RecipientFee,
)
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.core.oracle import PriceOracleMiddleware
from ipor_fusion.core.plasma_vault import BalanceFuse, PlasmaVault
from ipor_fusion.core.withdraw_manager import AccountRequest, WithdrawManager
from ipor_fusion.readers.aave_v3 import (
    AaveV3FuseReader,
    AaveV3PoolAddressesProvider,
    AaveV3PositionBreakdown,
    AaveV3Reader,
)
from ipor_fusion.readers.lending_health import (
    AAVE_V3_MARKET_IDS,
    MORPHO_MARKET_IDS,
    VaultLendingHealth,
    fetch_vault_lending_health,
)
from ipor_fusion.readers.morpho import (
    MorphoPositionBreakdown,
    MorphoReader,
    morpho_blue_address,
)
from ipor_fusion.types import MorphoBlueMarketId

T = TypeVar("T")

_logger = logging.getLogger(__name__)

_NO_CONTRACT = "no contract"

_ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass
class _FeeData:
    """Vault fee configuration: vault-level fee data plus FeeManager state.

    Every field is optional because each degrades independently: a vault may
    have no fee account configured (no FeeManager at all), and FeeManagers
    predating the deposit fee / high-water mark revert on those getters.
    Scales differ per source, so every field names its own: `*_bps` is basis
    points (10000 = 100%), `*_wad` is WAD (1e18 = 100%). Pair each with the
    matching converter — `_bps_to_percent` / `_wad_to_percent` — and a
    mismatch reads wrong at the call site.

    `*_vault_bps` and `*_manager_bps` are the SAME fee read from two places:
    the vault charges the former, the FeeManager believes it configured the
    latter. They agree in a healthy deployment; a difference means the two
    configurations have drifted.
    """

    performance_fee_vault_bps: int | None = None
    management_fee_vault_bps: int | None = None
    management_fee_last_update: int | None = None
    unrealized_management_fee: int | None = None
    fee_manager: str | None = None
    ipor_dao_fee_recipient: str | None = None
    deposit_fee_wad: int | None = None
    performance_fee_manager_bps: int | None = None
    management_fee_manager_bps: int | None = None
    performance_fee_recipients: list[RecipientFee] | None = None
    management_fee_recipients: list[RecipientFee] | None = None
    high_water_mark: HighWaterMarkPerformanceFee | None = None


@dataclass
class _WithdrawManagerData:
    """On-chain state snapshot from the WithdrawManager contract."""

    withdraw_window: int
    # None (not 0) when the getter failed: a zero fee and an unreadable fee
    # are different facts, and the fees section reports both.
    request_fee: int | None
    withdraw_fee: int | None
    shares_to_release: int
    last_release_funds_timestamp: int
    pending_requests: list[AccountRequest]


@dataclass
class _VaultData:
    block_number: int
    is_latest: bool
    block_timestamp: int
    share_decimals: int
    asset_decimals: int
    total_assets: int
    total_supply: int
    supply_cap: int
    asset: str
    asset_symbol: str
    access_manager: str
    price_oracle_addr: str
    rewards_manager: str | None
    withdraw_manager: str | None
    asset_price_usd: float | None
    fuses: list
    balance_fuses: list
    instant_fuses: list
    # Underlying asset balance held directly on the vault (raw). Part of
    # totalAssets via ERC4626 base accounting; by design NOT tracked as an
    # ERC20_VAULT_BALANCE substrate, so it is read directly in
    # _fetch_vault_data rather than derived from substrates.
    underlying_balance_on_vault: int = 0
    vault_name: str = ""
    deployment_block: int | None = None
    deployment_timestamp: int | None = None
    # Short error code from `_fetch_deployment_info` when block/timestamp could
    # not be resolved. Surfaced in CLI output and the JSON `deployment` field
    # so users can distinguish "lookup not attempted/failed" from
    # "no deployment exists" (e.g. `etherscan-paid-tier-required` on Base).
    deployment_error: str | None = None
    withdraw_manager_data: _WithdrawManagerData | None = None
    fee_data: _FeeData | None = None
    dependency_graph: dict[int, list[int]] | None = None
    lending_health: VaultLendingHealth | None = None
    # Morpho per-substrate position breakdown (collateral / borrow / supply),
    # keyed by IPOR market_id. Populated only for markets in MORPHO_MARKET_IDS
    # whose substrates resolve to valid Morpho Blue market IDs.
    morpho_positions: dict[int, list[MorphoPositionBreakdown]] | None = None
    # Aave V3 per-asset position breakdown (supply / variable_debt / stable_debt),
    # keyed by IPOR market_id. Populated only for markets in AAVE_V3_MARKET_IDS
    # whose balance fuse resolves to a Pool and whose substrates decode to
    # valid asset addresses. Empty positions are filtered out (a vault may
    # allow an asset without using it).
    aave_positions: dict[int, list[AaveV3PositionBreakdown]] | None = None
    # USD price per token address (lowercase) for tokens appearing in lending
    # breakdowns (Morpho loan/collateral, Aave reserve assets). Sourced from the
    # vault's PriceOracleMiddleware. Missing keys mean the oracle has no source
    # configured for that token.
    token_prices_usd: dict[str, float] | None = None
    # MARKET_ID() reported by each registered action fuse. Keyed by checksummed
    # fuse address. Fuses that don't expose MARKET_ID() (or whose call reverts)
    # are absent from the dict — used to detect orphan markets (action fuses
    # with no matching balance fuse, which silently drop positions from
    # totalAssets).
    fuse_markets: dict[str, int] | None = None
    # Substrates registered for each balance-fuse market. Allows the fuse
    # tables to report substrate counts per fuse without duplicating reads.
    market_substrates: dict[int, list[bytes]] | None = None
    # Confirmed AccessManager role holders, sorted by role_account_sort_key.
    # None when the RoleGranted log scan failed (see _ROLE_SCAN_ERRORS).
    role_accounts: list[RoleAccount] | None = None


# Failure modes of the heavy RoleGranted scan: JSON-RPC rejections/limits plus
# transport-level errors — web3's HTTPProvider re-raises raw requests
# exceptions (read timeouts, 429/5xx via raise_for_status).
_ROLE_SCAN_ERRORS = (
    ContractLogicError,
    Web3RPCError,
    TimeExhausted,
    requests.RequestException,
)


def _safe_call(func: Callable[[], T]) -> T | None:
    try:
        return func()
    except (ContractLogicError, Web3RPCError, TimeExhausted) as exc:
        _logger.debug("_safe_call suppressed %s: %s", type(exc).__name__, exc)
        return None


_MARKET_ID_SELECTOR = function_signature_to_4byte_selector("MARKET_ID()")


def _fuse_market_id_call(ctx: Web3Context, fuse: ChecksumAddress) -> Call[int]:
    """The immutable MARKET_ID() every IPOR Fusion fuse exposes.

    Read with `try_aggregate`: a fuse that reverts or lacks the getter (a
    non-fuse address ever found in getFuses()) reads as ``None``.
    """
    return Call(
        to=fuse,
        data=_MARKET_ID_SELECTOR,
        output_types=["uint256"],
        decoder=int,
        ctx=ctx,
    )


def _read_batch(
    ctx: Web3Context,
    required: Sequence[Call[Any]],
    optional: Sequence[Call[Any]] = (),
) -> tuple[list[Any], list[Any]]:
    """Read `required` + `optional` in one Multicall3 round trip.

    A failing optional read is ``None``. A failing required read is re-run on
    its own, so it raises exactly what `Call.call()` would.
    """
    results = Multicall3(ctx).try_aggregate([*required, *optional])
    head = [
        value if value is not None else call.call()
        for value, call in zip(results, required, strict=False)
    ]
    return head, results[len(required) :]


def _resolve_token_symbol(ctx: Web3Context, address: str) -> str:
    cache = load_contract_cache()
    cache_key = f"symbol:{address}"
    if cached := cache.get(cache_key):
        return cached

    checksum = Web3.to_checksum_address(address)
    code = ctx.web3.eth.get_code(checksum)
    if not code or code == b"":
        update_contract_cache(cache_key, _NO_CONTRACT)
        return _NO_CONTRACT

    try:
        symbol = ERC20(ctx, checksum).symbol().call()
    except Exception:
        symbol = ""
    if symbol:
        update_contract_cache(cache_key, symbol)
    return symbol


def _resolve_token_decimals(ctx: Web3Context, address: str) -> int | None:
    """Resolve ERC-20 decimals, cached. Returns None if address is not a contract."""
    cache = load_contract_cache()
    cache_key = f"decimals:{address}"
    if cached := cache.get(cache_key):
        return int(cached)

    checksum = Web3.to_checksum_address(address)
    code = ctx.web3.eth.get_code(checksum)
    if not code or code == b"":
        return None

    try:
        decimals = ERC20(ctx, checksum).decimals().call()
    except Exception:
        return None
    update_contract_cache(cache_key, str(decimals))
    return decimals


def _fetch_withdraw_manager(
    ctx: Web3Context, plasma_vault: PlasmaVault
) -> tuple[ChecksumAddress | None, _WithdrawManagerData | None]:
    """Resolve the vault's WithdrawManager and snapshot its state."""
    address = plasma_vault.withdraw_manager_address()
    if not address:
        return None, None
    wm = WithdrawManager(ctx, address)
    _, (window, request_fee, withdraw_fee, shares, last_ts) = _read_batch(
        ctx,
        [],
        [
            wm.get_withdraw_window(),
            wm.get_request_fee(),
            wm.get_withdraw_fee(),
            wm.get_shares_to_release(),
            wm.get_last_release_funds_timestamp(),
        ],
    )
    return address, _WithdrawManagerData(
        withdraw_window=window or 0,
        request_fee=request_fee,
        withdraw_fee=withdraw_fee,
        shares_to_release=shares or 0,
        last_release_funds_timestamp=last_ts or 0,
        pending_requests=_safe_call(wm.get_pending_requests) or [],
    )


def _fetch_fee_manager_address(
    ctx: Web3Context, fee_account: ChecksumAddress
) -> str | None:
    """Resolve FeeAccount.FEE_MANAGER(); None when it is not a FeeAccount.

    The fee account reported by a vault is not guaranteed to be a FeeAccount
    contract — older deployments can name a plain recipient address. An EOA
    answers eth_call with empty data, which fails ABI decoding instead of
    reverting, so `_safe_call` alone does not cover it.
    """
    try:
        return _safe_call(FeeAccount(ctx, fee_account).fee_manager().call)
    except DecodingError:
        return None


def _fetch_fee_data(ctx: Web3Context, plasma_vault: PlasmaVault) -> _FeeData:
    """Fetch the vault's fee configuration, including the FeeManager hop.

    Three dependent round trips: vault-level fee data, then
    FeeAccount.FEE_MANAGER() to discover the FeeManager, then the FeeManager
    getters. Nothing raises on a revert — every read degrades to ``None``, so
    vaults without a fee account and FeeManagers predating the deposit fee or
    high-water mark still produce a usable snapshot.
    """
    _, (perf, mgmt, unrealized) = _read_batch(
        ctx,
        [],
        [
            plasma_vault.get_performance_fee_data(),
            plasma_vault.get_management_fee_data(),
            plasma_vault.get_unrealized_management_fee(),
        ],
    )
    result = _FeeData(
        performance_fee_vault_bps=perf.fee_in_percentage if perf else None,
        management_fee_vault_bps=mgmt.fee_in_percentage if mgmt else None,
        management_fee_last_update=mgmt.last_update_timestamp if mgmt else None,
        unrealized_management_fee=unrealized,
    )

    # A FeeManager deploys two separate escrow accounts (performance and
    # management) but is itself the FEE_MANAGER of both, so either hop
    # resolves to the same address; take whichever is configured.
    fee_account = next(
        (
            data.fee_account
            for data in (perf, mgmt)
            if data is not None and data.fee_account != _ZERO_ADDRESS
        ),
        None,
    )
    if fee_account is None:
        return result

    fee_manager_addr = _fetch_fee_manager_address(ctx, fee_account)
    if not fee_manager_addr or fee_manager_addr == _ZERO_ADDRESS:
        return result
    result.fee_manager = fee_manager_addr

    fee_manager = FeeManager(ctx, fee_manager_addr)
    (
        _,
        (
            result.deposit_fee_wad,
            result.performance_fee_manager_bps,
            result.management_fee_manager_bps,
            result.performance_fee_recipients,
            result.management_fee_recipients,
            result.ipor_dao_fee_recipient,
            result.high_water_mark,
        ),
    ) = _read_batch(
        ctx,
        [],
        [
            fee_manager.get_deposit_fee(),
            fee_manager.get_total_performance_fee(),
            fee_manager.get_total_management_fee(),
            fee_manager.get_performance_fee_recipients(),
            fee_manager.get_management_fee_recipients(),
            fee_manager.get_ipor_dao_fee_recipient_address(),
            fee_manager.get_plasma_vault_high_water_mark_performance_fee(),
        ],
    )
    return result


def _collect_morpho_substrates(
    market_substrates: dict[int, list[bytes]],
) -> dict[int, list[MorphoBlueMarketId]]:
    """Filter + parse Morpho Blue market IDs from raw substrate bytes."""
    result: dict[int, list[MorphoBlueMarketId]] = {}
    for mid, subs in market_substrates.items():
        if mid not in MORPHO_MARKET_IDS:
            continue
        morpho_ids = [
            MorphoBlueMarketId(sub.hex()) for sub in subs if len(sub.hex()) == 64
        ]
        if morpho_ids:
            result[mid] = morpho_ids
    return result


def _fetch_morpho_positions(
    ctx: Web3Context,
    pool: ThreadPoolExecutor,
    vault_addr: ChecksumAddress,
    market_substrates: dict[int, list[bytes]],
) -> dict[int, list[MorphoPositionBreakdown]] | None:
    """Fetch collateral / borrow / supply breakdown for every Morpho substrate.

    Each IPOR Morpho market (MORPHO_MARKET_IDS) can hold multiple morpho
    market_id substrates; each substrate has an independent position. The
    on-chain balance fuse reports a single netted number per IPOR market —
    this helper exposes the three-way decomposition behind that number.
    """
    per_market_substrates = _collect_morpho_substrates(market_substrates)
    if not per_market_substrates:
        return None

    reader = MorphoReader(ctx, morpho_blue_address(ctx.chain_id))
    futures: dict[int, list[Future]] = {
        mid: [
            pool.submit(
                _safe_call,
                lambda mid_hex=morpho_mid: reader.position_breakdown(
                    mid_hex, vault_addr
                ),
            )
            for morpho_mid in morpho_mids
        ]
        for mid, morpho_mids in per_market_substrates.items()
    }

    result: dict[int, list[MorphoPositionBreakdown]] = {}
    for mid, per_market in futures.items():
        breakdowns = [bd for fut in per_market if (bd := fut.result()) is not None]
        if breakdowns:
            result[mid] = breakdowns
    return result or None


def _collect_aave_substrate_assets(
    market_substrates: dict[int, list[bytes]],
) -> dict[int, list[ChecksumAddress]]:
    """Filter + parse asset addresses from Aave V3 substrate bytes.

    Aave substrates are zero-padded plain addresses (12 zero bytes + 20 address
    bytes). Returns a per-market list of checksummed asset addresses.
    """
    result: dict[int, list[ChecksumAddress]] = {}
    for mid, subs in market_substrates.items():
        if mid not in AAVE_V3_MARKET_IDS:
            continue
        assets: list[ChecksumAddress] = []
        for sub in subs:
            hex_str = sub.hex()
            if len(hex_str) != 64:
                continue
            assets.append(Web3.to_checksum_address("0x" + hex_str[24:]))
        if assets:
            result[mid] = assets
    return result


def _fetch_aave_pools(
    ctx: Web3Context, balance_fuses: list[BalanceFuse]
) -> dict[int, ChecksumAddress]:
    """Map every Aave V3 market to the Pool its balance fuse reads.

    Aave V3 Core, Aave V3 Prime and SparkLend share the fuse contracts but not
    the Pool, so the Pool is resolved per market rather than per chain: the
    fuse's PoolAddressesProvider, then its Pool, one batch each. A balance
    fuse that is not an Aave V3 fuse reverts or answers with empty data and is
    left out.
    """
    fuses = {
        bf.market_id: bf.fuse
        for bf in balance_fuses
        if bf.market_id in AAVE_V3_MARKET_IDS
    }
    if not fuses:
        return {}
    multicall = Multicall3(ctx)
    providers = multicall.try_aggregate(
        [
            AaveV3FuseReader(ctx, fuse).pool_addresses_provider()
            for fuse in fuses.values()
        ]
    )
    resolved = {
        mid: provider
        for mid, provider in zip(fuses, providers, strict=True)
        if provider is not None
    }
    pools = multicall.try_aggregate(
        [AaveV3PoolAddressesProvider(ctx, p).get_pool() for p in resolved.values()]
    )
    return {
        mid: pool for mid, pool in zip(resolved, pools, strict=True) if pool is not None
    }


def _fetch_aave_positions(
    ctx: Web3Context,
    pool: ThreadPoolExecutor,
    vault_addr: ChecksumAddress,
    aave_pools: dict[int, ChecksumAddress],
    market_substrates: dict[int, list[bytes]],
) -> dict[int, list[AaveV3PositionBreakdown]] | None:
    """Fetch supply / variable / stable debt per Aave V3 asset substrate.

    The on-chain `getUserAccountData` aggregates all reserves into a single
    base-currency total — this helper exposes the per-asset decomposition.
    Each market is read from its own Pool (`aave_pools`); markets without one
    are skipped. Empty positions (vault allows the asset but holds none of it)
    are dropped.
    """
    per_market_assets = {
        mid: assets
        for mid, assets in _collect_aave_substrate_assets(market_substrates).items()
        if mid in aave_pools
    }
    if not per_market_assets:
        return None

    readers = {mid: AaveV3Reader(ctx, aave_pools[mid]) for mid in per_market_assets}
    futures: dict[int, list[Future]] = {
        mid: [
            pool.submit(
                _safe_call, partial(readers[mid].position_breakdown, asset, vault_addr)
            )
            for asset in assets
        ]
        for mid, assets in per_market_assets.items()
    }

    result: dict[int, list[AaveV3PositionBreakdown]] = {}
    for mid, per_market in futures.items():
        breakdowns = [
            bd
            for fut in per_market
            if (bd := fut.result()) is not None and not bd.is_empty
        ]
        if breakdowns:
            result[mid] = breakdowns
    return result or None


def _collect_breakdown_token_addresses(
    morpho_positions: dict[int, list[MorphoPositionBreakdown]] | None,
    aave_positions: dict[int, list[AaveV3PositionBreakdown]] | None,
) -> set[ChecksumAddress]:
    """Return the set of unique token addresses appearing in any breakdown.

    Used to batch-fetch USD prices via the price oracle in one parallel pass.
    """
    addrs: set[ChecksumAddress] = set()
    for morpho_list in (morpho_positions or {}).values():
        for pb in morpho_list:
            addrs.add(pb.loan_token)
            addrs.add(pb.collateral_token)
    for aave_list in (aave_positions or {}).values():
        for ab in aave_list:
            addrs.add(ab.asset)
    return addrs


def _fetch_breakdown_token_prices(
    ctx: Web3Context,
    oracle: PriceOracleMiddleware,
    addresses: set[ChecksumAddress],
) -> dict[str, float] | None:
    """Fetch USD prices for breakdown tokens in one batch via the vault's oracle.

    Returns a dict keyed by lowercase token address. Tokens with no oracle
    source configured are simply omitted (callers treat absence as "no price").
    """
    if not addresses:
        return None
    ordered = sorted(addresses)
    prices = Multicall3(ctx).try_aggregate(
        [oracle.get_asset_price(addr) for addr in ordered]
    )
    result = {
        addr.lower(): price.readable()
        for addr, price in zip(ordered, prices, strict=True)
        if price is not None
    }
    return result or None


def _fetch_block(ctx: Web3Context, block_number: int | None) -> tuple[int, int]:
    """``(block_number, timestamp)`` of the requested block, latest by default."""
    resolved = ctx.web3.eth.block_number if block_number is None else block_number
    return resolved, ctx.web3.eth.get_block(resolved)["timestamp"]


def _fetch_vault_reads(ctx: Web3Context, plasma_vault: PlasmaVault) -> dict[str, Any]:
    """Vault and underlying-asset state, as `_VaultData` fields.

    Two Multicall3 round trips: vault getters, then everything that needs the
    asset, the oracle or the fuse list (asset metadata, idle balance, price,
    each fuse's MARKET_ID()).
    """
    pv = plasma_vault
    (
        (
            share_decimals,
            total_assets,
            total_supply,
            supply_cap,
            asset,
            access_manager,
            price_oracle_addr,
            fuses,
            instant_fuses,
        ),
        (vault_name, rewards_manager),
    ) = _read_batch(
        ctx,
        [
            pv.decimals(),
            pv.total_assets(),
            pv.total_supply(),
            pv.get_total_supply_cap(),
            pv.underlying_asset_address(),
            pv.get_access_manager_address(),
            pv.get_price_oracle_middleware_address(),
            pv.get_fuses(),
            pv.get_instant_withdrawal_fuses(),
        ],
        [pv.name(), pv.get_rewards_claim_manager_address()],
    )

    asset_erc20 = ERC20(ctx, asset)
    oracle = PriceOracleMiddleware(ctx, price_oracle_addr)
    (asset_decimals, underlying_balance), (symbol, price, *fuse_market_ids) = (
        _read_batch(
            ctx,
            [
                asset_erc20.decimals(),
                asset_erc20.balance_of(Web3.to_checksum_address(pv.address)),
            ],
            [
                asset_erc20.symbol(),
                oracle.get_asset_price(asset),
                *(_fuse_market_id_call(ctx, fuse) for fuse in fuses),
            ],
        )
    )
    # Fuses without a readable MARKET_ID() are left out; the rest detect orphan
    # markets (action fuse registered, no balance fuse for its market).
    fuse_markets = {
        fuse: mid
        for fuse, mid in zip(fuses, fuse_market_ids, strict=True)
        if mid is not None
    }
    return {
        "share_decimals": share_decimals,
        "total_assets": total_assets,
        "total_supply": total_supply,
        "supply_cap": supply_cap,
        "asset": asset,
        "access_manager": access_manager,
        "price_oracle_addr": price_oracle_addr,
        "fuses": fuses,
        "instant_fuses": instant_fuses,
        "vault_name": vault_name or "",
        "rewards_manager": rewards_manager,
        "asset_decimals": asset_decimals,
        "underlying_balance_on_vault": underlying_balance,
        "asset_symbol": symbol or "?",
        "asset_price_usd": price.readable() if price else None,
        "fuse_markets": fuse_markets or None,
    }


def _fetch_role_accounts(
    ctx: Web3Context, plasma_vault: PlasmaVault
) -> list[RoleAccount] | None:
    """All confirmed role holders on the vault's AccessManager, sorted; None
    when the RoleGranted log scan fails (provider without broad eth_getLogs
    support, or a transport-level failure on the heavy query)."""
    try:
        manager = AccessManager(ctx, plasma_vault.get_access_manager_address().call())
        accounts = manager.get_all_role_accounts()
    except _ROLE_SCAN_ERRORS:
        return None
    return sorted(accounts, key=role_account_sort_key)


@dataclass
class _MarketReads:
    balance_fuses: list[BalanceFuse]
    dependency_graph: dict[int, list[int]]
    # Only markets with at least one substrate.
    market_substrates: dict[int, list[bytes]]
    aave_pools: dict[int, ChecksumAddress]


def _fetch_market_reads(
    ctx: Web3Context, plasma_vault: PlasmaVault, with_aave_pools: bool
) -> _MarketReads:
    """Balance fuses, then every market's dependency graph and substrates in
    one batch, then (optionally) the Aave V3 Pools behind the markets."""
    balance_fuses = plasma_vault.get_balance_fuses()
    market_ids = [bf.market_id for bf in balance_fuses]
    graph_and_substrates, _ = _read_batch(
        ctx,
        [
            *(plasma_vault.get_dependency_balance_graph(mid) for mid in market_ids),
            *(plasma_vault.get_market_substrates(mid) for mid in market_ids),
        ],
    )
    graphs = graph_and_substrates[: len(market_ids)]
    substrates = graph_and_substrates[len(market_ids) :]
    return _MarketReads(
        balance_fuses=balance_fuses,
        dependency_graph={
            mid: [int(dep) for dep in deps]
            for mid, deps in zip(market_ids, graphs, strict=True)
            if deps
        },
        market_substrates={
            mid: subs for mid, subs in zip(market_ids, substrates, strict=True) if subs
        },
        aave_pools=_fetch_aave_pools(ctx, balance_fuses) if with_aave_pools else {},
    )


@dataclass
class _LendingReads:
    lending_health: VaultLendingHealth | None = None
    morpho_positions: dict[int, list[MorphoPositionBreakdown]] | None = None
    aave_positions: dict[int, list[AaveV3PositionBreakdown]] | None = None


def _fetch_lending_reads(
    ctx: Web3Context,
    pool: ThreadPoolExecutor,
    vault_addr: ChecksumAddress,
    chain_id: int,
    markets: _MarketReads,
) -> _LendingReads:
    """Lending health (Morpho, Aave V3) plus the per-position breakdowns.

    Health runs as one pool task (it fans out on its own executor); the
    breakdowns fan out on `pool` from the calling thread, so no pool task ever
    waits on another.
    """
    f_health = pool.submit(
        _safe_call,
        partial(
            fetch_vault_lending_health,
            ctx,
            vault_addr,
            chain_id,
            [bf.market_id for bf in markets.balance_fuses],
            markets.market_substrates,
            aave_pools=markets.aave_pools,
        ),
    )
    morpho_positions = _fetch_morpho_positions(
        ctx, pool, vault_addr, markets.market_substrates
    )
    aave_positions = _fetch_aave_positions(
        ctx, pool, vault_addr, markets.aave_pools, markets.market_substrates
    )
    return _LendingReads(
        lending_health=f_health.result(),
        morpho_positions=morpho_positions,
        aave_positions=aave_positions,
    )


def _fetch_vault_data(
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    block_number: int | None,
    chain_id: int = 0,
) -> _VaultData:
    # Fail fast with a client-mappable typed error instead of letting the
    # fetch die deep in the stack (e.g. eth_getLogs range caps) on chains
    # the tooling is not validated on.
    ensure_supported_chain(chain_id or ctx.chain_id)
    vault_addr = Web3.to_checksum_address(plasma_vault.address)
    with ThreadPoolExecutor() as pool:
        # Independent pipelines, each a short chain of batched reads; none of
        # them waits on a pool future, so they cannot starve the pool.
        f_block = pool.submit(_fetch_block, ctx, block_number)
        f_vault = pool.submit(_fetch_vault_reads, ctx, plasma_vault)
        f_fees = pool.submit(_fetch_fee_data, ctx, plasma_vault)
        f_withdraw = pool.submit(_fetch_withdraw_manager, ctx, plasma_vault)
        f_roles = pool.submit(_fetch_role_accounts, ctx, plasma_vault)
        f_markets = pool.submit(
            _fetch_market_reads, ctx, plasma_vault, with_aave_pools=bool(chain_id)
        )

        markets = f_markets.result()
        lending = (
            _fetch_lending_reads(ctx, pool, vault_addr, chain_id, markets)
            if chain_id
            else _LendingReads()
        )
        vault_reads = f_vault.result()
        token_prices_usd = (
            _fetch_breakdown_token_prices(
                ctx,
                PriceOracleMiddleware(ctx, vault_reads["price_oracle_addr"]),
                _collect_breakdown_token_addresses(
                    lending.morpho_positions, lending.aave_positions
                ),
            )
            if chain_id
            else None
        )
        resolved_block, block_timestamp = f_block.result()
        withdraw_manager, withdraw_manager_data = f_withdraw.result()

        return _VaultData(
            **vault_reads,
            block_number=resolved_block,
            is_latest=block_number is None,
            block_timestamp=block_timestamp,
            withdraw_manager=withdraw_manager,
            withdraw_manager_data=withdraw_manager_data,
            fee_data=f_fees.result(),
            balance_fuses=markets.balance_fuses,
            dependency_graph=markets.dependency_graph or None,
            market_substrates=markets.market_substrates or None,
            lending_health=lending.lending_health,
            morpho_positions=lending.morpho_positions,
            aave_positions=lending.aave_positions,
            token_prices_usd=token_prices_usd,
            role_accounts=f_roles.result(),
        )


def _fetch_deployment_info(
    ctx: Web3Context,
    chain_id: int,
    vault_address: str,
    api_key: str | None,
) -> tuple[int | None, int | None, str | None]:
    """Return ``(block, timestamp, error)`` for the vault deployment, using cache.

    ``error`` is a short machine-readable code surfaced to the caller when the
    lookup fails for a known reason (e.g. ``etherscan-paid-tier-required`` for
    Base/Optimism on the free Etherscan tier). ``None`` means either success or
    a benign empty response.
    """
    cache_key = f"{chain_id}:{vault_address}"
    cache = load_deployment_cache()
    if entry := cache.get(cache_key):
        return entry["block"], entry["timestamp"], None

    tx_hash, error = get_deployment_tx(chain_id, vault_address, api_key)
    if not tx_hash:
        return None, None, error

    try:
        tx = ctx.web3.eth.get_transaction(HexStr(tx_hash))
        block_number: int = tx["blockNumber"]
        block_info = ctx.web3.eth.get_block(block_number)
        timestamp: int = block_info["timestamp"]
        update_deployment_cache(cache_key, block_number, timestamp)
        return block_number, timestamp, None
    except Exception:
        return None, None, "rpc-fetch-failed"

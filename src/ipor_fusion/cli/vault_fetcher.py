from __future__ import annotations

import logging
from collections.abc import Callable
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
from typing import Any, TypeVar

from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted, Web3RPCError
from web3.types import ChecksumAddress, HexStr

from ipor_fusion.cli.config_store import (
    load_contract_cache,
    load_deployment_cache,
    update_contract_cache,
    update_deployment_cache,
)
from ipor_fusion.cli.explorer import get_deployment_tx
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.oracle import PriceOracleMiddleware
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.core.withdraw_manager import AccountRequest, WithdrawManager
from ipor_fusion.readers.aave_v3 import AaveV3PositionBreakdown, AaveV3Reader
from ipor_fusion.readers.lending_health import (
    AAVE_V3_MARKET_IDS,
    AAVE_V3_POOL,
    MORPHO_BLUE_ADDRESS,
    MORPHO_MARKET_IDS,
    VaultLendingHealth,
    fetch_vault_lending_health,
)
from ipor_fusion.readers.morpho import MorphoPositionBreakdown, MorphoReader
from ipor_fusion.types import MorphoBlueMarketId


T = TypeVar("T")

_logger = logging.getLogger(__name__)

_NO_CONTRACT = "no contract"


@dataclass
class _WithdrawManagerData:
    """On-chain state snapshot from the WithdrawManager contract."""

    withdraw_window: int
    request_fee: int
    withdraw_fee: int
    shares_to_release: int
    last_release_funds_timestamp: int
    pending_requests: list[AccountRequest]


@dataclass
class _VaultData:  # pylint: disable=too-many-instance-attributes
    block_label: str
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
    vault_name: str = ""
    deployment_block: int | None = None
    deployment_timestamp: int | None = None
    withdraw_manager_data: _WithdrawManagerData | None = None
    dependency_graph: dict[int, list[int]] | None = None
    lending_health: VaultLendingHealth | None = None
    # Morpho per-substrate position breakdown (collateral / borrow / supply),
    # keyed by IPOR market_id. Populated only for markets in MORPHO_MARKET_IDS
    # whose substrates resolve to valid Morpho Blue market IDs.
    morpho_positions: dict[int, list[MorphoPositionBreakdown]] | None = None
    # Aave V3 per-asset position breakdown (supply / variable_debt / stable_debt),
    # keyed by IPOR market_id. Populated only for markets in AAVE_V3_MARKET_IDS
    # whose substrates decode to valid asset addresses on supported chains.
    # Empty positions are filtered out (a vault may allow an asset without using it).
    aave_positions: dict[int, list[AaveV3PositionBreakdown]] | None = None
    # USD price per token address (lowercase) for tokens appearing in lending
    # breakdowns (Morpho loan/collateral, Aave reserve assets). Sourced from the
    # vault's PriceOracleMiddleware. Missing keys mean the oracle has no source
    # configured for that token.
    token_prices_usd: dict[str, float] | None = None


def _safe_call(func: Callable[[], T]) -> T | None:
    try:
        return func()
    except (ContractLogicError, Web3RPCError, TimeExhausted) as exc:
        _logger.debug("_safe_call suppressed %s: %s", type(exc).__name__, exc)
        return None


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
        symbol = ERC20(ctx, checksum).symbol()
    except Exception:  # pylint: disable=broad-except
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
        decimals = ERC20(ctx, checksum).decimals()
    except Exception:  # pylint: disable=broad-except
        return None
    update_contract_cache(cache_key, str(decimals))
    return decimals


def _fetch_withdraw_manager_data(
    ctx: Web3Context,
    pool: ThreadPoolExecutor,
    withdraw_mgr_addr: ChecksumAddress | None,
) -> _WithdrawManagerData | None:
    if not withdraw_mgr_addr:
        return None
    wm_contract = WithdrawManager(ctx, withdraw_mgr_addr)
    f_window = pool.submit(_safe_call, wm_contract.get_withdraw_window)
    f_req_fee = pool.submit(_safe_call, wm_contract.get_request_fee)
    f_wd_fee = pool.submit(_safe_call, wm_contract.get_withdraw_fee)
    f_shares = pool.submit(_safe_call, wm_contract.get_shares_to_release)
    f_last_ts = pool.submit(_safe_call, wm_contract.get_last_release_funds_timestamp)
    f_requests = pool.submit(_safe_call, wm_contract.get_pending_requests)
    return _WithdrawManagerData(
        withdraw_window=f_window.result() or 0,
        request_fee=f_req_fee.result() or 0,
        withdraw_fee=f_wd_fee.result() or 0,
        shares_to_release=f_shares.result() or 0,
        last_release_funds_timestamp=f_last_ts.result() or 0,
        pending_requests=f_requests.result() or [],
    )


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

    reader = MorphoReader(ctx, MORPHO_BLUE_ADDRESS)
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


def _fetch_aave_positions(
    ctx: Web3Context,
    pool: ThreadPoolExecutor,
    vault_addr: ChecksumAddress,
    chain_id: int,
    market_substrates: dict[int, list[bytes]],
) -> dict[int, list[AaveV3PositionBreakdown]] | None:
    """Fetch supply / variable / stable debt per Aave V3 asset substrate.

    The on-chain `getUserAccountData` aggregates all reserves into a single
    base-currency total — this helper exposes the per-asset decomposition.
    Empty positions (vault allows the asset but holds none of it) are dropped.
    """
    aave_pool_addr = AAVE_V3_POOL.get(chain_id)
    if not aave_pool_addr:
        return None
    per_market_assets = _collect_aave_substrate_assets(market_substrates)
    if not per_market_assets:
        return None

    reader = AaveV3Reader(ctx, aave_pool_addr)
    futures: dict[int, list[Future]] = {
        mid: [
            pool.submit(
                _safe_call,
                lambda a=asset: reader.position_breakdown(a, vault_addr),
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
    pool: ThreadPoolExecutor,
    oracle: PriceOracleMiddleware,
    addresses: set[ChecksumAddress],
) -> dict[str, float] | None:
    """Fetch USD prices for breakdown tokens in parallel via the vault's oracle.

    Returns a dict keyed by lowercase token address. Tokens with no oracle
    source configured are simply omitted (callers treat absence as "no price").
    """
    if not addresses:
        return None
    futures = {
        addr: pool.submit(_safe_call, lambda a=addr: oracle.get_asset_price(a))
        for addr in addresses
    }
    prices: dict[str, float] = {}
    for addr, fut in futures.items():
        if (price := fut.result()) is not None:
            prices[addr.lower()] = price.readable()
    return prices or None


def _fetch_vault_data(  # pylint: disable=too-many-locals
    ctx: Web3Context,
    plasma_vault: PlasmaVault,
    block_number: int | None,
    chain_id: int = 0,
) -> _VaultData:
    with ThreadPoolExecutor() as pool:
        # Phase 1: all independent vault reads in parallel
        f_block = pool.submit(lambda: ctx.web3.eth.block_number)
        f_name: Future = pool.submit(_safe_call, plasma_vault.name)
        f_decimals = pool.submit(plasma_vault.decimals)
        f_total_assets = pool.submit(plasma_vault.total_assets)
        f_total_supply = pool.submit(plasma_vault.total_supply)
        f_supply_cap = pool.submit(plasma_vault.get_total_supply_cap)
        f_asset = pool.submit(plasma_vault.underlying_asset_address)
        f_access = pool.submit(plasma_vault.get_access_manager_address)
        f_oracle = pool.submit(plasma_vault.get_price_oracle_middleware_address)
        f_fuses = pool.submit(plasma_vault.get_fuses)
        f_balance_fuses = pool.submit(plasma_vault.get_balance_fuses)
        f_rewards: Future = pool.submit(
            _safe_call, plasma_vault.get_rewards_claim_manager_address
        )
        f_withdraw = pool.submit(plasma_vault.withdraw_manager_address)
        f_instant = pool.submit(plasma_vault.get_instant_withdrawal_fuses)

        # Phase 2: asset-dependent (wait for asset + oracle addresses)
        asset = f_asset.result()
        price_oracle_addr = f_oracle.result()
        asset_erc20 = ERC20(ctx, asset)
        oracle = PriceOracleMiddleware(ctx, price_oracle_addr)

        f_symbol: Future = pool.submit(_safe_call, asset_erc20.symbol)
        f_adec = pool.submit(asset_erc20.decimals)
        f_price: Future = pool.submit(_safe_call, lambda: oracle.get_asset_price(asset))

        # Collect all results
        latest_block = f_block.result()
        block_label = (
            str(block_number)
            if block_number is not None
            else f"{latest_block} (latest)"
        )
        block_timestamp: int = ctx.web3.eth.get_block(
            block_number if block_number is not None else latest_block
        )["timestamp"]
        asset_price = f_price.result()
        withdraw_mgr_addr = f_withdraw.result()

        # Phase 3: withdraw manager details (needs address from phase 1)
        wm_data = _fetch_withdraw_manager_data(ctx, pool, withdraw_mgr_addr)

        # Phase 4: dependency balance graph per market
        balance_fuses = f_balance_fuses.result()
        dep_futs = {
            bf.market_id: pool.submit(
                plasma_vault.get_dependency_balance_graph, bf.market_id
            )
            for bf in balance_fuses
        }
        dep_graph: dict[int, list[int]] = {}
        for market_id, fut in dep_futs.items():
            deps = fut.result()
            if deps:
                dep_graph[market_id] = [int(d) for d in deps]

        # Phase 5: lending health (Morpho, Aave V3)
        lending_health: VaultLendingHealth | None = None
        if chain_id:
            sub_futs: dict[int, Any] = {
                bf.market_id: pool.submit(
                    plasma_vault.get_market_substrates, bf.market_id
                )
                for bf in balance_fuses
            }
            market_substrates: dict[int, list[bytes]] = {}
            for mid, fut in sub_futs.items():
                subs = fut.result()
                if subs:
                    market_substrates[mid] = subs

            vault_addr = Web3.to_checksum_address(plasma_vault.address)
            lending_health = _safe_call(
                lambda: fetch_vault_lending_health(
                    ctx,
                    vault_addr,
                    chain_id,
                    [bf.market_id for bf in balance_fuses],
                    market_substrates,
                )
            )

            morpho_positions = _fetch_morpho_positions(
                ctx, pool, vault_addr, market_substrates
            )
            aave_positions = _fetch_aave_positions(
                ctx, pool, vault_addr, chain_id, market_substrates
            )
            token_prices_usd = _fetch_breakdown_token_prices(
                pool,
                oracle,
                _collect_breakdown_token_addresses(morpho_positions, aave_positions),
            )
        else:
            morpho_positions = None
            aave_positions = None
            token_prices_usd = None

        return _VaultData(
            block_label=block_label,
            block_timestamp=block_timestamp,
            share_decimals=f_decimals.result(),
            asset_decimals=f_adec.result(),
            total_assets=f_total_assets.result(),
            total_supply=f_total_supply.result(),
            supply_cap=f_supply_cap.result(),
            asset=asset,
            vault_name=f_name.result() or "",
            asset_symbol=f_symbol.result() or "?",
            access_manager=f_access.result(),
            price_oracle_addr=price_oracle_addr,
            rewards_manager=f_rewards.result(),
            withdraw_manager=withdraw_mgr_addr,
            asset_price_usd=asset_price.readable() if asset_price else None,
            fuses=f_fuses.result(),
            balance_fuses=balance_fuses,
            instant_fuses=f_instant.result(),
            withdraw_manager_data=wm_data,
            dependency_graph=dep_graph or None,
            lending_health=lending_health,
            morpho_positions=morpho_positions,
            aave_positions=aave_positions,
            token_prices_usd=token_prices_usd,
        )


def _fetch_deployment_info(
    ctx: Web3Context,
    chain_id: int,
    vault_address: str,
    api_key: str | None,
) -> tuple[int | None, int | None]:
    """Return (block, timestamp) for the vault deployment, using cache."""
    cache_key = f"{chain_id}:{vault_address}"
    cache = load_deployment_cache()
    if entry := cache.get(cache_key):
        return entry["block"], entry["timestamp"]

    if not (tx_hash := get_deployment_tx(chain_id, vault_address, api_key)):
        return None, None

    try:
        tx = ctx.web3.eth.get_transaction(HexStr(tx_hash))
        block_number: int = tx["blockNumber"]
        block_info = ctx.web3.eth.get_block(block_number)
        timestamp: int = block_info["timestamp"]
        update_deployment_cache(cache_key, block_number, timestamp)
        return block_number, timestamp
    except Exception:  # pylint: disable=broad-except
        return None, None

"""Per-substrate lending position breakdowns behind the netted balance-fuse
values (Morpho Blue, Aave V3) and the USD prices of the tokens in them."""

from __future__ import annotations

from concurrent.futures import Future, ThreadPoolExecutor
from functools import partial

from eth_typing import HexAddress
from web3 import Web3
from web3.types import ChecksumAddress

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.core.oracle import PriceOracleMiddleware
from ipor_fusion.core.plasma_vault import BalanceFuse
from ipor_fusion.readers.aave_v3 import (
    AaveV3FuseReader,
    AaveV3PoolAddressesProvider,
    AaveV3PositionBreakdown,
    AaveV3Reader,
)
from ipor_fusion.readers.lending_health import AAVE_V3_MARKET_IDS, MORPHO_MARKET_IDS
from ipor_fusion.readers.morpho import (
    MorphoPositionBreakdown,
    MorphoReader,
    morpho_blue_address,
)
from ipor_fusion.types import (
    MarketId,
    MorphoBlueMarketId,
    parse_hex_address,
    parse_morpho_market_id,
)
from ipor_fusion.vault_reads import _safe_call


def _collect_morpho_substrates(
    market_substrates: dict[MarketId, list[bytes]],
) -> dict[MarketId, list[MorphoBlueMarketId]]:
    """Filter + parse Morpho Blue market IDs from raw substrate bytes."""
    result: dict[MarketId, list[MorphoBlueMarketId]] = {}
    for mid, subs in market_substrates.items():
        if mid not in MORPHO_MARKET_IDS:
            continue
        morpho_ids = [parse_morpho_market_id(sub) for sub in subs if len(sub) == 32]
        if morpho_ids:
            result[mid] = morpho_ids
    return result


def _fetch_morpho_positions(
    ctx: Web3Context,
    pool: ThreadPoolExecutor,
    vault_addr: ChecksumAddress,
    market_substrates: dict[MarketId, list[bytes]],
    unread: list[str],
) -> dict[MarketId, list[MorphoPositionBreakdown]] | None:
    """Fetch collateral / borrow / supply breakdown for every Morpho substrate;
    a substrate whose read fails is left out and named in ``unread``.

    Each IPOR Morpho market (MORPHO_MARKET_IDS) can hold multiple morpho
    market_id substrates; each substrate has an independent position. The
    on-chain balance fuse reports a single netted number per IPOR market —
    this helper exposes the three-way decomposition behind that number.
    """
    per_market_substrates = _collect_morpho_substrates(market_substrates)
    if not per_market_substrates:
        return None

    reader = MorphoReader(morpho_blue_address(ctx.chain_id))
    futures: dict[MarketId, list[Future[MorphoPositionBreakdown | None]]] = {
        mid: [
            pool.submit(
                _safe_call,
                lambda mid_hex=morpho_mid: reader.read_position_breakdown(
                    ctx, mid_hex, vault_addr
                ),
            )
            for morpho_mid in morpho_mids
        ]
        for mid, morpho_mids in per_market_substrates.items()
    }

    result: dict[MarketId, list[MorphoPositionBreakdown]] = {}
    for mid, per_market in futures.items():
        reads = [fut.result() for fut in per_market]
        unread += [
            f"Morpho position in market {morpho_mid}"
            for morpho_mid, bd in zip(per_market_substrates[mid], reads, strict=True)
            if bd is None
        ]
        breakdowns = [bd for bd in reads if bd is not None]
        if breakdowns:
            result[mid] = breakdowns
    return result or None


def _collect_aave_substrate_assets(
    market_substrates: dict[MarketId, list[bytes]],
) -> dict[MarketId, list[ChecksumAddress]]:
    """Filter + parse asset addresses from Aave V3 substrate bytes.

    Aave substrates are zero-padded plain addresses (12 zero bytes + 20 address
    bytes). Returns a per-market list of checksummed asset addresses.
    """
    result: dict[MarketId, list[ChecksumAddress]] = {}
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
) -> dict[MarketId, ChecksumAddress]:
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
        [AaveV3FuseReader(fuse).pool_addresses_provider() for fuse in fuses.values()]
    )
    resolved = {
        mid: provider
        for mid, provider in zip(fuses, providers, strict=True)
        if provider is not None
    }
    pools = multicall.try_aggregate(
        [AaveV3PoolAddressesProvider(p).get_pool() for p in resolved.values()]
    )
    return {
        mid: pool for mid, pool in zip(resolved, pools, strict=True) if pool is not None
    }


def _fetch_aave_positions(
    ctx: Web3Context,
    pool: ThreadPoolExecutor,
    vault_addr: ChecksumAddress,
    aave_pools: dict[MarketId, ChecksumAddress],
    market_substrates: dict[MarketId, list[bytes]],
) -> dict[MarketId, list[AaveV3PositionBreakdown]] | None:
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

    readers = {mid: AaveV3Reader(aave_pools[mid]) for mid in per_market_assets}
    futures: dict[MarketId, list[Future[AaveV3PositionBreakdown | None]]] = {
        mid: [
            pool.submit(
                _safe_call,
                partial(readers[mid].read_position_breakdown, ctx, asset, vault_addr),
            )
            for asset in assets
        ]
        for mid, assets in per_market_assets.items()
    }

    result: dict[MarketId, list[AaveV3PositionBreakdown]] = {}
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
    morpho_positions: dict[MarketId, list[MorphoPositionBreakdown]] | None,
    aave_positions: dict[MarketId, list[AaveV3PositionBreakdown]] | None,
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
) -> dict[HexAddress, float] | None:
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
        parse_hex_address(addr): price.readable()
        for addr, price in zip(ordered, prices, strict=True)
        if price is not None
    }
    return result or None

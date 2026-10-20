"""A Plasma Vault's on-chain state at one block, read in a few batched round
trips: `fetch_vault_data` returns a `VaultData` snapshot (vault and asset,
fees, WithdrawManager, markets, substrates, lending positions, role holders),
and `fetch_deployment_info` resolves the vault's deployment block.

Token symbols, decimals and deployment blocks are cached per chain in the
context's `ContractCache` (``ctx.cache``).
"""

from __future__ import annotations

import logging
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from functools import partial
from typing import Any, TypeVar

from eth_typing import HexAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import ChecksumAddress, Timestamp

from ipor_fusion.cache import CacheKey, cache_get, cache_key, cache_put
from ipor_fusion.chains import ensure_supported_chain
from ipor_fusion.core.access import AccessManager, RoleAccount, role_account_sort_key
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.core.oracle import PriceOracleMiddleware, price_oracle_middleware
from ipor_fusion.core.plasma_vault import BalanceFuse, PlasmaVault
from ipor_fusion.errors import (
    EmptyCallResultError,
    UnsupportedVaultVersionError,
)
from ipor_fusion.explorer import get_deployment_tx
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.readers.aave_v3 import (
    AaveV3PositionBreakdown,
)
from ipor_fusion.readers.hypercore import (
    HYPEREVM_CHAIN_ID,
    HyperCoreVaultState,
    read_hypercore_vault_state,
)
from ipor_fusion.readers.lending_health import (
    VaultLendingHealth,
    fetch_vault_lending_health,
)
from ipor_fusion.readers.morpho import (
    MorphoPositionBreakdown,
)
from ipor_fusion.readers.unpriceable import (
    MiddlewarePricedToken,
    fetch_unpriceable_priced_tokens,
)
from ipor_fusion.substrates import decode_substrate
from ipor_fusion.types import (
    AccessManagerAddress,
    Amount,
    ChainId,
    Decimals,
    FuseAddress,
    MarketId,
    PlasmaVaultAddress,
    Shares,
    parse_hex_address,
)
from ipor_fusion.vault_erc20 import (
    Erc20Holdings,
    _Asset,
    _erc20_holdings,
    _read_erc20_market,
)
from ipor_fusion.vault_managers import (
    FeeData as FeeData,
)
from ipor_fusion.vault_managers import (
    WithdrawManagerData as WithdrawManagerData,
)
from ipor_fusion.vault_managers import (
    _fetch_fee_data,
    _fetch_withdraw_manager,
)
from ipor_fusion.vault_positions import (
    _collect_breakdown_token_addresses,
    _fetch_aave_pools,
    _fetch_aave_positions,
    _fetch_breakdown_token_prices,
    _fetch_morpho_positions,
)
from ipor_fusion.vault_reads import (
    _ROLE_SCAN_ERRORS,
    _read_batch,
    _safe_call,
    _unless_unimplemented,
)

T = TypeVar("T")

# Vault getters newer than some deployed vaults; a vault that predates one is
# read in a degraded way and reports it in `VaultData.unimplemented_getters`.
GET_MARKET_SUBSTRATES = "getMarketSubstrates(uint256)"
GET_TOTAL_SUPPLY_CAP = "getTotalSupplyCap()"
GET_PRICE_ORACLE_MIDDLEWARE = "getPriceOracleMiddleware()"

_logger = logging.getLogger(__name__)

_NO_CONTRACT = "no contract"


@dataclass
class VaultData:
    """A Plasma Vault's state at one block, as `fetch_vault_data` reads it.

    Amounts are raw on-chain integers. Optional data that could not be read
    is ``None`` (or empty) rather than an error. Everything that depends on
    chain state at ``block_number`` is in here, so validation and the
    ``vault info`` document are computed from the snapshot alone.
    """

    vault: PlasmaVaultAddress
    chain_id: ChainId
    block_number: int
    is_latest: bool
    block_timestamp: Timestamp
    share_decimals: Decimals
    asset_decimals: Decimals
    total_assets: Amount
    total_supply: Shares
    supply_cap: Shares
    asset: ChecksumAddress
    asset_symbol: str
    access_manager: AccessManagerAddress
    price_oracle: ChecksumAddress
    rewards_manager: ChecksumAddress | None
    withdraw_manager: ChecksumAddress | None
    asset_price_usd: float | None
    fuses: list[FuseAddress]
    balance_fuses: list[BalanceFuse]
    instant_fuses: list[FuseAddress]
    # Underlying asset balance held directly on the vault (raw). Part of
    # totalAssets via ERC4626 base accounting; by design NOT tracked as an
    # ERC20_VAULT_BALANCE substrate, so it is read directly in
    # fetch_vault_data rather than derived from substrates.
    underlying_balance_on_vault: Amount = Amount(0)
    vault_name: str = ""
    deployment_block: int | None = None
    deployment_timestamp: Timestamp | None = None
    # Short error code from `fetch_deployment_info` when block/timestamp could
    # not be resolved. Surfaced in CLI output and the JSON `deployment` field
    # so users can distinguish "lookup not attempted/failed" from
    # "no deployment exists" (e.g. `etherscan-paid-tier-required` on Base).
    deployment_error: str | None = None
    withdraw_manager_data: WithdrawManagerData | None = None
    fee_data: FeeData | None = None
    dependency_graph: dict[MarketId, list[MarketId]] | None = None
    lending_health: VaultLendingHealth | None = None
    # Morpho per-substrate position breakdown (collateral / borrow / supply),
    # keyed by IPOR market_id. Populated only for markets in MORPHO_MARKET_IDS
    # whose substrates resolve to valid Morpho Blue market IDs.
    morpho_positions: dict[MarketId, list[MorphoPositionBreakdown]] | None = None
    # Aave V3 per-asset position breakdown (supply / variable_debt / stable_debt),
    # keyed by IPOR market_id. Populated only for markets in AAVE_V3_MARKET_IDS
    # whose balance fuse resolves to a Pool and whose substrates decode to
    # valid asset addresses. Empty positions are filtered out (a vault may
    # allow an asset without using it).
    aave_positions: dict[MarketId, list[AaveV3PositionBreakdown]] | None = None
    # USD price per token address (lowercase) for tokens appearing in lending
    # breakdowns (Morpho loan/collateral, Aave reserve assets). Sourced from the
    # vault's PriceOracleMiddleware. Missing keys mean the oracle has no source
    # configured for that token.
    token_prices_usd: dict[HexAddress, float] | None = None
    # Middleware-priced tokens of granted substrates the PriceOracleMiddleware
    # cannot price (see readers.unpriceable). None when the check did not run.
    unpriceable_priced_tokens: list[MiddlewarePricedToken] | None = None
    # MARKET_ID() reported by each registered action fuse. Keyed by checksummed
    # fuse address. Fuses that don't expose MARKET_ID() (or whose call reverts)
    # are absent from the dict — used to detect orphan markets (action fuses
    # with no matching balance fuse, which silently drop positions from
    # totalAssets).
    fuse_markets: dict[FuseAddress, MarketId] | None = None
    # Substrates registered for each balance-fuse market. Allows the fuse
    # tables to report substrate counts per fuse without duplicating reads.
    market_substrates: dict[MarketId, list[bytes]] | None = None
    # The HyperCore market's live state (NAV legs, pending action, perp
    # markets); only on HyperEVM and only for a vault with a market-55 balance fuse.
    hypercore: HyperCoreVaultState | None = None
    # Confirmed AccessManager role holders, sorted by role_account_sort_key.
    # None when the RoleGranted log scan failed (see _ROLE_SCAN_ERRORS).
    role_accounts: list[RoleAccount] | None = None
    # Getters the vault does not implement because it predates them (see
    # GET_MARKET_SUBSTRATES and friends); the data they serve is degraded.
    unimplemented_getters: list[str] = field(default_factory=list)
    # totalAssetsInMarket of every balance-fuse market, once per market, in
    # balance-fuse order.
    market_balances: dict[MarketId, Amount] = field(default_factory=dict)
    # The ERC20_VAULT_BALANCE tokens read directly (empty without that market).
    erc20: Erc20Holdings = field(default_factory=Erc20Holdings)
    # Symbols (lowercase address keys) of the substrate and breakdown tokens,
    # and decimals of the breakdown tokens; empty when fetched with
    # metadata=False (the ERC20 holdings' symbols are then "?"). "" / None:
    # not a token, or unreadable. Token metadata is cached per chain and
    # address (ctx.cache), so a cached value may postdate the block.
    token_symbols: dict[HexAddress, str] = field(default_factory=dict)
    token_decimals: dict[HexAddress, Decimals | None] = field(default_factory=dict)


_MARKET_ID_SELECTOR = function_signature_to_4byte_selector("MARKET_ID()")


def _fuse_market_id_call(fuse: ChecksumAddress) -> Call[int]:
    """The immutable MARKET_ID() every IPOR Fusion fuse exposes.

    Read with `try_aggregate`: a fuse that reverts or lacks the getter (a
    non-fuse address ever found in getFuses()) reads as ``None``.
    """
    return Call(
        to=fuse,
        data=_MARKET_ID_SELECTOR,
        output_types=["uint256"],
        decoder=int,
    )


_UNCAPPED = Shares(2**256 - 1)


def _missing_required_getter(
    plasma_vault: PlasmaVault, exc: EmptyCallResultError
) -> UnsupportedVaultVersionError:
    """A required vault read returned no data: the probe already proved a
    Plasma Vault lives there, so it predates the getter."""
    return UnsupportedVaultVersionError(
        f"Plasma Vault {plasma_vault.address} predates a getter the vault "
        f"tooling requires: {exc}"
    )


def resolve_token_symbol(ctx: Web3Context, address: HexAddress) -> str:
    """ERC-20 symbol on ``ctx``'s chain, cached in ``ctx.cache``;
    ``"no contract"`` when nothing is deployed at ``address`` (at the context's
    block) and ``""`` when the symbol cannot be read. Neither is cached: the
    address can be deployed later."""
    key = cache_key(ctx.chain_id, address, "symbol")
    if cached := cache_get(ctx.cache, [key]).get(key):
        return cached

    checksum = Web3.to_checksum_address(address)
    code = ctx.web3.eth.get_code(checksum, block_identifier=ctx.default_block)
    if not code:
        return _NO_CONTRACT

    try:
        symbol = ERC20(checksum).symbol().call(ctx)
    except Exception:
        symbol = ""
    if symbol:
        cache_put(ctx.cache, {key: symbol})
    return symbol


def _cached_int(cached: dict[CacheKey, str], key: CacheKey) -> int | None:
    """A cached non-negative integer; anything else (a corrupt or foreign
    entry) is a miss."""
    value = cached.get(key, "")
    if not (value.isascii() and value.isdigit()):
        return None
    try:
        return int(value)
    except ValueError:  # beyond the int-string conversion limit
        return None


def resolve_token_decimals(ctx: Web3Context, address: HexAddress) -> Decimals | None:
    """ERC-20 decimals on ``ctx``'s chain, cached in ``ctx.cache``; ``None``
    (not cached) when ``address`` has no code at the context's block or does
    not answer."""
    key = cache_key(ctx.chain_id, address, "decimals")
    if (cached := _cached_int(cache_get(ctx.cache, [key]), key)) is not None:
        return Decimals(cached)

    checksum = Web3.to_checksum_address(address)
    code = ctx.web3.eth.get_code(checksum, block_identifier=ctx.default_block)
    if not code:
        return None

    try:
        decimals = ERC20(checksum).decimals().call(ctx)
    except Exception:
        return None
    cache_put(ctx.cache, {key: str(decimals)})
    return decimals


def _block_timestamp(ctx: Web3Context, block: int) -> Timestamp:
    return ctx.web3.eth.get_block(block)["timestamp"]


def _resolve_price_oracle(
    ctx: Web3Context,
    pv: PlasmaVault,
    middleware: ChecksumAddress | None,
    legacy: ChecksumAddress | None,
    unimplemented: list[str],
) -> ChecksumAddress:
    """The vault's price oracle from its batched reads. Vaults deployed before
    the August 2024 audit name the getter getPriceOracle(); without either
    getter the vault is too old for the tooling."""
    address = _unless_unimplemented(
        ctx, middleware, pv.get_price_oracle_middleware_address()
    )
    if address is not None:
        return address
    address = _unless_unimplemented(ctx, legacy, pv.get_price_oracle_address())
    if address is None:
        raise UnsupportedVaultVersionError(
            f"Plasma Vault {pv.address} implements neither "
            f"{GET_PRICE_ORACLE_MIDDLEWARE} nor getPriceOracle()"
        )
    unimplemented.append(GET_PRICE_ORACLE_MIDDLEWARE)
    return address


def _fetch_vault_reads(
    ctx: Web3Context, plasma_vault: PlasmaVault
) -> tuple[dict[str, Any], PriceOracleMiddleware]:
    """Vault and underlying-asset state, as `VaultData` fields, and the price
    oracle wrapper resolved for them (reused, so its ABI is probed once).

    Two Multicall3 round trips: vault getters, then everything that needs the
    asset, the oracle or the fuse list (asset metadata, idle balance, price,
    each fuse's MARKET_ID()).
    """
    pv = plasma_vault
    try:
        (
            (
                share_decimals,
                total_assets,
                total_supply,
                asset,
                access_manager,
                fuses,
                instant_fuses,
            ),
            (vault_name, rewards_manager, supply_cap, middleware_oracle, legacy_oracle),
        ) = _read_batch(
            ctx,
            [
                pv.decimals(),
                pv.total_assets(),
                pv.total_supply(),
                pv.underlying_asset_address(),
                pv.get_access_manager_address(),
                pv.get_fuses(),
                pv.get_instant_withdrawal_fuses(),
            ],
            [
                pv.name(),
                pv.get_rewards_claim_manager_address(),
                pv.get_total_supply_cap(),
                pv.get_price_oracle_middleware_address(),
                pv.get_price_oracle_address(),
            ],
        )
    except EmptyCallResultError as exc:
        raise _missing_required_getter(pv, exc) from exc
    unimplemented: list[str] = []
    # Vaults deployed before the August 2024 audit have no supply cap, so
    # nothing caps them.
    supply_cap = _unless_unimplemented(ctx, supply_cap, pv.get_total_supply_cap())
    if supply_cap is None:
        unimplemented.append(GET_TOTAL_SUPPLY_CAP)
        supply_cap = _UNCAPPED
    price_oracle = _resolve_price_oracle(
        ctx, pv, middleware_oracle, legacy_oracle, unimplemented
    )

    asset_erc20 = ERC20(asset)
    oracle = price_oracle_middleware(ctx, price_oracle)
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
                *(_fuse_market_id_call(fuse) for fuse in fuses),
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
        "price_oracle": price_oracle,
        "fuses": fuses,
        "instant_fuses": instant_fuses,
        "vault_name": vault_name or "",
        "rewards_manager": rewards_manager,
        "asset_decimals": asset_decimals,
        "underlying_balance_on_vault": underlying_balance,
        "asset_symbol": symbol or "?",
        "asset_price_usd": price.readable() if price else None,
        "fuse_markets": fuse_markets or None,
        "unimplemented_getters": unimplemented,
    }, oracle


def _fetch_role_accounts(
    ctx: Web3Context, plasma_vault: PlasmaVault
) -> list[RoleAccount] | None:
    """All confirmed role holders on the vault's AccessManager, sorted; None
    when the RoleGranted log scan cannot complete (the provider rejects even
    small eth_getLogs pages, or the scan runs out of time) or a transport-level
    failure hits the reads."""
    try:
        manager = AccessManager(plasma_vault.get_access_manager_address().call(ctx))
        accounts = manager.read_all_role_accounts(ctx)
    except _ROLE_SCAN_ERRORS:
        return None
    return sorted(accounts, key=role_account_sort_key)


@dataclass
class _MarketReads:
    balance_fuses: list[BalanceFuse]
    dependency_graph: dict[MarketId, list[MarketId]]
    # Only markets with at least one substrate.
    market_substrates: dict[MarketId, list[bytes]]
    aave_pools: dict[MarketId, ChecksumAddress]
    market_balances: dict[MarketId, Amount]
    # False when the vault predates getMarketSubstrates (substrates unknown).
    substrates_available: bool = True


def _fetch_market_reads(
    ctx: Web3Context, plasma_vault: PlasmaVault, *, with_aave_pools: bool
) -> _MarketReads:
    """Balance fuses, then every market's dependency graph and substrates in
    one batch, then (optionally) the Aave V3 Pools behind the markets."""
    balance_fuses = plasma_vault.read_balance_fuses(ctx)
    market_ids = [bf.market_id for bf in balance_fuses]
    substrate_calls = [plasma_vault.get_market_substrates(mid) for mid in market_ids]
    venues = list(dict.fromkeys(market_ids))
    try:
        required, batched_substrates = _read_batch(
            ctx,
            [
                *(plasma_vault.get_dependency_balance_graph(mid) for mid in market_ids),
                *(plasma_vault.total_assets_in_market(mid) for mid in venues),
            ],
            substrate_calls,
        )
    except EmptyCallResultError as exc:
        raise _missing_required_getter(plasma_vault, exc) from exc
    graphs, balances = required[: len(market_ids)], required[len(market_ids) :]
    substrates = [
        _unless_unimplemented(ctx, value, call)
        for value, call in zip(batched_substrates, substrate_calls, strict=True)
    ]
    return _MarketReads(
        balance_fuses=balance_fuses,
        dependency_graph={
            mid: [MarketId(dep) for dep in deps]
            for mid, deps in zip(market_ids, graphs, strict=True)
            if deps
        },
        market_substrates={
            mid: subs for mid, subs in zip(market_ids, substrates, strict=True) if subs
        },
        aave_pools=_fetch_aave_pools(ctx, balance_fuses) if with_aave_pools else {},
        market_balances=dict(zip(venues, balances, strict=True)),
        substrates_available=None not in substrates,
    )


@dataclass
class _LendingReads:
    lending_health: VaultLendingHealth | None = None
    morpho_positions: dict[MarketId, list[MorphoPositionBreakdown]] | None = None
    aave_positions: dict[MarketId, list[AaveV3PositionBreakdown]] | None = None


def _fetch_lending_reads(
    ctx: Web3Context,
    pool: ThreadPoolExecutor,
    vault_addr: PlasmaVaultAddress,
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
            balance_fuse_market_ids=[bf.market_id for bf in markets.balance_fuses],
            market_substrates=markets.market_substrates,
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


def fetch_vault_data(
    ctx: Web3Context,
    vault: PlasmaVaultAddress,
    *,
    block: int | None = None,
    lending: bool = True,
    metadata: bool = True,
) -> VaultData:
    """Read the vault's state at one block: ``block``, else the context's
    default block when that is a number, else the latest block, resolved once
    up front. Every read, log scans included, runs on a copy of ``ctx`` pinned
    to that block (`Web3Context.at_block`); ``ctx`` itself is not modified.
    The chain is ``ctx.chain_id``.

    ``lending=False`` skips lending health, the Morpho / Aave position
    breakdowns, their token prices and the unpriceable-token check.
    ``metadata=False`` skips the token symbols and decimals the ``vault info``
    document shows (validation does not need them) and reads no symbol at all:
    the ERC20 holdings are then named ``"?"``.

    Raises `UnsupportedChainError` on a chain the tooling is not validated on
    and `UnsupportedVaultVersionError` when the vault predates a getter the
    read cannot do without.
    """
    # Fail fast with a client-mappable typed error instead of letting the
    # fetch die deep in the stack on chains the tooling is not validated on.
    ensure_supported_chain(ctx.chain_id)
    if block is None and isinstance(ctx.default_block, int):
        block = ctx.default_block
    resolved_block = ctx.web3.eth.block_number if block is None else block
    is_latest = block is None
    ctx = ctx.at_block(resolved_block)
    plasma_vault = PlasmaVault(vault)
    with ThreadPoolExecutor() as pool:
        # Independent pipelines, each a short chain of batched reads; none of
        # them waits on a pool future, so they cannot starve the pool.
        f_timestamp = pool.submit(_block_timestamp, ctx, resolved_block)
        f_vault = pool.submit(_fetch_vault_reads, ctx, plasma_vault)
        f_fees = pool.submit(_fetch_fee_data, ctx, plasma_vault)
        f_withdraw = pool.submit(_fetch_withdraw_manager, ctx, plasma_vault)
        f_roles = pool.submit(_fetch_role_accounts, ctx, plasma_vault)
        f_markets = pool.submit(
            _fetch_market_reads, ctx, plasma_vault, with_aave_pools=lending
        )

        markets = f_markets.result()
        f_hypercore = pool.submit(_fetch_hypercore_state, ctx, vault, markets)
        lending_reads = (
            _fetch_lending_reads(ctx, pool, vault, markets)
            if lending
            else _LendingReads()
        )
        vault_reads, oracle = f_vault.result()
        derived = _derived_reads(
            ctx,
            pool,
            vault,
            vault_reads,
            oracle,
            markets,
            lending_reads if lending else None,
            metadata=metadata,
        )
        withdraw_manager, withdraw_manager_data = f_withdraw.result()
        if not markets.substrates_available:
            vault_reads["unimplemented_getters"].append(GET_MARKET_SUBSTRATES)

        return VaultData(
            **vault_reads,
            vault=vault,
            chain_id=ctx.chain_id,
            block_number=resolved_block,
            is_latest=is_latest,
            block_timestamp=f_timestamp.result(),
            withdraw_manager=withdraw_manager,
            withdraw_manager_data=withdraw_manager_data,
            fee_data=f_fees.result(),
            balance_fuses=markets.balance_fuses,
            dependency_graph=markets.dependency_graph or None,
            market_substrates=markets.market_substrates or None,
            lending_health=lending_reads.lending_health,
            morpho_positions=lending_reads.morpho_positions,
            aave_positions=lending_reads.aave_positions,
            role_accounts=f_roles.result(),
            hypercore=f_hypercore.result(),
            market_balances=markets.market_balances,
            **derived,
        )


def _derived_reads(
    ctx: Web3Context,
    pool: ThreadPoolExecutor,
    vault: PlasmaVaultAddress,
    vault_reads: dict[str, Any],
    oracle: PriceOracleMiddleware,
    markets: _MarketReads,
    lending: _LendingReads | None,
    *,
    metadata: bool,
) -> dict[str, Any]:
    """The `VaultData` fields read on top of the vault and market reads: the
    ERC20 holdings, and (with ``lending``) the breakdown token prices and the
    unpriceable-token check, and (with ``metadata``) the token metadata, which
    also names the ERC20 holdings."""
    f_erc20 = pool.submit(
        _read_erc20_market,
        ctx,
        vault,
        balance_fuses=markets.balance_fuses,
        market_substrates=markets.market_substrates,
        market_balances=markets.market_balances,
        oracle=oracle,
    )
    fields: dict[str, Any] = {}
    if lending is not None:
        fields["token_prices_usd"] = _fetch_breakdown_token_prices(
            ctx,
            oracle,
            _collect_breakdown_token_addresses(
                lending.morpho_positions, lending.aave_positions
            ),
        )
        fields["unpriceable_priced_tokens"] = _safe_call(
            partial(
                fetch_unpriceable_priced_tokens,
                ctx,
                price_oracle=vault_reads["price_oracle"],
                underlying=vault_reads["asset"],
                balance_fuses=markets.balance_fuses,
                market_substrates=markets.market_substrates,
                morpho_positions=lending.morpho_positions,
            )
        )
    if metadata:
        fields["token_symbols"], fields["token_decimals"] = _token_metadata(
            ctx, markets, lending or _LendingReads()
        )
    fields["erc20"] = _erc20_holdings(
        _Asset(
            address=vault_reads["asset"],
            decimals=vault_reads["asset_decimals"],
            price_usd=vault_reads["asset_price_usd"],
        ),
        f_erc20.result(),
        fields.get("token_symbols", {}),
    )
    return fields


def _token_metadata(
    ctx: Web3Context, markets: _MarketReads, lending: _LendingReads
) -> tuple[dict[HexAddress, str], dict[HexAddress, Decimals | None]]:
    """Symbols of every substrate and breakdown token, decimals of the
    breakdown tokens (the amounts the document formats), keyed lowercase.

    Cache hits first; the rest in one Multicall3 batch. A token that answers
    nothing (not a token, or not deployed at this block) reads as ``""`` /
    ``None`` and is not cached, so a later block can still resolve it."""
    breakdown = {
        parse_hex_address(address)
        for address in _collect_breakdown_token_addresses(
            lending.morpho_positions, lending.aave_positions
        )
    }
    substrate_addresses = {
        info.address
        for market_id, subs in markets.market_substrates.items()
        for sub in subs
        if (info := decode_substrate(sub, market_id=market_id)).address
    }
    chain_id = ctx.chain_id
    symbol_keys = {
        a: cache_key(chain_id, a, "symbol") for a in breakdown | substrate_addresses
    }
    decimals_keys = {a: cache_key(chain_id, a, "decimals") for a in breakdown}
    cached = cache_get(ctx.cache, [*symbol_keys.values(), *decimals_keys.values()])
    symbols: dict[HexAddress, str] = {
        a: cached[k] for a, k in symbol_keys.items() if cached.get(k)
    }
    decimals: dict[HexAddress, Decimals | None] = {
        a: Decimals(value)
        for a, k in decimals_keys.items()
        if (value := _cached_int(cached, k)) is not None
    }
    missing_symbols = sorted((breakdown | substrate_addresses) - symbols.keys())
    missing_decimals = sorted(breakdown - decimals.keys())
    if not missing_symbols and not missing_decimals:
        return symbols, decimals
    tokens = {
        a: ERC20(Web3.to_checksum_address(a)) for a in breakdown | substrate_addresses
    }
    calls: list[Call[Any]] = [tokens[a].symbol() for a in missing_symbols]
    calls += [tokens[a].decimals() for a in missing_decimals]
    results = Multicall3(ctx).try_aggregate(calls)
    found: dict[CacheKey, str] = {}
    for address, symbol in zip(missing_symbols, results, strict=False):
        symbols[address] = symbol or ""
        if symbol:
            found[symbol_keys[address]] = symbol
    for address, value in zip(
        missing_decimals, results[len(missing_symbols) :], strict=True
    ):
        decimals[address] = value
        if value is not None:
            found[decimals_keys[address]] = str(value)
    cache_put(ctx.cache, found)
    return symbols, decimals


def _fetch_hypercore_state(
    ctx: Web3Context,
    vault_addr: PlasmaVaultAddress,
    markets: _MarketReads,
) -> HyperCoreVaultState | None:
    """The HyperCore market's state, on HyperEVM only (the Core precompiles
    exist nowhere else) and only when the vault has a market-55 balance fuse.
    A failed read (a precompile, the oracle, a layout the valuation library
    would revert on) leaves the block out rather than failing `vault info`."""
    if ctx.chain_id != HYPEREVM_CHAIN_ID:
        return None
    fuse = next(
        (
            bf.fuse
            for bf in markets.balance_fuses
            if bf.market_id == IporFusionMarkets.HYPERCORE
        ),
        None,
    )
    if fuse is None:
        return None
    try:
        return _safe_call(partial(read_hypercore_vault_state, ctx, vault_addr, fuse))
    except (ValueError, EmptyCallResultError) as exc:
        _logger.debug("HyperCore state unavailable: %s", exc)
        return None


def fetch_deployment_info(
    ctx: Web3Context,
    vault: PlasmaVaultAddress,
    *,
    api_key: str | None = None,
) -> tuple[int | None, Timestamp | None, str | None]:
    """Return ``(block, timestamp, error)`` for the vault deployment, cached in
    ``ctx.cache``.

    ``error`` is a short machine-readable code surfaced to the caller when the
    lookup fails for a known reason (e.g. ``etherscan-paid-tier-required`` for
    Base/Optimism on the free Etherscan tier, ``rpc-fetch-failed`` when the
    creation transaction or its block cannot be read). ``None`` means either
    success or a benign empty response. The cache never causes an error: one
    that cannot be read or written is skipped.
    """
    chain_id = ctx.chain_id
    block_key = cache_key(chain_id, vault, "deployment_block")
    time_key = cache_key(chain_id, vault, "deployment_timestamp")
    cached = cache_get(ctx.cache, [block_key, time_key])
    block = _cached_int(cached, block_key)
    stamp = _cached_int(cached, time_key)
    if block is not None and stamp is not None:
        return block, Timestamp(stamp), None

    tx_hash, error = get_deployment_tx(chain_id, vault, api_key=api_key)
    if not tx_hash:
        return None, None, error

    try:
        tx = ctx.web3.eth.get_transaction(tx_hash)
        block_number: int = tx["blockNumber"]
        timestamp: Timestamp = ctx.web3.eth.get_block(block_number)["timestamp"]
    except Exception:
        return None, None, "rpc-fetch-failed"
    cache_put(ctx.cache, {block_key: str(block_number), time_key: str(timestamp)})
    return block_number, timestamp, None

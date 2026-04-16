"""Lending health computation for Morpho Blue and Aave V3 markets.

Provides a unified view of LTV, liquidation thresholds, and health factors
for lending positions held by Plasma Vaults.
"""

from __future__ import annotations

import logging
import math
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass

from eth_abi import decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.core.context import Web3Context
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.readers.aave_v3 import AaveV3Reader
from ipor_fusion.readers.morpho import MorphoReader
from ipor_fusion.types import MorphoBlueMarketId

_logger = logging.getLogger(__name__)

WAD = 10**18
ORACLE_PRICE_SCALE = 10**36

# Morpho Blue is deployed at the same address across all supported chains.
MORPHO_BLUE_ADDRESS: ChecksumAddress = Web3.to_checksum_address(
    "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
)

# Aave V3 Pool addresses per chain.
AAVE_V3_POOL: dict[int, ChecksumAddress] = {
    1: Web3.to_checksum_address(
        "0x87870Bca3F3fD6335C3F4ce8392D69350B4fA4E2"
    ),  # Ethereum
    42161: Web3.to_checksum_address(
        "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
    ),  # Arbitrum
    8453: Web3.to_checksum_address(
        "0xA238Dd80C259a72e81d7e4664a9801593F98d1c5"
    ),  # Base
    10: Web3.to_checksum_address(
        "0x794a61358D6845594F94dc1DB02A252b5b4814aD"
    ),  # Optimism
}

# Market IDs that represent Morpho Blue lending positions with collateral/debt.
# Flash loans (19), rewards (22), and liquidity-in-markets (41) are excluded:
# they share Morpho substrates but carry no persistent borrow risk.
MORPHO_MARKET_IDS = frozenset(
    {
        IporFusionMarkets.MORPHO,
    }
)

# Market IDs that represent Aave V3 lending positions.
AAVE_V3_MARKET_IDS = frozenset(
    {
        IporFusionMarkets.AAVE_V3,
        IporFusionMarkets.AAVE_V3_LIDO,
    }
)


@dataclass(slots=True)
class LendingMarketHealth:
    """Health status of a single lending market position.

    `substrate_id` identifies the underlying market within the IPOR market_id:
    - Morpho: the 64-char hex morpho market id (one row per substrate).
    - Aave V3: None — Aave health is account-level, aggregated across reserves.
    """

    protocol: str
    market_id: int
    market_name: str
    current_ltv: float | None
    max_ltv: float
    health_factor: float | None
    total_collateral_usd: float | None
    total_debt_usd: float | None
    ltv_usage_percent: float | None
    substrate_id: str | None = None

    @property
    def is_warning(self) -> bool:
        if self.health_factor is None:
            return False
        return self.health_factor < 1.1

    @property
    def is_critical(self) -> bool:
        if self.health_factor is None:
            return False
        return self.health_factor <= 1.05


@dataclass(slots=True)
class VaultLendingHealth:
    """Aggregated lending health for all lending markets in a vault."""

    markets: list[LendingMarketHealth]

    @property
    def has_lending_positions(self) -> bool:
        return len(self.markets) > 0

    @property
    def worst_ltv_usage(self) -> float | None:
        usages = [m.ltv_usage_percent for m in self.markets if m.ltv_usage_percent]
        return max(usages) if usages else None


def _call_morpho_oracle_price(ctx: Web3Context, oracle_address: ChecksumAddress) -> int:
    """Call IOracle(oracle).price() -> uint256."""
    selector = function_signature_to_4byte_selector("price()")
    raw = ctx.call(oracle_address, selector)
    (price,) = decode(["uint256"], raw)
    return price


def _shares_to_assets_up(shares: int, total_assets: int, total_shares: int) -> int:
    """Convert shares to assets, rounding up (Morpho convention)."""
    if total_shares == 0:
        return shares
    return math.ceil(shares * (total_assets + 1) / (total_shares + 1))


def _compute_morpho_market_health(  # pylint: disable=broad-exception-caught
    ctx: Web3Context,
    reader: MorphoReader,
    morpho_market_id: MorphoBlueMarketId,
    vault_address: ChecksumAddress,
    ipor_market_id: int,
    market_name: str,
) -> LendingMarketHealth | None:
    """Compute LTV health for a single Morpho Blue market position."""
    try:
        position = reader.position(morpho_market_id, vault_address)
    except Exception:
        _logger.debug("Failed to read Morpho position for %s", morpho_market_id)
        return None

    # No borrow = no liquidation risk
    if position.borrow_shares == 0:
        params = reader.market_params(morpho_market_id)
        return LendingMarketHealth(
            protocol="morpho",
            market_id=ipor_market_id,
            market_name=market_name,
            current_ltv=0.0,
            max_ltv=params.lltv / WAD,
            health_factor=None,
            total_collateral_usd=None,
            total_debt_usd=None,
            ltv_usage_percent=0.0,
            substrate_id=morpho_market_id,
        )

    try:
        market = reader.market(morpho_market_id)
        params = reader.market_params(morpho_market_id)
        oracle_price = _call_morpho_oracle_price(ctx, params.oracle)
    except Exception:
        _logger.debug("Failed to read Morpho market data for %s", morpho_market_id)
        return None

    borrowed = _shares_to_assets_up(
        position.borrow_shares, market.total_borrow_assets, market.total_borrow_shares
    )

    if position.collateral == 0 or oracle_price == 0:
        return LendingMarketHealth(
            protocol="morpho",
            market_id=ipor_market_id,
            market_name=market_name,
            current_ltv=None,
            max_ltv=params.lltv / WAD,
            health_factor=None,
            total_collateral_usd=None,
            total_debt_usd=None,
            ltv_usage_percent=None,
            substrate_id=morpho_market_id,
        )

    # collateral_value_in_loan = collateral * oracle_price / ORACLE_PRICE_SCALE
    # current_ltv = borrowed / collateral_value_in_loan
    #             = borrowed * ORACLE_PRICE_SCALE / (collateral * oracle_price)
    current_ltv_wad = (
        borrowed * ORACLE_PRICE_SCALE * WAD // (position.collateral * oracle_price)
    )
    current_ltv = current_ltv_wad / WAD
    max_ltv = params.lltv / WAD

    # health_factor = max_borrow / borrowed
    # max_borrow = collateral * oracle_price / ORACLE_PRICE_SCALE * lltv / WAD
    max_borrow = (
        position.collateral * oracle_price * params.lltv // (ORACLE_PRICE_SCALE * WAD)
    )
    health_factor = max_borrow / borrowed if borrowed > 0 else None

    ltv_usage = (current_ltv / max_ltv * 100) if max_ltv > 0 else None

    return LendingMarketHealth(
        protocol="morpho",
        market_id=ipor_market_id,
        market_name=market_name,
        current_ltv=round(current_ltv, 6),
        max_ltv=round(max_ltv, 6),
        health_factor=round(health_factor, 4) if health_factor else None,
        total_collateral_usd=None,
        total_debt_usd=None,
        ltv_usage_percent=round(ltv_usage, 2) if ltv_usage is not None else None,
        substrate_id=morpho_market_id,
    )


def _compute_aave_market_health(  # pylint: disable=broad-exception-caught
    reader: AaveV3Reader,
    vault_address: ChecksumAddress,
    ipor_market_id: int,
    market_name: str,
) -> LendingMarketHealth | None:
    """Compute LTV health for the vault's Aave V3 position."""
    try:
        data = reader.get_user_account_data(vault_address)
    except Exception:
        _logger.debug("Failed to read Aave V3 account data for %s", vault_address)
        return None

    # No debt = no risk
    if data.total_debt_base == 0:
        max_ltv = data.ltv / 10000 if data.ltv > 0 else 0
        return LendingMarketHealth(
            protocol="aave_v3",
            market_id=ipor_market_id,
            market_name=market_name,
            current_ltv=0.0,
            max_ltv=round(max_ltv, 6),
            health_factor=None,
            total_collateral_usd=round(data.total_collateral_base / 1e8, 2),
            total_debt_usd=0.0,
            ltv_usage_percent=0.0,
        )

    # Aave returns ltv and liquidation_threshold in basis points (1 = 0.01%)
    # health_factor is in 1e18 (WAD)
    current_ltv = (
        data.total_debt_base / data.total_collateral_base
        if data.total_collateral_base > 0
        else 0
    )
    liq_threshold = data.current_liquidation_threshold / 10000
    health_factor = data.health_factor / WAD

    ltv_usage = (current_ltv / liq_threshold * 100) if liq_threshold > 0 else None

    return LendingMarketHealth(
        protocol="aave_v3",
        market_id=ipor_market_id,
        market_name=market_name,
        current_ltv=round(current_ltv, 6),
        max_ltv=round(liq_threshold, 6),
        health_factor=round(health_factor, 4),
        total_collateral_usd=round(data.total_collateral_base / 1e8, 2),
        total_debt_usd=round(data.total_debt_base / 1e8, 2),
        ltv_usage_percent=round(ltv_usage, 2) if ltv_usage is not None else None,
    )


def fetch_vault_lending_health(  # pylint: disable=too-complex,import-outside-toplevel
    ctx: Web3Context,
    vault_address: ChecksumAddress,
    chain_id: int,
    balance_fuse_market_ids: list[int],
    market_substrates: dict[int, list[bytes]],
) -> VaultLendingHealth:
    """Fetch lending health for all lending markets in a vault.

    Args:
        ctx: Web3 context for on-chain calls.
        vault_address: The Plasma Vault address.
        chain_id: Chain ID (needed for Aave V3 pool address lookup).
        balance_fuse_market_ids: List of market IDs from balance fuses.
        market_substrates: Map of market_id -> list of raw substrate bytes.
    """
    from ipor_fusion.cli.vault_substrate import _market_name  # avoid circular

    morpho_markets: list[tuple[int, str, MorphoBlueMarketId]] = []
    aave_market_ids: list[tuple[int, str]] = []

    for mid in balance_fuse_market_ids:
        name = _market_name(mid)
        if mid in MORPHO_MARKET_IDS:
            substrates = market_substrates.get(mid, [])
            for sub in substrates:
                hex_str = sub.hex()
                if len(hex_str) == 64:
                    morpho_markets.append((mid, name, MorphoBlueMarketId(hex_str)))
        elif mid in AAVE_V3_MARKET_IDS:
            aave_market_ids.append((mid, name))

    results: list[LendingMarketHealth] = []

    with ThreadPoolExecutor() as pool:
        futures = []

        if morpho_markets:
            morpho_reader = MorphoReader(ctx, MORPHO_BLUE_ADDRESS)
            for ipor_mid, name, morpho_mid in morpho_markets:
                futures.append(
                    pool.submit(
                        _compute_morpho_market_health,
                        ctx,
                        morpho_reader,
                        morpho_mid,
                        vault_address,
                        ipor_mid,
                        name,
                    )
                )

        if aave_market_ids:
            aave_pool = AAVE_V3_POOL.get(chain_id)
            if aave_pool:
                aave_reader = AaveV3Reader(ctx, aave_pool)
                # Aave V3 returns aggregated data per user, not per asset.
                # Only fetch once, use for all Aave market IDs.
                first_mid, first_name = aave_market_ids[0]
                futures.append(
                    pool.submit(
                        _compute_aave_market_health,
                        aave_reader,
                        vault_address,
                        first_mid,
                        first_name,
                    )
                )
            else:
                _logger.debug(
                    "No Aave V3 pool address for chain %d, skipping", chain_id
                )

        for fut in futures:
            result = fut.result()
            if result is not None:
                results.append(result)

    return VaultLendingHealth(markets=results)

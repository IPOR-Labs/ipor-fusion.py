import math
from dataclasses import dataclass

from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import Timestamp

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.core.context import Web3Context
from ipor_fusion.types import Amount, Fee, MorphoBlueMarketId, Shares

WAD = 10**18
SECONDS_PER_YEAR = 365 * 24 * 60 * 60  # matches Morpho IRM YEAR constant


@dataclass(slots=True)
class MorphoMarket:
    """On-chain state of a Morpho Blue lending market."""

    total_supply_assets: Amount
    total_supply_shares: Shares
    total_borrow_assets: Amount
    total_borrow_shares: Shares
    last_update: Timestamp
    fee: Fee


@dataclass(slots=True)
class MorphoPosition:
    """User position within a Morpho Blue market."""

    supply_shares: Shares
    borrow_shares: Shares
    collateral: Amount


@dataclass(slots=True)
class MorphoMarketParams:
    """Configuration parameters defining a Morpho Blue market."""

    loan_token: ChecksumAddress
    collateral_token: ChecksumAddress
    oracle: ChecksumAddress
    irm: ChecksumAddress
    lltv: int


@dataclass(slots=True)
class MorphoMarketRates:
    """Continuously-compounded APYs for a Morpho Blue market.

    `utilization` is `totalBorrowAssets / totalSupplyAssets` (0.0 if supply is 0).
    `borrow_apy` is computed from the IRM's `borrowRateView` rate-per-second using
    continuous compounding (`exp(r * YEAR) - 1`), matching what the Morpho UI shows.
    `supply_apy` accounts for utilization and the protocol fee.
    `rate_per_second_wad` is the raw rate returned by the IRM, scaled by 1e18.
    """

    rate_per_second_wad: int
    utilization: float
    borrow_apy: float
    supply_apy: float


@dataclass(slots=True)
class MorphoPositionBreakdown:
    """User position in a Morpho Blue market, expressed in asset amounts.

    `collateral` is denominated in the market's collateral token; `borrow_assets`
    and `supply_assets` are denominated in the loan token. Shares are converted
    to assets using Morpho's rounding convention (round-up for borrow, round-down
    for supply — matches what the protocol credits on redeem).
    """

    market_id: MorphoBlueMarketId
    loan_token: ChecksumAddress
    collateral_token: ChecksumAddress
    collateral: Amount
    borrow_assets: Amount
    supply_assets: Amount


def _market_decoder(value: tuple) -> MorphoMarket:
    return MorphoMarket(*value)


def _position_decoder(value: tuple) -> MorphoPosition:
    return MorphoPosition(*value)


def _market_params_decoder(value: tuple) -> MorphoMarketParams:
    loan, collateral, oracle, irm, lltv = value
    return MorphoMarketParams(
        loan_token=Web3.to_checksum_address(loan),
        collateral_token=Web3.to_checksum_address(collateral),
        oracle=Web3.to_checksum_address(oracle),
        irm=Web3.to_checksum_address(irm),
        lltv=lltv,
    )


class MorphoReader(ContractWrapper):
    """Reader for Morpho Blue protocol on-chain state."""

    def market(self, market_id: MorphoBlueMarketId) -> Call[MorphoMarket]:
        return self._view(
            "market(bytes32)",
            bytes.fromhex(market_id.removeprefix("0x")),
            output_types=[
                "uint128",
                "uint128",
                "uint128",
                "uint128",
                "uint128",
                "uint128",
            ],
            decoder=_market_decoder,
        )

    def position(
        self, market_id: MorphoBlueMarketId, user: ChecksumAddress
    ) -> Call[MorphoPosition]:
        return self._view(
            "position(bytes32,address)",
            bytes.fromhex(market_id.removeprefix("0x")),
            user,
            output_types=["uint256", "uint128", "uint128"],
            decoder=_position_decoder,
        )

    def position_breakdown(
        self, market_id: MorphoBlueMarketId, user: ChecksumAddress
    ) -> MorphoPositionBreakdown:
        """Return the user's position with shares converted to asset amounts.

        Combines `position()`, `market()`, and `market_params()` reads.
        """
        pos = self.position(market_id, user).call()
        market = self.market(market_id).call()
        params = self.market_params(market_id).call()
        if market.total_borrow_shares > 0:
            borrow_assets = math.ceil(
                pos.borrow_shares
                * (market.total_borrow_assets + 1)
                / (market.total_borrow_shares + 1)
            )
        else:
            borrow_assets = 0
        if market.total_supply_shares > 0:
            supply_assets = (
                pos.supply_shares
                * market.total_supply_assets
                // market.total_supply_shares
            )
        else:
            supply_assets = 0
        return MorphoPositionBreakdown(
            market_id=market_id,
            loan_token=params.loan_token,
            collateral_token=params.collateral_token,
            collateral=pos.collateral,
            borrow_assets=Amount(borrow_assets),
            supply_assets=Amount(supply_assets),
        )

    def market_params(self, market_id: MorphoBlueMarketId) -> Call[MorphoMarketParams]:
        return self._view(
            "idToMarketParams(bytes32)",
            bytes.fromhex(market_id.removeprefix("0x")),
            output_types=["address", "address", "address", "address", "uint256"],
            decoder=_market_params_decoder,
        )

    def rates(self, market_id: MorphoBlueMarketId) -> MorphoMarketRates:
        """Read the IRM and derive supply/borrow APYs for the market.

        Calls `borrowRateView(marketParams, market)` on the market's IRM contract.
        APY is continuously compounded using `SECONDS_PER_YEAR = 365 * 86400`,
        matching how the Morpho frontend displays rates.
        """
        market = self.market(market_id).call()
        params = self.market_params(market_id).call()
        return self.rates_from(market, params)

    def rates_from(
        self, market: MorphoMarket, params: MorphoMarketParams
    ) -> MorphoMarketRates:
        """Compute APYs given pre-fetched market state and params.

        Use this when the caller has already read `market()` and `market_params()`
        to avoid two redundant RPC roundtrips.
        """
        rate_wad = _irm_borrow_rate_view(self._ctx, params, market)
        rate = rate_wad / WAD
        borrow_apy = math.expm1(rate * SECONDS_PER_YEAR)
        if market.total_supply_assets > 0:
            utilization = market.total_borrow_assets / market.total_supply_assets
        else:
            utilization = 0.0
        fee_factor = 1.0 - market.fee / WAD
        supply_apy = borrow_apy * utilization * fee_factor
        return MorphoMarketRates(
            rate_per_second_wad=rate_wad,
            utilization=utilization,
            borrow_apy=borrow_apy,
            supply_apy=supply_apy,
        )


def _irm_borrow_rate_view(
    ctx: Web3Context, params: MorphoMarketParams, market: MorphoMarket
) -> int:
    """Call `borrowRateView((MarketParams),(Market))` on the IRM contract.

    Returns the rate per second, scaled by 1e18 (Morpho convention).
    """
    selector = function_signature_to_4byte_selector(
        "borrowRateView("
        "(address,address,address,address,uint256),"
        "(uint128,uint128,uint128,uint128,uint128,uint128)"
        ")"
    )
    payload = encode(
        [
            "(address,address,address,address,uint256)",
            "(uint128,uint128,uint128,uint128,uint128,uint128)",
        ],
        [
            (
                params.loan_token,
                params.collateral_token,
                params.oracle,
                params.irm,
                params.lltv,
            ),
            (
                market.total_supply_assets,
                market.total_supply_shares,
                market.total_borrow_assets,
                market.total_borrow_shares,
                market.last_update,
                market.fee,
            ),
        ],
    )
    raw = ctx.call(params.irm, selector + payload)
    (rate,) = decode(["uint256"], raw)
    return rate

import math
from dataclasses import dataclass

from eth_abi import decode
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import Timestamp

from ipor_fusion.core.contract import ContractWrapper
from ipor_fusion.types import Amount, Fee, MorphoBlueMarketId, Shares


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


class MorphoReader(ContractWrapper):
    """Reader for Morpho Blue protocol on-chain state."""

    def market(self, market_id: MorphoBlueMarketId) -> MorphoMarket:
        raw = self._call("market(bytes32)", bytes.fromhex(market_id.removeprefix("0x")))
        values = decode(
            ["uint128", "uint128", "uint128", "uint128", "uint128", "uint128"],
            raw,
        )
        return MorphoMarket(*values)

    def position(
        self, market_id: MorphoBlueMarketId, user: ChecksumAddress
    ) -> MorphoPosition:
        raw = self._call(
            "position(bytes32,address)",
            bytes.fromhex(market_id.removeprefix("0x")),
            user,
        )
        values = decode(["uint256", "uint128", "uint128"], raw)
        return MorphoPosition(*values)

    def position_breakdown(
        self, market_id: MorphoBlueMarketId, user: ChecksumAddress
    ) -> MorphoPositionBreakdown:
        """Return the user's position with shares converted to asset amounts.

        Combines `position()`, `market()`, and `market_params()` reads.
        """
        pos = self.position(market_id, user)
        market = self.market(market_id)
        params = self.market_params(market_id)
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

    def market_params(self, market_id: MorphoBlueMarketId) -> MorphoMarketParams:
        raw = self._call(
            "idToMarketParams(bytes32)", bytes.fromhex(market_id.removeprefix("0x"))
        )
        loan, collateral, oracle, irm, lltv = decode(
            ["address", "address", "address", "address", "uint256"], raw
        )
        return MorphoMarketParams(
            loan_token=Web3.to_checksum_address(loan),
            collateral_token=Web3.to_checksum_address(collateral),
            oracle=Web3.to_checksum_address(oracle),
            irm=Web3.to_checksum_address(irm),
            lltv=lltv,
        )

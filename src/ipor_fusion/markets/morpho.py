from eth_typing import ChecksumAddress

from ipor_fusion.errors import UnsupportedFuseError
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.fuses.morpho import (
    MorphoSupplyFuse,
    MorphoFlashLoanFuse,
    MorphoClaimFuse,
)
from ipor_fusion.markets.base import LendingProtocol
from ipor_fusion.types import MorphoBlueMarketId


class MorphoMarket(LendingProtocol):
    def __init__(
        self,
        supply_fuse: ChecksumAddress = None,
        flash_loan_fuse: ChecksumAddress = None,
        claim_fuse: ChecksumAddress = None,
    ):
        self._supply_fuse = MorphoSupplyFuse(supply_fuse) if supply_fuse else None
        self._flash_loan_fuse = (
            MorphoFlashLoanFuse(flash_loan_fuse) if flash_loan_fuse else None
        )
        self._claim_fuse = MorphoClaimFuse(claim_fuse) if claim_fuse else None

    def supply(
        self,
        market_id: MorphoBlueMarketId,
        amount: int,
        **kwargs,
    ) -> FuseAction:
        if not self._supply_fuse:
            raise UnsupportedFuseError("MorphoSupplyFuse")
        return self._supply_fuse.supply(market_id, amount)

    def withdraw(
        self,
        market_id: MorphoBlueMarketId,
        amount: int,
        **kwargs,
    ) -> FuseAction:
        if not self._supply_fuse:
            raise UnsupportedFuseError("MorphoSupplyFuse")
        return self._supply_fuse.withdraw(market_id, amount)

    def flash_loan(
        self, asset: ChecksumAddress, amount: int, actions: list[FuseAction]
    ) -> FuseAction:
        if not self._flash_loan_fuse:
            raise UnsupportedFuseError("MorphoFlashLoanFuse")
        return self._flash_loan_fuse.flash_loan(asset, amount, actions)

    def claim_rewards(
        self,
        universal_rewards_distributor: ChecksumAddress,
        rewards_token: ChecksumAddress,
        claimable: int,
        proof: list[str],
    ) -> FuseAction:
        if not self._claim_fuse:
            raise UnsupportedFuseError("MorphoClaimFuse")
        return self._claim_fuse.claim(
            universal_rewards_distributor, rewards_token, claimable, proof
        )

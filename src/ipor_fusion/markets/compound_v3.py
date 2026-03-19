from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.fuses.compound_v3 import CompoundV3SupplyFuse
from ipor_fusion.markets.base import LendingProtocol


class CompoundV3Market(LendingProtocol):
    def __init__(self, supply_fuse: ChecksumAddress):
        self._supply_fuse = CompoundV3SupplyFuse(supply_fuse)

    def supply(
        self,
        asset: ChecksumAddress = None,
        amount: int = 0,
        **kwargs,
    ) -> FuseAction:
        return self._supply_fuse.supply(asset, amount)

    def withdraw(
        self,
        asset: ChecksumAddress = None,
        amount: int = 0,
        **kwargs,
    ) -> FuseAction:
        return self._supply_fuse.withdraw(asset, amount)

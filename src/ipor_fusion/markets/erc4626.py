from typing import List

from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.fuses.erc4626 import Erc4626SupplyFuse
from ipor_fusion.markets.base import LendingProtocol


class Erc4626Market(LendingProtocol):
    def __init__(self, supply_fuse: ChecksumAddress):
        self._supply_fuse = Erc4626SupplyFuse(supply_fuse)

    def supply(
        self,
        vault: ChecksumAddress,
        amount: int,
        **kwargs,
    ) -> FuseAction:
        return self._supply_fuse.supply(vault, amount)

    def withdraw(
        self,
        vault: ChecksumAddress,
        amount: int,
        **kwargs,
    ) -> FuseAction:
        return self._supply_fuse.withdraw(vault, amount)

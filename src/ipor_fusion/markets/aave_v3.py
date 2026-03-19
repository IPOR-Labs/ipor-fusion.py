from typing import List

from eth_typing import ChecksumAddress

from ipor_fusion.errors import UnsupportedFuseError
from ipor_fusion.fuses.aave_v3 import AaveV3SupplyFuse, AaveV3BorrowFuse
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.markets.base import BorrowingProtocol


class AaveV3Market(BorrowingProtocol):
    def __init__(
        self,
        supply_fuse: ChecksumAddress,
        borrow_fuse: ChecksumAddress = None,
    ):
        self._supply_fuse = AaveV3SupplyFuse(supply_fuse)
        self._borrow_fuse = AaveV3BorrowFuse(borrow_fuse) if borrow_fuse else None

    def supply(
        self,
        asset: ChecksumAddress = None,
        amount: int = 0,
        e_mode: int = 0,
        **kwargs,
    ) -> FuseAction:
        return self._supply_fuse.supply(asset, amount, e_mode)

    def withdraw(
        self,
        asset: ChecksumAddress = None,
        amount: int = 0,
        **kwargs,
    ) -> FuseAction:
        return self._supply_fuse.withdraw(asset, amount)

    def borrow(self, asset: ChecksumAddress, amount: int, **kwargs) -> FuseAction:
        if not self._borrow_fuse:
            raise UnsupportedFuseError("AaveV3BorrowFuse")
        return self._borrow_fuse.borrow(asset, amount)

    def repay(self, asset: ChecksumAddress, amount: int, **kwargs) -> FuseAction:
        if not self._borrow_fuse:
            raise UnsupportedFuseError("AaveV3BorrowFuse")
        return self._borrow_fuse.repay(asset, amount)

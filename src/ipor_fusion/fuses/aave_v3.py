from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction


class AaveV3SupplyFuse(Fuse):
    def supply(
        self, asset: ChecksumAddress, amount: int, e_mode: int = 0
    ) -> FuseAction:
        return self._action_raw(
            "enter((address,uint256,uint256))",
            ["address", "uint256", "uint256"],
            [asset, amount, e_mode],
        )

    def withdraw(self, asset: ChecksumAddress, amount: int) -> FuseAction:
        return self._action_raw(
            "exit((address,uint256))", ["address", "uint256"], [asset, amount]
        )


class AaveV3BorrowFuse(Fuse):
    def borrow(self, asset: ChecksumAddress, amount: int) -> FuseAction:
        return self._action_raw(
            "enter((address,uint256))", ["address", "uint256"], [asset, amount]
        )

    def repay(self, asset: ChecksumAddress, amount: int) -> FuseAction:
        return self._action_raw(
            "exit((address,uint256))", ["address", "uint256"], [asset, amount]
        )

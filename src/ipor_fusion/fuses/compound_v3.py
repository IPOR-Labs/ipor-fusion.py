from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction


class CompoundV3SupplyFuse(Fuse):
    def supply(self, asset: ChecksumAddress, amount: int) -> FuseAction:
        return self._action_raw(
            "enter((address,uint256))", ["address", "uint256"], [asset, amount]
        )

    def withdraw(self, asset: ChecksumAddress, amount: int) -> FuseAction:
        return self._action_raw(
            "exit((address,uint256))", ["address", "uint256"], [asset, amount]
        )

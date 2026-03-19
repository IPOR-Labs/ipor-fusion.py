from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction


class Erc4626SupplyFuse(Fuse):
    def supply(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        return self._action_raw(
            "enter((address,uint256))", ["address", "uint256"], [vault_address, amount]
        )

    def withdraw(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        return self._action_raw(
            "exit((address,uint256))", ["address", "uint256"], [vault_address, amount]
        )

from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import MAX_UINT256


class GearboxSupplyFuse(Fuse):
    def supply(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        return self._action_raw(
            "enter((address,uint256))", ["address", "uint256"], [vault_address, amount]
        )

    def withdraw(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        return self._action_raw(
            "exit((address,uint256))", ["address", "uint256"], [vault_address, amount]
        )


class GearboxStakeFuse(Fuse):
    def __init__(
        self, farm_fuse_address: ChecksumAddress, farmd_token_address: ChecksumAddress
    ):
        super().__init__(farm_fuse_address)
        self._farmd_token_address = farmd_token_address

    def stake(self) -> FuseAction:
        return self._action_raw(
            "enter((uint256,address))",
            ["uint256", "address"],
            [MAX_UINT256, self._farmd_token_address],
        )

    def unstake(self, amount: int) -> FuseAction:
        return self._action_raw(
            "exit((uint256,address))",
            ["uint256", "address"],
            [amount, self._farmd_token_address],
        )

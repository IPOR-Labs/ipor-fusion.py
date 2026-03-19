from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import MAX_UINT256


class FluidInstadappSupplyFuse(Fuse):
    def supply(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        return self._action_raw(
            "enter((address,uint256))", ["address", "uint256"], [vault_address, amount]
        )

    def withdraw(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        return self._action_raw(
            "exit((address,uint256))", ["address", "uint256"], [vault_address, amount]
        )


class FluidInstadappStakingFuse(Fuse):
    def __init__(
        self,
        staking_fuse_address: ChecksumAddress,
        staking_contract_address: ChecksumAddress,
    ):
        super().__init__(staking_fuse_address)
        self._staking_contract_address = staking_contract_address

    def stake(self) -> FuseAction:
        return self._action_raw(
            "enter((uint256,address))",
            ["uint256", "address"],
            [MAX_UINT256, self._staking_contract_address],
        )

    def unstake(self, amount: int) -> FuseAction:
        return self._action_raw(
            "exit((uint256,address))",
            ["uint256", "address"],
            [amount, self._staking_contract_address],
        )

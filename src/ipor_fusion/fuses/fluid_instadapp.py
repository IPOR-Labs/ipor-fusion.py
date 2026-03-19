from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction, StakeFuse


class FluidInstadappSupplyFuse(Fuse):
    def supply(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        self._validate_address(vault_address, "vault_address")
        self._validate_amount(amount, "amount")
        return self._action_raw(
            "enter((address,uint256))", ["address", "uint256"], [vault_address, amount]
        )

    def withdraw(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        self._validate_address(vault_address, "vault_address")
        self._validate_amount(amount, "amount")
        return self._action_raw(
            "exit((address,uint256))", ["address", "uint256"], [vault_address, amount]
        )


class FluidInstadappStakingFuse(StakeFuse):
    def __init__(  # pylint: disable=useless-parent-delegation
        self,
        staking_fuse_address: ChecksumAddress,
        staking_contract_address: ChecksumAddress,
    ):
        super().__init__(staking_fuse_address, staking_contract_address)

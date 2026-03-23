from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction, StakeFuse
from ipor_fusion.types import Amount


class FluidInstadappSupplyFuse(Fuse):
    """Fuse for supplying and withdrawing assets on Fluid Instadapp vaults."""

    def supply(self, *, vault_address: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(vault_address, "vault_address")
        self._validate_amount(amount, "amount")
        return self._action_raw("enter((address,uint256))", [[vault_address, amount]])

    def withdraw(self, *, vault_address: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(vault_address, "vault_address")
        self._validate_amount(amount, "amount")
        return self._action_raw("exit((address,uint256))", [[vault_address, amount]])


class FluidInstadappStakingFuse(StakeFuse):
    """Fuse for staking and unstaking on Fluid Instadapp staking contracts."""

    def __init__(  # pylint: disable=useless-parent-delegation
        self,
        fuse_address: ChecksumAddress,
        staking_address: ChecksumAddress,
    ):
        super().__init__(fuse_address, staking_address)

from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction, StakeFuse
from ipor_fusion.types import Amount


class GearboxSupplyFuse(Fuse):
    def supply(self, vault_address: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(vault_address, "vault_address")
        self._validate_amount(amount, "amount")
        return self._action_raw("enter((address,uint256))", [[vault_address, amount]])

    def withdraw(self, vault_address: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(vault_address, "vault_address")
        self._validate_amount(amount, "amount")
        return self._action_raw("exit((address,uint256))", [[vault_address, amount]])


class GearboxStakeFuse(StakeFuse):
    def __init__(  # pylint: disable=useless-parent-delegation
        self, farm_fuse_address: ChecksumAddress, farmd_token_address: ChecksumAddress
    ):
        super().__init__(farm_fuse_address, farmd_token_address)

from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import Amount


class ERC4626SupplyFuse(Fuse):
    def supply(self, vault_address: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(vault_address, "vault_address")
        self._validate_amount(amount, "amount")
        return self._action_raw(
            "enter((address,uint256))", ["address", "uint256"], [vault_address, amount]
        )

    def withdraw(self, vault_address: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(vault_address, "vault_address")
        self._validate_amount(amount, "amount")
        return self._action_raw(
            "exit((address,uint256))", ["address", "uint256"], [vault_address, amount]
        )

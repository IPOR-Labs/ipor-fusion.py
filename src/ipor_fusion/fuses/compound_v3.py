from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import Amount


class CompoundV3SupplyFuse(Fuse):
    """Fuse for supplying and withdrawing assets on Compound V3."""

    def supply(self, *, asset: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(asset, "asset")
        self._validate_amount(amount, "amount")
        return self._action_raw("enter((address,uint256))", [[asset, amount]])

    def withdraw(self, *, asset: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(asset, "asset")
        self._validate_amount(amount, "amount")
        return self._action_raw("exit((address,uint256))", [[asset, amount]])

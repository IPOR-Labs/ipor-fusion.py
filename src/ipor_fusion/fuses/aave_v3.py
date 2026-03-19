from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import Amount


class AaveV3SupplyFuse(Fuse):
    """Fuse for supplying and withdrawing assets on Aave V3."""

    def supply(
        self, *, asset: ChecksumAddress, amount: Amount, e_mode: int = 0
    ) -> FuseAction:
        self._validate_address(asset, "asset")
        self._validate_amount(amount, "amount")
        return self._action_raw(
            "enter((address,uint256,uint256))",
            [[asset, amount, e_mode]],
        )

    def withdraw(self, *, asset: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(asset, "asset")
        self._validate_amount(amount, "amount")
        return self._action_raw("exit((address,uint256))", [[asset, amount]])


class AaveV3BorrowFuse(Fuse):
    """Fuse for borrowing and repaying assets on Aave V3."""

    def borrow(self, *, asset: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(asset, "asset")
        self._validate_amount(amount, "amount")
        return self._action_raw("enter((address,uint256))", [[asset, amount]])

    def repay(self, *, asset: ChecksumAddress, amount: Amount) -> FuseAction:
        self._validate_address(asset, "asset")
        self._validate_amount(amount, "amount")
        return self._action_raw("exit((address,uint256))", [[asset, amount]])

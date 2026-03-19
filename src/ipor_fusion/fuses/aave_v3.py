from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuses.base import Fuse, FuseAction


class AaveV3SupplyFuse(Fuse):
    def supply(
        self, asset: ChecksumAddress, amount: int, e_mode: int = 0
    ) -> FuseAction:
        data = encode(["address", "uint256", "uint256"], [asset, amount, e_mode])
        selector = function_signature_to_4byte_selector(
            "enter((address,uint256,uint256))"
        )
        return FuseAction(fuse=self._address, data=selector + data)

    def withdraw(self, asset: ChecksumAddress, amount: int) -> FuseAction:
        data = encode(["address", "uint256"], [asset, amount])
        selector = function_signature_to_4byte_selector("exit((address,uint256))")
        return FuseAction(fuse=self._address, data=selector + data)


class AaveV3BorrowFuse(Fuse):
    def borrow(self, asset: ChecksumAddress, amount: int) -> FuseAction:
        data = encode(["address", "uint256"], [asset, amount])
        selector = function_signature_to_4byte_selector("enter((address,uint256))")
        return FuseAction(fuse=self._address, data=selector + data)

    def repay(self, asset: ChecksumAddress, amount: int) -> FuseAction:
        data = encode(["address", "uint256"], [asset, amount])
        selector = function_signature_to_4byte_selector("exit((address,uint256))")
        return FuseAction(fuse=self._address, data=selector + data)

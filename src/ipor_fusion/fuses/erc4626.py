from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuses.base import Fuse, FuseAction


class Erc4626SupplyFuse(Fuse):
    def supply(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        data = encode(["address", "uint256"], [vault_address, amount])
        selector = function_signature_to_4byte_selector("enter((address,uint256))")
        return FuseAction(fuse=self._address, data=selector + data)

    def withdraw(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        data = encode(["address", "uint256"], [vault_address, amount])
        selector = function_signature_to_4byte_selector("exit((address,uint256))")
        return FuseAction(fuse=self._address, data=selector + data)

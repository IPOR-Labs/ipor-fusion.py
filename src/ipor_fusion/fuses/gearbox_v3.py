from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import MAX_UINT256


class GearboxSupplyFuse(Fuse):
    def supply(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        data = encode(["address", "uint256"], [vault_address, amount])
        selector = function_signature_to_4byte_selector("enter((address,uint256))")
        return FuseAction(fuse=self._address, data=selector + data)

    def withdraw(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        data = encode(["address", "uint256"], [vault_address, amount])
        selector = function_signature_to_4byte_selector("exit((address,uint256))")
        return FuseAction(fuse=self._address, data=selector + data)


class GearboxStakeFuse(Fuse):
    def __init__(
        self, farm_fuse_address: ChecksumAddress, farmd_token_address: ChecksumAddress
    ):
        super().__init__(farm_fuse_address)
        self._farmd_token_address = farmd_token_address

    def stake(self) -> FuseAction:
        data = encode(["uint256", "address"], [MAX_UINT256, self._farmd_token_address])
        selector = function_signature_to_4byte_selector("enter((uint256,address))")
        return FuseAction(fuse=self._address, data=selector + data)

    def unstake(self, amount: int) -> FuseAction:
        data = encode(["uint256", "address"], [amount, self._farmd_token_address])
        selector = function_signature_to_4byte_selector("exit((uint256,address))")
        return FuseAction(fuse=self._address, data=selector + data)

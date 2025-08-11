from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class Erc4626SupplyFuse:
    def __init__(
        self,
        fuse_address: ChecksumAddress,
        erc4626_address: ChecksumAddress,
    ):
        if not fuse_address:
            raise ValueError("fuseAddress is required")
        if not erc4626_address:
            raise ValueError("erc4626Address is required")
        self.fuse_address = fuse_address
        self.erc4626_address = erc4626_address

    def supply(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        enter_data = Erc4626SupplyFuseEnterData(vault_address, amount)
        return FuseAction(self.fuse_address, enter_data.function_call())

    def withdraw(self, vault_address: ChecksumAddress, amount: int) -> FuseAction:
        exit_data = Erc4626SupplyFuseExitData(vault_address, amount)
        return FuseAction(self.fuse_address, exit_data.function_call())


class Erc4626SupplyFuseEnterData:
    def __init__(self, address: str, amount: int):
        self.address = address
        self.amount = amount

    def encode(self) -> bytes:
        return encode(["address", "uint256"], [self.address, self.amount])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((address,uint256))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class Erc4626SupplyFuseExitData:
    def __init__(self, address: str, amount: int):
        self.address = address
        self.amount = amount

    def encode(self) -> bytes:
        return encode(["address", "uint256"], [self.address, self.amount])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("exit((address,uint256))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()

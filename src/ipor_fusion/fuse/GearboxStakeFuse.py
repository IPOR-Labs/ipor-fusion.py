from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class GearboxStakeFuse:
    MAX_UINT256 = (1 << 256) - 1

    def __init__(
        self, farmd_token_address: ChecksumAddress, farm_fuse_address: ChecksumAddress
    ):
        self.farmd_token_address = farmd_token_address
        self.farm_fuse_address = farm_fuse_address

    def stake(self) -> FuseAction:
        enter_data = GearboxV3FarmdSupplyFuseEnterData(
            self.MAX_UINT256, self.farmd_token_address
        )
        return FuseAction(self.farm_fuse_address, enter_data.function_call())

    def unstake(self, amount: int) -> FuseAction:
        exit_data = GearboxV3FarmdSupplyFuseExitData(amount, self.farmd_token_address)
        return FuseAction(self.farm_fuse_address, exit_data.function_call())


class GearboxV3FarmdSupplyFuseEnterData:
    def __init__(self, d_token_amount: int, farmd_token: str):
        self.d_token_amount = d_token_amount
        self.farmd_token = farmd_token

    def encode(self) -> bytes:
        return encode(["uint256", "address"], [self.d_token_amount, self.farmd_token])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((uint256,address))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class GearboxV3FarmdSupplyFuseExitData:
    def __init__(self, d_token_amount: int, farmd_token: str):
        self.d_token_amount = d_token_amount
        self.farmd_token = farmd_token

    def encode(self) -> bytes:
        return encode(["uint256", "address"], [self.d_token_amount, self.farmd_token])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("exit((uint256,address))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()

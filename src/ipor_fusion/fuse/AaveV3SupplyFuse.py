from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.MarketId import MarketId
from ipor_fusion.fuse.FuseAction import FuseAction


class AaveV3SupplyFuse:
    PROTOCOL_ID = "aave-v3"
    E_MODE_CATEGORY_ID = 300
    ENTER = "enter"
    EXIT = "exit"

    def __init__(self, fuse_address: ChecksumAddress):
        if fuse_address is None:
            raise ValueError("fuseAddress is required")
        self.fuse_address = fuse_address

    def supply(self, market_id: MarketId, amount: int, e_mode: int) -> FuseAction:
        aave_v3_supply_fuse_enter_data = AaveV3SupplyFuseEnterData(
            market_id.market_id, amount, e_mode
        )
        return FuseAction(
            self.fuse_address, aave_v3_supply_fuse_enter_data.function_call()
        )

    def withdraw(self, market_id: MarketId, amount: int) -> FuseAction:
        aave_v3_supply_fuse_exit_data = AaveV3SupplyFuseExitData(
            market_id.market_id, amount
        )
        return FuseAction(
            self.fuse_address, aave_v3_supply_fuse_exit_data.function_call()
        )


class AaveV3SupplyFuseEnterData:
    def __init__(self, asset: str, amount: int, user_e_mode_category_id: int):
        self.asset = asset
        self.amount = amount
        self.user_e_mode_category_id = user_e_mode_category_id

    def encode(self) -> bytes:
        return encode(
            ["address", "uint256", "uint256"],
            [self.asset, self.amount, self.user_e_mode_category_id],
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((address,uint256,uint256))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class AaveV3SupplyFuseExitData:
    def __init__(self, asset: str, amount: int):
        self.asset = asset
        self.amount = amount

    def encode(self) -> bytes:
        return encode(["address", "uint256"], [self.asset, self.amount])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("exit((address,uint256))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()

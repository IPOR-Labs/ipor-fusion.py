from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.MarketId import MarketId
from ipor_fusion.fuse.FuseAction import FuseAction


class MoonwellSupplyFuse:
    PROTOCOL_ID = "moonwell"

    def __init__(self, fuse_address: str, asset_address: str):
        if not fuse_address:
            raise ValueError("fuseAddress is required")
        self.fuse_address = fuse_address

    def supply(self, market_id: MarketId, amount: int) -> FuseAction:
        moonwell_supply_fuse_enter_data = MoonwellSupplyFuseEnterData(
            market_id.market_id, amount
        )
        return FuseAction(
            self.fuse_address, moonwell_supply_fuse_enter_data.function_call()
        )

    def withdraw(self, market_id: MarketId, amount: int) -> FuseAction:
        moonwell_supply_fuse_exit_data = MoonwellSupplyFuseExitData(
            market_id.market_id, amount
        )
        return FuseAction(
            self.fuse_address, moonwell_supply_fuse_exit_data.function_call()
        )


class MoonwellSupplyFuseEnterData:
    def __init__(self, asset: str, amount: int):
        self.asset = asset
        self.amount = amount

    def encode(self) -> bytes:
        # ABI encoding: address and uint256
        return encode(["address", "uint256"], [self.asset, self.amount])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((address,uint256))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class MoonwellSupplyFuseExitData:
    def __init__(self, asset: str, amount: int):
        self.asset = asset
        self.amount = amount

    def encode(self) -> bytes:
        # ABI encoding: address and uint256
        return encode(["address", "uint256"], [self.asset, self.amount])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("exit((address,uint256))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()
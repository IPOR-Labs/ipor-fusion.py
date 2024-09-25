from typing import List

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion_sdk.MarketId import MarketId
from ipor_fusion_sdk.fuse.Fuse import Fuse
from ipor_fusion_sdk.fuse.FuseActionDynamicStruct import FuseActionDynamicStruct


class CompoundV3SupplyFuse(Fuse):
    PROTOCOL_ID = "compound-v3"
    ENTER = "enter"
    EXIT = "exit"

    def __init__(self, fuse_address: str, asset_address: str):
        if not fuse_address:
            raise ValueError("fuseAddress is required")
        if not asset_address:
            raise ValueError("assetAddress is required")
        self.fuse_address = fuse_address
        self.asset_address = asset_address

    def supports(self, market_id: MarketId) -> bool:
        if market_id is None:
            raise ValueError("marketId is required")
        return (
            market_id.protocol_id == self.PROTOCOL_ID
            and market_id.market_id == self.asset_address
        )

    def create_fuse_enter_action(
        self, market_id: MarketId, amount: int
    ) -> List[FuseActionDynamicStruct]:
        compound_v3_supply_fuse_enter_data = CompoundV3SupplyFuseEnterData(
            market_id.market_id, amount
        )
        return [
            FuseActionDynamicStruct(
                self.fuse_address, compound_v3_supply_fuse_enter_data.function_call()
            )
        ]

    def create_fuse_exit_action(
        self, market_id: MarketId, amount: int
    ) -> List[FuseActionDynamicStruct]:
        compound_v3_supply_fuse_exit_data = CompoundV3SupplyFuseExitData(
            market_id.market_id, amount
        )
        return [
            FuseActionDynamicStruct(
                self.fuse_address, compound_v3_supply_fuse_exit_data.function_call()
            )
        ]

    def create_fuse_claim_action(
        self, market_id: MarketId
    ) -> List[FuseActionDynamicStruct]:
        raise NotImplementedError("Fuse claim action is not supported for Compound V3")


class CompoundV3SupplyFuseEnterData:
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


class CompoundV3SupplyFuseExitData:
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

from typing import List

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion_sdk.fuse.FuseActionDynamicStruct import FuseActionDynamicStruct
from ipor_fusion_sdk.fuse.Fuse import Fuse
from ipor_fusion_sdk.operation.BaseOperation import MarketId


class Erc4626SupplyFuse(Fuse):
    ENTER = "enter"
    EXIT = "exit"

    def __init__(self, fuse_address: str, protocol_id: str, erc4626_address: str):
        if not fuse_address:
            raise ValueError("fuseAddress is required")
        if not protocol_id:
            raise ValueError("protocolId is required")
        if not erc4626_address:
            raise ValueError("erc4626Address is required")
        self.fuse_address = fuse_address
        self.protocol_id = protocol_id
        self.erc4626_address = erc4626_address

    def supports(self, market_id: MarketId) -> bool:
        if market_id is None:
            raise ValueError("marketId is required")
        return (
            market_id.protocol_id == self.protocol_id
            and market_id.market_id == self.erc4626_address
        )

    def create_fuse_enter_action(
        self, market_id: MarketId, amount: int
    ) -> List[FuseActionDynamicStruct]:
        enter_data = Erc4626SupplyFuseEnterData(market_id.market_id, amount)
        return [FuseActionDynamicStruct(self.fuse_address, enter_data.function_call())]

    def create_fuse_exit_action(
        self, market_id: MarketId, amount: int
    ) -> List[FuseActionDynamicStruct]:
        exit_data = Erc4626SupplyFuseExitData(market_id.market_id, amount)
        return [
            FuseActionDynamicStruct(self.fuse_address, exit_data.function_selector())
        ]

    def create_fuse_claim_action(
        self, market_id: MarketId
    ) -> List[FuseActionDynamicStruct]:
        raise NotImplementedError(
            "Fuse claim action is not supported for generic ERC4626"
        )


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

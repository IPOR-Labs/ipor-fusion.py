from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.types import Amount, MorphoBlueMarketId


class MorphoBlueSupplyFuse:
    ENTER = "enter"
    EXIT = "exit"

    def __init__(self, fuse_address: ChecksumAddress):
        if fuse_address is None:
            raise ValueError("fuseAddress is required")
        self.fuse_address = fuse_address

    def supply(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        morpho_blue_supply_fuse_enter_data = MorphoBlueSupplyFuseEnterData(
            market_id, amount
        )
        return FuseAction(
            self.fuse_address, morpho_blue_supply_fuse_enter_data.function_call()
        )

    def withdraw(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        morpho_blue_supply_fuse_exit_data = MorphoBlueSupplyFuseExitData(
            market_id, amount
        )
        return FuseAction(
            self.fuse_address, morpho_blue_supply_fuse_exit_data.function_call()
        )


class MorphoBlueSupplyFuseEnterData:
    def __init__(self, market_id: MorphoBlueMarketId, amount: Amount):
        self.market_id = market_id
        self.amount = amount

    def encode(self) -> bytes:
        return encode(
            ["bytes32", "uint256"],
            [bytes.fromhex(self.market_id), self.amount],
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((bytes32,uint256))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class MorphoBlueSupplyFuseExitData:
    def __init__(self, market_id: MorphoBlueMarketId, amount: Amount):
        self.market_id = market_id
        self.amount = amount

    def encode(self) -> bytes:
        return encode(
            ["bytes32", "uint256"], [bytes.fromhex(self.market_id), self.amount]
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("exit((bytes32,uint256))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()

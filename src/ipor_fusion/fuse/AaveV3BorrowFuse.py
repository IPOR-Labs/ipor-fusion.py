from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.MarketId import MarketId
from ipor_fusion.fuse.FuseAction import FuseAction


class AaveV3BorrowFuse:
    PROTOCOL_ID = "aave-v3"

    def __init__(self, fuse_address: ChecksumAddress):
        if fuse_address is None:
            raise ValueError("fuseAddress is required")
        self.fuse_address = fuse_address

    def borrow(self, market_id: MarketId, amount: int) -> FuseAction:
        aave_v3_borrow_fuse_enter_data = AaveV3BorrowFuseEnterData(
            market_id.market_id, amount
        )
        return FuseAction(
            self.fuse_address, aave_v3_borrow_fuse_enter_data.function_call()
        )

    def repay(self, market_id: MarketId, amount: int) -> FuseAction:
        aave_v3_borrow_fuse_exit_data = AaveV3BorrowFuseExitData(
            market_id.market_id, amount
        )
        return FuseAction(
            self.fuse_address, aave_v3_borrow_fuse_exit_data.function_call()
        )


class AaveV3BorrowFuseEnterData:
    def __init__(self, asset: str, amount: int):
        self.asset = asset
        self.amount = amount

    def encode(self) -> bytes:
        return encode(
            ["address", "uint256"],
            [self.asset, self.amount],
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((address,uint256))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class AaveV3BorrowFuseExitData:
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

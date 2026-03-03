from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.types import Amount, MorphoBlueMarketId, Shares


class MorphoBlueBorrowFuse:
    ENTER = "enter"
    EXIT = "exit"

    def __init__(self, fuse_address: ChecksumAddress):
        self._fuse_address = fuse_address

    def borrow(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        if self._fuse_address is None:
            raise ValueError("fuseAddress is required")
        enter_data = MorphoBlueBorrowFuseEnterData(market_id, amount, 0)
        return FuseAction(self._fuse_address, enter_data.function_call())

    def repay(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        if self._fuse_address is None:
            raise ValueError("fuseAddress is required")
        exit_data = MorphoBlueBorrowFuseExitData(market_id, amount, 0)
        return FuseAction(self._fuse_address, exit_data.function_call())


class MorphoBlueBorrowFuseEnterData:
    def __init__(
        self,
        market_id: MorphoBlueMarketId,
        amount_to_borrow: Amount,
        shares_to_borrow: Shares,
    ):
        self.market_id = market_id
        self.amount_to_borrow = amount_to_borrow
        self.shares_to_borrow = shares_to_borrow

    def encode(self) -> bytes:
        return encode(
            ["bytes32", "uint256", "uint256"],
            [
                bytes.fromhex(self.market_id),
                self.amount_to_borrow,
                self.shares_to_borrow,
            ],
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((bytes32,uint256,uint256))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class MorphoBlueBorrowFuseExitData:
    def __init__(
        self,
        market_id: MorphoBlueMarketId,
        amount_to_repay: Amount,
        shares_to_repay: Shares,
    ):
        self.market_id = market_id
        self.amount_to_repay = amount_to_repay
        self.shares_to_repay = shares_to_repay

    def encode(self) -> bytes:
        return encode(
            ["bytes32", "uint256", "uint256"],
            [
                bytes.fromhex(self.market_id),
                self.amount_to_repay,
                self.shares_to_repay,
            ],
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("exit((bytes32,uint256,uint256))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()

from eth_typing import ChecksumAddress

from ipor_fusion.MarketId import MarketId
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.AaveV3BorrowFuse import AaveV3BorrowFuse
from ipor_fusion.fuse.AaveV3SupplyFuse import AaveV3SupplyFuse
from ipor_fusion.fuse.FuseAction import FuseAction


class AaveV3Market:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        aave_v3_supply_fuse_address: ChecksumAddress = None,
        aave_v3_borrow_fuse_address: ChecksumAddress = None,
    ):
        if transaction_executor is None:
            raise ValueError("transaction_executor is required")

        self._transaction_executor = transaction_executor
        self._aave_v3_supply_fuse = None
        self._aave_v3_borrow_fuse = None
        if aave_v3_supply_fuse_address:
            self._aave_v3_supply_fuse = AaveV3SupplyFuse(aave_v3_supply_fuse_address)
        if aave_v3_borrow_fuse_address:
            self._aave_v3_borrow_fuse = AaveV3BorrowFuse(aave_v3_borrow_fuse_address)

    def supply(
        self, asset_address: ChecksumAddress, amount: int, e_mode: int
    ) -> FuseAction:
        if self._aave_v3_supply_fuse is None:
            raise UnsupportedFuseError("AaveV3SupplyFuse is not set up")

        market_id = MarketId(
            AaveV3SupplyFuse.PROTOCOL_ID,
            asset_address,
        )
        return self._aave_v3_supply_fuse.supply(
            market_id=market_id, amount=amount, e_mode=e_mode
        )

    def withdraw(self, asset_address: ChecksumAddress, amount: int) -> FuseAction:
        if self._aave_v3_supply_fuse is None:
            raise UnsupportedFuseError("AaveV3SupplyFuse is not set up")

        market_id = MarketId(AaveV3SupplyFuse.PROTOCOL_ID, asset_address)
        return self._aave_v3_supply_fuse.withdraw(market_id, amount)

    def borrow(self, asset_address: ChecksumAddress, amount: int) -> FuseAction:
        if self._aave_v3_borrow_fuse is None:
            raise UnsupportedFuseError("AaveV3BorrowFuse is not set up")

        market_id = MarketId(
            AaveV3BorrowFuse.PROTOCOL_ID,
            asset_address,
        )

        return self._aave_v3_borrow_fuse.borrow(market_id, amount)

    def repay(self, asset_address: ChecksumAddress, amount: int) -> FuseAction:
        if self._aave_v3_borrow_fuse is None:
            raise UnsupportedFuseError("AaveV3BorrowFuse is not set up")

        market_id = MarketId(
            AaveV3BorrowFuse.PROTOCOL_ID,
            asset_address,
        )

        return self._aave_v3_borrow_fuse.repay(market_id, amount)

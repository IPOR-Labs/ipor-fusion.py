from eth_typing import ChecksumAddress

from ipor_fusion.MarketId import MarketId
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.fuse.CompoundV3SupplyFuse import CompoundV3SupplyFuse
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.types import Amount


class CompoundV3Market:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        compound_v3_supply_fuse_address: ChecksumAddress,
    ):
        if transaction_executor is None:
            raise ValueError("transaction_executor is required")
        if compound_v3_supply_fuse_address is None:
            raise ValueError("compound_v3_supply_fuse_address is required")
        self._transaction_executor = transaction_executor
        self._compound_v3_supply_fuse = CompoundV3SupplyFuse(
            compound_v3_supply_fuse_address
        )

    def supply(self, asset_address: ChecksumAddress, amount: Amount) -> FuseAction:
        market_id = MarketId(
            CompoundV3SupplyFuse.PROTOCOL_ID,
            asset_address,
        )
        return self._compound_v3_supply_fuse.supply(market_id, amount)

    def withdraw(self, asset_address: ChecksumAddress, amount: Amount) -> FuseAction:
        market_id = MarketId(
            CompoundV3SupplyFuse.PROTOCOL_ID,
            asset_address,
        )
        return self._compound_v3_supply_fuse.withdraw(market_id, amount)

from eth_typing import ChecksumAddress

from ipor_fusion.MarketId import MarketId
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.CompoundV3SupplyFuse import CompoundV3SupplyFuse
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.types import Amount


class CompoundV3Market:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        compound_v3_supply_fuse_address: ChecksumAddress,
    ):
        self._transaction_executor = transaction_executor
        self._compound_v3_supply_fuse = CompoundV3SupplyFuse(
            compound_v3_supply_fuse_address
        )

    def supply(self, asset_address: ChecksumAddress, amount: Amount) -> FuseAction:
        if not hasattr(self, "_compound_v3_supply_fuse"):
            raise UnsupportedFuseError(
                "CompoundV3SupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(
            CompoundV3SupplyFuse.PROTOCOL_ID,
            asset_address,
        )
        return self._compound_v3_supply_fuse.supply(market_id, amount)

    def withdraw(self, asset_address: ChecksumAddress, amount: Amount) -> FuseAction:
        if not hasattr(self, "_compound_v3_supply_fuse"):
            raise UnsupportedFuseError(
                "CompoundV3SupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(
            CompoundV3SupplyFuse.PROTOCOL_ID,
            asset_address,
        )
        return self._compound_v3_supply_fuse.withdraw(market_id, amount)

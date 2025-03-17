from typing import List

from web3 import Web3

from ipor_fusion.FuseMapper import FuseMapper
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.fuse.MorphoBlueSupplyFuse import MorphoBlueSupplyFuse
from ipor_fusion.types import Amount, MorphoBlueMarketId


class MorphoBlueMarket:

    def __init__(
        self,
        chain_id: int,
        transaction_executor: TransactionExecutor,
        fuses: List[str],
    ):
        self._chain_id = chain_id
        self._transaction_executor = transaction_executor

        self._any_fuse_supported = False
        for fuse in fuses:
            checksum_fuse = Web3.to_checksum_address(fuse)
            if checksum_fuse in FuseMapper.map(chain_id, "MorphoSupplyFuse"):
                self._morpho_blue_supply_fuse = MorphoBlueSupplyFuse(checksum_fuse)
                self._any_fuse_supported = True

    def is_market_supported(self) -> bool:
        return self._any_fuse_supported

    def supply(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        if not hasattr(self, "_morpho_blue_supply_fuse"):
            raise UnsupportedFuseError(
                "MorphoBlueSupplyFuse is not supported by PlasmaVault"
            )
        return self._morpho_blue_supply_fuse.supply(market_id, amount)

    def withdraw(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        if not hasattr(self, "_morpho_blue_supply_fuse"):
            raise UnsupportedFuseError(
                "MorphoBlueSupplyFuse is not supported by PlasmaVault"
            )
        return self._morpho_blue_supply_fuse.withdraw(market_id, amount)

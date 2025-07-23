from typing import List

from eth_typing import ChecksumAddress

from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.fuse.MorphoBlueSupplyFuse import MorphoBlueSupplyFuse
from ipor_fusion.fuse.MorphoFlashLoanFuse import MorphoFlashLoanFuse
from ipor_fusion.types import Amount, MorphoBlueMarketId


class MorphoMarket:

    def __init__(
        self,
        chain_id: int,
        transaction_executor: TransactionExecutor,
        morpho_supply_fuse_address: ChecksumAddress,
        morpho_flash_loan_fuse_address: ChecksumAddress,
    ):
        if transaction_executor is None:
            raise ValueError("transaction_executor is required")

        self._chain_id = chain_id
        self._transaction_executor = transaction_executor

        self._morpho_flash_loan_fuse = morpho_flash_loan_fuse_address

        self._morpho_blue_supply_fuse = MorphoBlueSupplyFuse(morpho_supply_fuse_address)
        self._morpho_flash_loan_fuse = MorphoFlashLoanFuse(
            morpho_flash_loan_fuse_address
        )

    def supply(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        if self._morpho_blue_supply_fuse is None:
            raise UnsupportedFuseError(
                "MorphoBlueSupplyFuse is not supported by PlasmaVault"
            )
        return self._morpho_blue_supply_fuse.supply(market_id, amount)

    def withdraw(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        if self._morpho_blue_supply_fuse is None:
            raise UnsupportedFuseError(
                "MorphoBlueSupplyFuse is not supported by PlasmaVault"
            )
        return self._morpho_blue_supply_fuse.withdraw(market_id, amount)

    def flash_loan(
        self, asset_address: ChecksumAddress, amount: Amount, actions: List[FuseAction]
    ) -> FuseAction:
        if self._morpho_flash_loan_fuse is None:
            raise UnsupportedFuseError(
                "MorphoFlashLoanFuse is not supported by PlasmaVault"
            )
        return self._morpho_flash_loan_fuse.flash_loan(asset_address, amount, actions)

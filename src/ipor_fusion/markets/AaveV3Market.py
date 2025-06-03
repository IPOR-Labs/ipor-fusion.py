from typing import List

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.FuseMapper import FuseMapper
from ipor_fusion.MarketId import MarketId
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.AaveV3BorrowFuse import AaveV3BorrowFuse
from ipor_fusion.fuse.AaveV3SupplyFuse import AaveV3SupplyFuse
from ipor_fusion.fuse.FuseAction import FuseAction


class AaveV3Market:

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
            if checksum_fuse in FuseMapper.map(chain_id, "AaveV3SupplyFuse"):
                self._aave_v3_supply_fuse = AaveV3SupplyFuse(checksum_fuse)
                self._any_fuse_supported = True
            if checksum_fuse in FuseMapper.map(chain_id, "AaveV3BorrowFuse"):
                self._aave_v3_borrow_fuse = AaveV3BorrowFuse(checksum_fuse)
                self._any_fuse_supported = True

    def is_market_supported(self) -> bool:
        return self._any_fuse_supported

    def supply(
        self, asset_address: ChecksumAddress, amount: int, e_mode: int
    ) -> FuseAction:
        if not hasattr(self, "_aave_v3_supply_fuse"):
            raise UnsupportedFuseError(
                "AaveV3SupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(
            AaveV3SupplyFuse.PROTOCOL_ID,
            asset_address,
        )
        return self._aave_v3_supply_fuse.supply(
            market_id=market_id, amount=amount, e_mode=e_mode
        )

    def withdraw(self, asset_address: ChecksumAddress, amount: int) -> FuseAction:
        if not hasattr(self, "_aave_v3_supply_fuse"):
            raise UnsupportedFuseError(
                "AaveV3SupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(AaveV3SupplyFuse.PROTOCOL_ID, asset_address)
        return self._aave_v3_supply_fuse.withdraw(market_id, amount)

    def borrow(self, asset_address: ChecksumAddress, amount: int) -> FuseAction:
        if not hasattr(self, "_aave_v3_borrow_fuse"):
            raise UnsupportedFuseError(
                "AaveV3BorrowFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(
            AaveV3BorrowFuse.PROTOCOL_ID,
            asset_address,
        )

        return self._aave_v3_borrow_fuse.borrow(market_id, amount)

    def repay(self, asset_address: ChecksumAddress, amount: int) -> FuseAction:
        if not hasattr(self, "_aave_v3_borrow_fuse"):
            raise UnsupportedFuseError(
                "AaveV3BorrowFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(
            AaveV3BorrowFuse.PROTOCOL_ID,
            asset_address,
        )

        return self._aave_v3_borrow_fuse.repay(market_id, amount)

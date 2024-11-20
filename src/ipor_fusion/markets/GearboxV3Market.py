from typing import List

from web3 import Web3

from ipor_fusion.ERC20 import ERC20
from ipor_fusion.MarketId import MarketId
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.fuse.GearboxSupplyFuse import GearboxSupplyFuse


class GearboxV3Market:
    GEARBOX_FARM_FUSE = Web3.to_checksum_address(
        "0x50fbc3e2eb2ec49204a41ea47946016703ba358d"
    )
    GEARBOX_POOL_FUSE = Web3.to_checksum_address(
        "0xeb58e3adb9e537c06ebe2dee6565b248ec758a93"
    )
    GEARBOX_CLAIM_FUSE = Web3.to_checksum_address(
        "0x2496Aaeb9F74CcecCE0902F3459F3dde795d7A65"
    )

    GEARBOX_V3_POOL_USDC = Web3.to_checksum_address(
        "0x890A69EF363C9c7BdD5E36eb95Ceb569F63ACbF6"
    )
    GEARBOX_V3_FARM_POOL_USDC = Web3.to_checksum_address(
        "0xD0181a36B0566a8645B7eECFf2148adE7Ecf2BE9"
    )

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        fuses: List[str],
    ):
        self._transaction_executor = transaction_executor
        self._pool = ERC20(transaction_executor, self.GEARBOX_V3_POOL_USDC)
        self._farm_pool = ERC20(transaction_executor, self.GEARBOX_V3_FARM_POOL_USDC)

        self._any_fuse_supported = False
        for fuse in fuses:
            checksum_fuse = Web3.to_checksum_address(fuse)
            if checksum_fuse == self.GEARBOX_FARM_FUSE:
                self._gearbox_supply_fuse = GearboxSupplyFuse(
                    self.GEARBOX_V3_POOL_USDC,
                    self.GEARBOX_POOL_FUSE,
                    self.GEARBOX_V3_FARM_POOL_USDC,
                    self.GEARBOX_FARM_FUSE,
                )
                self._any_fuse_supported = True

    def is_market_supported(self) -> bool:
        return self._any_fuse_supported

    def pool(self) -> ERC20:
        return self._pool

    def farm_pool(self) -> ERC20:
        return self._farm_pool

    def supply_and_stake(self, amount: int) -> List[FuseAction]:
        if not hasattr(self, "_gearbox_supply_fuse"):
            raise UnsupportedFuseError(
                "GearboxSupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(GearboxSupplyFuse.PROTOCOL_ID, self._pool.address())
        return self._gearbox_supply_fuse.supply_and_stake(market_id, amount)

    def unstake_and_withdraw(self, amount: int) -> List[FuseAction]:
        if not hasattr(self, "_gearbox_supply_fuse"):
            raise UnsupportedFuseError(
                "GearboxSupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(GearboxSupplyFuse.PROTOCOL_ID, self._pool.address())
        return self._gearbox_supply_fuse.unstake_and_withdraw(market_id, amount)

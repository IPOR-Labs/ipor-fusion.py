from typing import List

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.ERC20 import ERC20
from ipor_fusion.MarketId import MarketId
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.fuse.GearboxSupplyFuse import GearboxSupplyFuse


class GearboxV3Market:

    def __init__(
        self,
        chain_id: int,
        transaction_executor: TransactionExecutor,
        d_usdcv_3_address: ChecksumAddress = None,
        erc_4626_supply_fuse_market_id_3_address: ChecksumAddress = None,
        gearbox_v3_farm_supply_fuse_address: ChecksumAddress = None,
        farmd_usdcv_3_address: ChecksumAddress = None,
    ):
        if transaction_executor is None:
            raise ValueError("transaction_executor is required")

        self._chain_id = chain_id
        self._transaction_executor = transaction_executor
        self._erc_4626_supply_fuse_market_id_3 = (
            erc_4626_supply_fuse_market_id_3_address
        )
        self._gearbox_v3_farm_supply_fuse_address = gearbox_v3_farm_supply_fuse_address

        if d_usdcv_3_address is None:
            self._d_usdcv_3 = self.get_dUSDCV3()

        if farmd_usdcv_3_address is None:
            self._farmd_usdcv_3 = self.get_farmdUSDCV3()

        self._gearbox_supply_fuse = GearboxSupplyFuse(
            self._d_usdcv_3,
            self._erc_4626_supply_fuse_market_id_3,
            self._farmd_usdcv_3,
            self._gearbox_v3_farm_supply_fuse_address,
        )

        self._pool = ERC20(transaction_executor, self._d_usdcv_3)
        self._farm_pool = ERC20(transaction_executor, self._farmd_usdcv_3)

    def pool(self) -> ERC20:
        return self._pool

    def farm_pool(self) -> ERC20:
        return self._farm_pool

    def supply_and_stake(self, amount: int) -> List[FuseAction]:
        if self._gearbox_supply_fuse is None:
            raise UnsupportedFuseError(
                "GearboxSupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(GearboxSupplyFuse.PROTOCOL_ID, self._pool.address())
        return self._gearbox_supply_fuse.supply_and_stake(market_id, amount)

    def unstake_and_withdraw(self, amount: int) -> List[FuseAction]:
        if self._gearbox_supply_fuse is None:
            raise UnsupportedFuseError(
                "GearboxSupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(GearboxSupplyFuse.PROTOCOL_ID, self._pool.address())
        return self._gearbox_supply_fuse.unstake_and_withdraw(market_id, amount)

    def get_dUSDCV3(self) -> ChecksumAddress:
        if self._chain_id == 42161:
            return Web3.to_checksum_address(
                "0x890A69EF363C9c7BdD5E36eb95Ceb569F63ACbF6"
            )

        raise BaseException("Chain ID not supported")

    def get_farmdUSDCV3(self) -> ChecksumAddress:
        if self._chain_id == 42161:
            return Web3.to_checksum_address(
                "0xD0181a36B0566a8645B7eECFf2148adE7Ecf2BE9"
            )

        raise BaseException("Chain ID not supported")

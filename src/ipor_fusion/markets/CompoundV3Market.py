from typing import List

from web3 import Web3

from ipor_fusion.ERC20 import ERC20
from ipor_fusion.MarketId import MarketId
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.CompoundV3SupplyFuse import CompoundV3SupplyFuse
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.FuseMappingLoader import FuseMappingLoader


class CompoundV3Market:

    COMPOUND_V3_USDC_C_TOKEN = Web3.to_checksum_address(
        "0x9c4ec768c28520b50860ea7a15bd7213a9ff58bf"
    )

    USDC = Web3.to_checksum_address("0xaf88d065e77c8cc2239327c5edb3a432268e5831")

    def __init__(
        self,
        chain_id: int,
        transaction_executor: TransactionExecutor,
        fuses: List[str],
    ):
        self._chain_id = chain_id
        self._transaction_executor = transaction_executor
        self._compound_v3_usdc_c_token = ERC20(
            transaction_executor, self.COMPOUND_V3_USDC_C_TOKEN
        )

        self._any_fuse_supported = False
        for fuse in fuses:
            checksum_fuse = Web3.to_checksum_address(fuse)
            if checksum_fuse in FuseMappingLoader.load(
                chain_id, "CompoundV3SupplyFuse"
            ):
                self._compound_v3_supply_fuse = CompoundV3SupplyFuse(checksum_fuse)
                self._any_fuse_supported = True

    def is_market_supported(self) -> bool:
        return self._any_fuse_supported

    def supply(self, amount: int) -> FuseAction:
        if not hasattr(self, "_compound_v3_supply_fuse"):
            raise UnsupportedFuseError(
                "CompoundV3SupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(CompoundV3SupplyFuse.PROTOCOL_ID, self.USDC)
        return self._compound_v3_supply_fuse.supply(market_id, amount)

    def withdraw(self, amount: int) -> FuseAction:
        if not hasattr(self, "_compound_v3_supply_fuse"):
            raise UnsupportedFuseError(
                "CompoundV3SupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(CompoundV3SupplyFuse.PROTOCOL_ID, self.USDC)
        return self._compound_v3_supply_fuse.withdraw(market_id, amount)

    def usdc_c_token(self) -> ERC20:
        return self._compound_v3_usdc_c_token

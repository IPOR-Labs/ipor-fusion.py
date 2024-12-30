from typing import List

from web3 import Web3

from ipor_fusion.AssetMapper import AssetMapper
from ipor_fusion.ERC20 import ERC20
from ipor_fusion.FuseMapper import FuseMapper
from ipor_fusion.MarketId import MarketId
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
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
                self._aave_v3_supply_fuse = AaveV3SupplyFuse(
                    checksum_fuse,
                    AssetMapper.map(chain_id=chain_id, asset_symbol="USDC"),
                )
                self._any_fuse_supported = True

        if self._any_fuse_supported:
            self._usdc_a_token_arb_usdc_n = ERC20(
                transaction_executor,
                AssetMapper.map(chain_id=chain_id, asset_symbol="aArbUSDCn"),
            )

    def is_market_supported(self) -> bool:
        return self._any_fuse_supported

    def supply(self, amount: int) -> FuseAction:
        if not hasattr(self, "_aave_v3_supply_fuse"):
            raise UnsupportedFuseError(
                "AaveV3SupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(
            AaveV3SupplyFuse.PROTOCOL_ID,
            AssetMapper.map(chain_id=self._chain_id, asset_symbol="USDC"),
        )
        return self._aave_v3_supply_fuse.supply(market_id, amount)

    def withdraw(self, amount: int) -> FuseAction:
        if not hasattr(self, "_aave_v3_supply_fuse"):
            raise UnsupportedFuseError(
                "AaveV3SupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(
            AaveV3SupplyFuse.PROTOCOL_ID,
            AssetMapper.map(chain_id=self._chain_id, asset_symbol="USDC"),
        )
        return self._aave_v3_supply_fuse.withdraw(market_id, amount)

    def usdc_a_token_arb_usdc_n(self):
        return self._usdc_a_token_arb_usdc_n

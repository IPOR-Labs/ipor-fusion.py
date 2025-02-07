from eth_typing import ChecksumAddress

from ipor_fusion.MarketId import MarketId
from ipor_fusion.fuse.Erc4626SupplyFuse import (
    Erc4626SupplyFuseExitData,
    Erc4626SupplyFuseEnterData,
)
from ipor_fusion.fuse.FuseAction import FuseAction


class GearboxSupplyOnlyFuse:
    MAX_UINT256 = (1 << 256) - 1

    def __init__(
        self, d_token_address: ChecksumAddress, erc4626_fuse_address: ChecksumAddress
    ):
        self.d_token_address = d_token_address
        self.erc4626_fuse_address = erc4626_fuse_address

    def supply(self, market_id: MarketId, amount: int) -> FuseAction:
        erc4626_supply_fuse_enter_data = Erc4626SupplyFuseEnterData(
            market_id.market_id, amount
        )
        return FuseAction(
            self.erc4626_fuse_address, erc4626_supply_fuse_enter_data.function_call()
        )

    def withdraw(self, market_id: MarketId) -> FuseAction:
        erc4626_supply_fuse_exit_data = Erc4626SupplyFuseExitData(
            market_id.market_id, self.MAX_UINT256
        )
        return FuseAction(
            self.erc4626_fuse_address, erc4626_supply_fuse_exit_data.function_call()
        )

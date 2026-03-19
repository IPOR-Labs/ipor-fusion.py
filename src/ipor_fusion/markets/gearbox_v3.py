from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.fuses.gearbox_v3 import GearboxSupplyFuse
from ipor_fusion.markets.base import LendingProtocol


class GearboxV3Market(LendingProtocol):
    def __init__(
        self,
        erc4626_fuse: ChecksumAddress | None = None,
        farm_fuse: ChecksumAddress | None = None,
        d_token: ChecksumAddress | None = None,
        farmd_token: ChecksumAddress | None = None,
    ):
        self._d_token = d_token
        self._farmd_token = farmd_token
        self._supply_fuse: GearboxSupplyFuse | None = None
        if erc4626_fuse and farm_fuse and d_token and farmd_token:
            self._supply_fuse = GearboxSupplyFuse(
                erc4626_fuse_address=erc4626_fuse,
                farm_fuse_address=farm_fuse,
                d_token_address=d_token,
                farmd_token_address=farmd_token,
            )

    def supply(self, asset: ChecksumAddress, amount: int, **kwargs) -> list[FuseAction]:
        if not self._supply_fuse:
            raise ValueError("GearboxV3 supply fuse not configured")
        return self._supply_fuse.supply_and_stake(asset, amount)

    def supply_and_stake(self, amount: int, **kwargs) -> list[FuseAction]:
        if not self._supply_fuse:
            raise ValueError("GearboxV3 supply fuse not configured")
        return self._supply_fuse.supply_and_stake(self._d_token, amount)

    def withdraw(
        self, asset: ChecksumAddress, amount: int, **kwargs
    ) -> list[FuseAction]:
        if not self._supply_fuse:
            raise ValueError("GearboxV3 supply fuse not configured")
        return self._supply_fuse.unstake_and_withdraw(asset, amount)

    def unstake_and_withdraw(self, amount: int, **kwargs) -> list[FuseAction]:
        if not self._supply_fuse:
            raise ValueError("GearboxV3 supply fuse not configured")
        return self._supply_fuse.unstake_and_withdraw(self._d_token, amount)

    def farm_pool(self):
        from ipor_fusion.core.erc20 import ERC20Token

        if not self._farmd_token:
            raise ValueError("GearboxV3 farmd_token not configured")
        return ERC20Token(self._farmd_token)

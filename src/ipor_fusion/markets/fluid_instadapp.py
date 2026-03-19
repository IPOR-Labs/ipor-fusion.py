from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.fuses.fluid_instadapp import FluidInstadappSupplyFuse
from ipor_fusion.markets.base import LendingProtocol


class FluidInstadappMarket(LendingProtocol):
    def __init__(
        self,
        erc4626_fuse: ChecksumAddress | None = None,
        staking_fuse: ChecksumAddress | None = None,
        pool_token: ChecksumAddress | None = None,
        staking_contract: ChecksumAddress | None = None,
    ):
        self._pool_token = pool_token
        self._staking_contract = staking_contract
        self._supply_fuse: FluidInstadappSupplyFuse | None = None
        if erc4626_fuse and staking_fuse and pool_token and staking_contract:
            self._supply_fuse = FluidInstadappSupplyFuse(
                erc4626_fuse_address=erc4626_fuse,
                staking_fuse_address=staking_fuse,
                pool_token_address=pool_token,
                staking_contract_address=staking_contract,
            )

    def supply(self, asset: ChecksumAddress, amount: int, **kwargs) -> list[FuseAction]:
        if not self._supply_fuse:
            raise ValueError("FluidInstadapp supply fuse not configured")
        return self._supply_fuse.supply_and_stake(asset, amount)

    def supply_and_stake(self, amount: int) -> list[FuseAction]:
        if not self._supply_fuse:
            raise ValueError("FluidInstadapp supply fuse not configured")
        return self._supply_fuse.supply_and_stake(self._pool_token, amount)

    def withdraw(
        self, asset: ChecksumAddress, amount: int, **kwargs
    ) -> list[FuseAction]:
        if not self._supply_fuse:
            raise ValueError("FluidInstadapp supply fuse not configured")
        return self._supply_fuse.unstake_and_withdraw(asset, amount)

    def unstake_and_withdraw(self, amount: int) -> list[FuseAction]:
        if not self._supply_fuse:
            raise ValueError("FluidInstadapp supply fuse not configured")
        return self._supply_fuse.unstake_and_withdraw(self._pool_token, amount)

    def staking_pool(self):
        from ipor_fusion.core.erc20 import ERC20Token

        if not self._staking_contract:
            raise ValueError("FluidInstadapp staking_contract not configured")
        return ERC20Token(self._staking_contract)

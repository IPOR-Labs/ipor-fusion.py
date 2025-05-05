from typing import List

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.ERC20 import ERC20
from ipor_fusion.FuseMapper import FuseMapper
from ipor_fusion.MarketId import MarketId
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FluidInstadappSupplyFuse import FluidInstadappSupplyFuse
from ipor_fusion.fuse.FuseAction import FuseAction


class FluidInstadappMarket:

    def __init__(
        self,
        chain_id: int,
        transaction_executor: TransactionExecutor,
        fuses: List[ChecksumAddress],
    ):
        self._chain_id = chain_id
        self._transaction_executor = transaction_executor

        self._any_fuse_supported = False
        for fuse in fuses:
            checksum_fuse = Web3.to_checksum_address(fuse)
            if checksum_fuse in FuseMapper.map(
                chain_id=chain_id, fuse_name="Erc4626SupplyFuseMarketId5"
            ):
                self._fluid_instadapp_pool_fuse = FluidInstadappSupplyFuse(
                    self.get_fUSDC(),
                    checksum_fuse,
                    self.get_FluidLendingStakingRewardsUsdc(),
                    self.get_safe_fuse_address(
                        chain_id, fuses, "FluidInstadappStakingSupplyFuse"
                    ),
                )
                self._any_fuse_supported = True

        if self._any_fuse_supported:
            self._pool = ERC20(
                transaction_executor,
                self.get_fUSDC(),
            )
            self._staking_pool = ERC20(
                transaction_executor,
                self.get_FluidLendingStakingRewardsUsdc(),
            )

    @staticmethod
    def get_safe_fuse_address(
        chain_id: int, vault_fuses: List[ChecksumAddress], fuse_name: str
    ) -> ChecksumAddress:
        fuses_from_mapper = FuseMapper.map(chain_id=chain_id, fuse_name=fuse_name)
        fuse = None
        for f in vault_fuses:
            for x in fuses_from_mapper:
                if f.lower() == x.lower():
                    fuse = f

        if not fuse:
            raise UnsupportedFuseError()

        return Web3.to_checksum_address(fuse)

    def is_market_supported(self) -> bool:
        return self._any_fuse_supported

    def staking_pool(self) -> ERC20:
        return self._staking_pool

    def pool(self) -> ERC20:
        return self._pool

    def supply_and_stake(self, amount: int) -> List[FuseAction]:
        if not hasattr(self, "_fluid_instadapp_pool_fuse"):
            raise UnsupportedFuseError(
                "FluidInstadappSupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID,
            self.get_fUSDC(),
        )
        return self._fluid_instadapp_pool_fuse.supply_and_stake(market_id, amount)

    def unstake_and_withdraw(self, amount: int) -> List[FuseAction]:
        if not hasattr(self, "_fluid_instadapp_pool_fuse"):
            raise UnsupportedFuseError(
                "FluidInstadappSupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID,
            self.get_fUSDC(),
        )
        return self._fluid_instadapp_pool_fuse.unstake_and_withdraw(market_id, amount)

    def get_fUSDC(self) -> ChecksumAddress:
        if self._chain_id == 42161:
            return Web3.to_checksum_address(
                "0x1a996cb54bb95462040408c06122d45d6cdb6096"
            )
        if self._chain_id == 8453:
            return Web3.to_checksum_address(
                "0xf42f5795D9ac7e9D757dB633D693cD548Cfd9169"
            )

        raise BaseException("Chain ID not supported")

    def get_FluidLendingStakingRewardsUsdc(self) -> ChecksumAddress:
        if self._chain_id == 42161:
            return Web3.to_checksum_address(
                "0x48f89d731C5e3b5BeE8235162FC2C639Ba62DB7d"
            )
        if self._chain_id == 8453:
            return Web3.to_checksum_address(
                "0x48f89d731C5e3b5BeE8235162FC2C639Ba62DB7d"
            )

        raise BaseException("Chain ID not supported")

from typing import List

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.ERC20 import ERC20
from ipor_fusion.MarketId import MarketId
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedChainId import UnsupportedChainId
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FluidInstadappSupplyFuse import FluidInstadappSupplyFuse
from ipor_fusion.fuse.FuseAction import FuseAction


class FluidInstadappMarket:

    def __init__(
        self,
        chain_id: int,
        transaction_executor: TransactionExecutor,
        erc_4626_supply_fuse_market_id_5_address: ChecksumAddress,
        fluid_instadapp_staking_supply_fuse_address: ChecksumAddress,
        f_usdc_address: ChecksumAddress = None,
        fluid_lending_staking_rewards_usdc_address: ChecksumAddress = None,
    ):
        if transaction_executor is None:
            raise ValueError("transaction_executor is required")

        self._chain_id = chain_id
        self._transaction_executor = transaction_executor
        self._erc_4626_supply_fuse_market_id_5_address = (
            erc_4626_supply_fuse_market_id_5_address
        )
        self._fluid_instadapp_staking_supply_fuse_address = (
            fluid_instadapp_staking_supply_fuse_address
        )

        self._f_usdc_address = f_usdc_address
        if f_usdc_address is None:
            self._f_usdc_address = self.get_fUSDC()

        self._fluid_lending_staking_rewards_usdc_address = (
            fluid_lending_staking_rewards_usdc_address
        )
        if fluid_lending_staking_rewards_usdc_address is None:
            self._fluid_lending_staking_rewards_usdc_address = (
                self.get_FluidLendingStakingRewardsUsdc()
            )

        self._fluid_instadapp_pool_fuse = FluidInstadappSupplyFuse(
            self._f_usdc_address,
            self._erc_4626_supply_fuse_market_id_5_address,
            self._fluid_lending_staking_rewards_usdc_address,
            self._fluid_instadapp_staking_supply_fuse_address,
        )

        self._pool = ERC20(
            transaction_executor,
            self._f_usdc_address,
        )
        self._staking_pool = ERC20(
            transaction_executor,
            self._fluid_lending_staking_rewards_usdc_address,
        )

    def staking_pool(self) -> ERC20:
        return self._staking_pool

    def pool(self) -> ERC20:
        return self._pool

    def supply_and_stake(self, amount: int) -> List[FuseAction]:
        if self._fluid_instadapp_pool_fuse is None:
            raise UnsupportedFuseError(
                "FluidInstadappSupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID,
            self._f_usdc_address,
        )
        return self._fluid_instadapp_pool_fuse.supply_and_stake(market_id, amount)

    def unstake_and_withdraw(self, amount: int) -> List[FuseAction]:
        if self._fluid_instadapp_pool_fuse is None:
            raise UnsupportedFuseError(
                "FluidInstadappSupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID,
            self._f_usdc_address,
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

        raise UnsupportedChainId("Chain ID not supported")

    def get_FluidLendingStakingRewardsUsdc(self) -> ChecksumAddress:
        if self._chain_id == 42161:
            return Web3.to_checksum_address(
                "0x48f89d731C5e3b5BeE8235162FC2C639Ba62DB7d"
            )
        if self._chain_id == 8453:
            return Web3.to_checksum_address(
                "0x48f89d731C5e3b5BeE8235162FC2C639Ba62DB7d"
            )

        raise UnsupportedChainId("Chain ID not supported")

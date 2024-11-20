from typing import List

from web3 import Web3

from ipor_fusion.ERC20 import ERC20
from ipor_fusion.MarketId import MarketId
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FluidInstadappSupplyFuse import FluidInstadappSupplyFuse
from ipor_fusion.fuse.FuseAction import FuseAction


class FluidInstadappMarket:
    FLUID_INSTADAPP_POOL_USDC = Web3.to_checksum_address(
        "0x1a996cb54bb95462040408c06122d45d6cdb6096"
    )
    FLUID_INSTADAPP_STAKING_POOL_USDC = Web3.to_checksum_address(
        "0x48f89d731C5e3b5BeE8235162FC2C639Ba62DB7d"
    )

    FLUID_INSTADAPP_POOL_FUSE = Web3.to_checksum_address(
        "0x0eA739e6218F67dF51d1748Ee153ae7B9DCD9a25"
    )
    FLUID_INSTADAPP_CLAIM_FUSE = Web3.to_checksum_address(
        "0x12F86cE5a2B95328c882e6A106dE775b04a131bA"
    )
    FLUID_INSTADAPP_STAKING_FUSE = Web3.to_checksum_address(
        "0x962A7F0A2CbE97d4004175036A81E643463b76ec"
    )

    def __init__(self, transaction_executor: TransactionExecutor, fuses: List[str]):
        self._transaction_executor = transaction_executor
        self._pool = ERC20(transaction_executor, self.FLUID_INSTADAPP_POOL_USDC)
        self._staking_pool = ERC20(
            transaction_executor, self.FLUID_INSTADAPP_STAKING_POOL_USDC
        )

        self._any_fuse_supported = False
        for fuse in fuses:
            checksum_fuse = Web3.to_checksum_address(fuse)
            if checksum_fuse == self.FLUID_INSTADAPP_POOL_FUSE:
                self._fluid_instadapp_pool_fuse = FluidInstadappSupplyFuse(
                    self.FLUID_INSTADAPP_POOL_USDC,
                    self.FLUID_INSTADAPP_POOL_FUSE,
                    self.FLUID_INSTADAPP_STAKING_POOL_USDC,
                    self.FLUID_INSTADAPP_STAKING_FUSE,
                )
                self._any_fuse_supported = True

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
            FluidInstadappSupplyFuse.PROTOCOL_ID, self.FLUID_INSTADAPP_POOL_USDC
        )
        return self._fluid_instadapp_pool_fuse.supply_and_stake(market_id, amount)

    def unstake_and_withdraw(self, amount: int) -> List[FuseAction]:
        if not hasattr(self, "_fluid_instadapp_pool_fuse"):
            raise UnsupportedFuseError(
                "FluidInstadappSupplyFuse is not supported by PlasmaVault"
            )

        market_id = MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID, self.FLUID_INSTADAPP_POOL_USDC
        )
        return self._fluid_instadapp_pool_fuse.unstake_and_withdraw(market_id, amount)

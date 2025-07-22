import logging

from eth_typing import ChecksumAddress
from web3.exceptions import ContractLogicError

from ipor_fusion.AccessManager import AccessManager
from ipor_fusion.CheatingTransactionExecutor import CheatingTransactionExecutor
from ipor_fusion.ERC20 import ERC20
from ipor_fusion.FuseMapper import FuseMapper
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.PriceOracleMiddleware import PriceOracleMiddleware
from ipor_fusion.RewardsClaimManager import RewardsClaimManager
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.WithdrawManager import WithdrawManager
from ipor_fusion.error.UnsupportedMarketError import UnsupportedMarketError
from ipor_fusion.markets.AaveV3Market import AaveV3Market
from ipor_fusion.markets.CompoundV3Market import CompoundV3Market
from ipor_fusion.markets.ERC4626Market import ERC4626Market
from ipor_fusion.markets.FluidInstadappMarket import FluidInstadappMarket
from ipor_fusion.markets.GearboxV3Market import GearboxV3Market
from ipor_fusion.markets.MorphoMarket import MorphoMarket
from ipor_fusion.markets.RamsesV2Market import RamsesV2Market
from ipor_fusion.markets.UniswapV3Market import UniswapV3Market
from ipor_fusion.markets.UniversalMarket import UniversalMarket

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


class PlasmaSystem:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        chain_id: int,
        plasma_vault_address: ChecksumAddress,
        withdraw_manager_address: ChecksumAddress = None,
    ):
        if not transaction_executor:
            raise ValueError("transaction_executor is required")
        if not chain_id:
            raise ValueError("chain_id is required")
        if not plasma_vault_address:
            raise ValueError("plasma_vault_address is required")

        self._transaction_executor = transaction_executor
        self._chain_id = chain_id
        self._plasma_vault_address = plasma_vault_address
        self._withdraw_manager_address = withdraw_manager_address

    def cheater(self, cheating_address: ChecksumAddress):
        web3 = self._transaction_executor.get_web3()
        cheating_transaction_executor = CheatingTransactionExecutor(
            web3, cheating_address
        )
        return PlasmaSystem(
            transaction_executor=cheating_transaction_executor,
            chain_id=self._chain_id,
            plasma_vault_address=self._plasma_vault_address,
            withdraw_manager_address=self._withdraw_manager_address,
        )

    def transaction_executor(self) -> TransactionExecutor:
        return self._transaction_executor

    def plasma_vault(self) -> PlasmaVault:
        return PlasmaVault(
            transaction_executor=self._transaction_executor,
            plasma_vault_address=self._plasma_vault_address,
        )

    def access_manager(self) -> AccessManager:
        return AccessManager(
            transaction_executor=self._transaction_executor,
            access_manager_address=self.plasma_vault().get_access_manager_address(),
        )

    def withdraw_manager(self) -> WithdrawManager:
        if not self._withdraw_manager_address:
            self._withdraw_manager_address = (
                self.plasma_vault().withdraw_manager_address()
            )

        return WithdrawManager(
            transaction_executor=self._transaction_executor,
            withdraw_manager_address=self._withdraw_manager_address,
        )

    def rewards_claim_manager(self) -> RewardsClaimManager:
        return RewardsClaimManager(
            transaction_executor=self._transaction_executor,
            rewards_claim_manager_address=self.plasma_vault().get_rewards_claim_manager_address(),
        )

    def price_oracle_middleware(self) -> PriceOracleMiddleware:
        return PriceOracleMiddleware(
            transaction_executor=self._transaction_executor,
            price_oracle_middleware_address=self.plasma_vault().get_price_oracle_middleware_address(),
        )

    def erc20(self, asset_address: str) -> ERC20:
        return ERC20(
            transaction_executor=self._transaction_executor,
            asset_address=asset_address,
        )

    def alpha(self) -> ChecksumAddress:
        return self._transaction_executor.get_account_address()

    def uniswap_v3(self) -> UniswapV3Market:
        uniswap_v3_market = UniswapV3Market(
            chain_id=self._chain_id, fuses=self.plasma_vault().get_fuses()
        )
        if not uniswap_v3_market.is_market_supported():
            raise UnsupportedMarketError(
                "Uniswap V3 Market is not supported by PlasmaVault"
            )
        return uniswap_v3_market

    def ramses_v2(self) -> RamsesV2Market:
        rewards_fuses = []
        try:
            rewards_fuses = self.rewards_claim_manager().get_rewards_fuses()
        except ContractLogicError as e:
            log.warning("Failed to get rewards fuses: %s", e)

        ramses_v2_market = RamsesV2Market(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            rewards_claim_manager=self.rewards_claim_manager(),
            fuses=self.plasma_vault().get_fuses(),
            rewards_fuses=rewards_fuses,
        )

        if not ramses_v2_market.is_market_supported():
            raise UnsupportedMarketError(
                "Ramses V2 Market is not supported by PlasmaVault"
            )

        return ramses_v2_market

    def gearbox_v3(self) -> GearboxV3Market:
        gearbox_v3_market = GearboxV3Market(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            fuses=self.plasma_vault().get_fuses(),
        )

        if not gearbox_v3_market.is_market_supported():
            raise UnsupportedMarketError(
                "Gearbox V3 Market is not supported by PlasmaVault"
            )
        return gearbox_v3_market

    def fluid_instadapp(
        self,
        f_usdc_address: ChecksumAddress = None,
        fluid_lending_staking_rewards_usdc_address: ChecksumAddress = None,
        erc_4626_supply_fuse_market_id_5_address: ChecksumAddress = None,
        fluid_instadapp_staking_supply_fuse_address: ChecksumAddress = None,
    ) -> FluidInstadappMarket:
        if erc_4626_supply_fuse_market_id_5_address is None:
            erc_4626_supply_fuse_market_id_5_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="Erc4626SupplyFuseMarketId5",
                fuses=self.plasma_vault().get_fuses(),
            )

        if fluid_instadapp_staking_supply_fuse_address is None:
            fluid_instadapp_staking_supply_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="FluidInstadappStakingSupplyFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        return FluidInstadappMarket(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            f_usdc_address=f_usdc_address,
            fluid_lending_staking_rewards_usdc_address=fluid_lending_staking_rewards_usdc_address,
            erc_4626_supply_fuse_market_id_5_address=erc_4626_supply_fuse_market_id_5_address,
            fluid_instadapp_staking_supply_fuse_address=fluid_instadapp_staking_supply_fuse_address,
        )

    def aave_v3(
        self,
        aave_v3_supply_fuse_address: ChecksumAddress = None,
        aave_v3_borrow_fuse_address: ChecksumAddress = None,
    ) -> AaveV3Market:
        if aave_v3_supply_fuse_address is None:
            aave_v3_supply_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="AaveV3SupplyFuse",
                fuses=self.plasma_vault().get_fuses(),
            )
        if aave_v3_borrow_fuse_address is None:
            aave_v3_borrow_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="AaveV3BorrowFuse",
                fuses=self.plasma_vault().get_fuses(),
            )
        return AaveV3Market(
            transaction_executor=self._transaction_executor,
            aave_v3_supply_fuse_address=aave_v3_supply_fuse_address,
            aave_v3_borrow_fuse_address=aave_v3_borrow_fuse_address,
        )

    def morpho(self) -> MorphoMarket:
        morpho_market = MorphoMarket(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            fuses=self.plasma_vault().get_fuses(),
        )

        if not morpho_market.is_market_supported():
            raise UnsupportedMarketError(
                "Morpho Blue Market is not supported by PlasmaVault"
            )
        return morpho_market

    def compound_v3(
        self, compound_v3_supply_fuse_address: ChecksumAddress = None
    ) -> CompoundV3Market:
        if compound_v3_supply_fuse_address is None:
            compound_v3_supply_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="CompoundV3SupplyFuse",
                fuses=self.plasma_vault().get_fuses(),
            )
        return CompoundV3Market(
            transaction_executor=self._transaction_executor,
            compound_v3_supply_fuse_address=compound_v3_supply_fuse_address,
        )

    def universal(self) -> UniversalMarket:
        universal_market = UniversalMarket(
            chain_id=self._chain_id,
            fuses=self.plasma_vault().get_fuses(),
            transaction_executor=self._transaction_executor,
            plasma_vault=self.plasma_vault(),
        )

        if not universal_market.is_market_supported():
            raise UnsupportedMarketError(
                "Universal Market is not supported by PlasmaVault"
            )
        return universal_market

    def erc4626(self, fuse_address: ChecksumAddress) -> ERC4626Market:
        if not fuse_address:
            raise ValueError("fuse_address is required")
        erc4626_market = ERC4626Market(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            fuse_address=fuse_address,
        )
        return erc4626_market

    def prank(self, address: ChecksumAddress):
        self._transaction_executor.prank(address)

    def chain_id(self):
        return self._chain_id

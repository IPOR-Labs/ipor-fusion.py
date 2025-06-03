import logging

from eth_typing import ChecksumAddress
from web3.exceptions import ContractLogicError

from ipor_fusion.AccessManager import AccessManager
from ipor_fusion.CheatingTransactionExecutor import CheatingTransactionExecutor
from ipor_fusion.ERC20 import ERC20
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.PriceOracleMiddleware import PriceOracleMiddleware
from ipor_fusion.RewardsClaimManager import RewardsClaimManager
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.WithdrawManager import WithdrawManager
from ipor_fusion.error.UnsupportedMarketError import UnsupportedMarketError
from ipor_fusion.markets.AaveV3Market import AaveV3Market
from ipor_fusion.markets.CompoundV3Market import CompoundV3Market
from ipor_fusion.markets.FluidInstadappMarket import FluidInstadappMarket
from ipor_fusion.markets.GearboxV3Market import GearboxV3Market
from ipor_fusion.markets.MorphoBlueMarket import MorphoBlueMarket
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

    def fluid_instadapp(self) -> FluidInstadappMarket:
        fluid_instadapp_market = FluidInstadappMarket(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            fuses=self.plasma_vault().get_fuses(),
        )

        if not fluid_instadapp_market.is_market_supported():
            raise UnsupportedMarketError(
                "Fluid Instadapp Market is not supported by PlasmaVault"
            )
        return fluid_instadapp_market

    def aave_v3(self) -> AaveV3Market:
        aave_v3_market = AaveV3Market(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            fuses=self.plasma_vault().get_fuses(),
        )

        if not aave_v3_market.is_market_supported():
            raise UnsupportedMarketError(
                "Aave V3 Market is not supported by PlasmaVault"
            )
        return aave_v3_market

    def morpho_blue(self) -> MorphoBlueMarket:
        morpho_blue_market = MorphoBlueMarket(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            fuses=self.plasma_vault().get_fuses(),
        )

        if not morpho_blue_market.is_market_supported():
            raise UnsupportedMarketError(
                "Morpho Blue Market is not supported by PlasmaVault"
            )
        return morpho_blue_market

    def compound_v3(self) -> CompoundV3Market:
        compound_v3_market = CompoundV3Market(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            fuses=self.plasma_vault().get_fuses(),
        )

        if not compound_v3_market.is_market_supported():
            raise UnsupportedMarketError(
                "Compound V3 Market is not supported by PlasmaVault"
            )
        return compound_v3_market

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

    def prank(self, address: ChecksumAddress):
        self._transaction_executor.prank(address)

    def chain_id(self):
        return self._chain_id

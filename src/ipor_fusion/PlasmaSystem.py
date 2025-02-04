import logging
from typing import Optional

from web3.exceptions import ContractLogicError

from ipor_fusion.AccessManager import AccessManager
from ipor_fusion.AssetMapper import AssetMapper
from ipor_fusion.ERC20 import ERC20
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.PlasmaVaultDataReader import PlasmaVaultData
from ipor_fusion.PriceOracleMiddleware import PriceOracleMiddleware
from ipor_fusion.RewardsClaimManager import RewardsClaimManager
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.WithdrawManager import WithdrawManager
from ipor_fusion.error.UnsupportedAsset import UnsupportedAsset
from ipor_fusion.error.UnsupportedMarketError import UnsupportedMarketError
from ipor_fusion.markets.AaveV3Market import AaveV3Market
from ipor_fusion.markets.CompoundV3Market import CompoundV3Market
from ipor_fusion.markets.FluidInstadappMarket import FluidInstadappMarket
from ipor_fusion.markets.GearboxV3Market import GearboxV3Market
from ipor_fusion.markets.RamsesV2Market import RamsesV2Market
from ipor_fusion.markets.UniswapV3Market import UniswapV3Market
from ipor_fusion.markets.UniversalMarket import UniversalMarket

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


# pylint: disable=too-many-instance-attributes
class PlasmaSystem:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        chain_id: int,
        plasma_vault_data: PlasmaVaultData,
    ):
        self._transaction_executor = transaction_executor
        self._chain_id = chain_id
        self._plasma_vault_data = plasma_vault_data

        self._plasma_vault = PlasmaVault(
            transaction_executor=transaction_executor,
            plasma_vault_address=plasma_vault_data.plasma_vault_address,
        )
        self._access_manager = AccessManager(
            transaction_executor=transaction_executor,
            access_manager_address=plasma_vault_data.access_manager_address,
        )
        self._withdraw_manager = WithdrawManager(
            transaction_executor=transaction_executor,
            withdraw_manager_address=plasma_vault_data.withdraw_manager_address,
        )
        self._rewards_claim_manager = RewardsClaimManager(
            transaction_executor=transaction_executor,
            rewards_claim_manager_address=plasma_vault_data.rewards_claim_manager_address,
        )
        self._price_oracle_middleware = PriceOracleMiddleware(
            transaction_executor=transaction_executor,
            price_oracle_middleware_address=plasma_vault_data.price_oracle_middleware_address,
        )
        self._fuses = self._plasma_vault.get_fuses()
        self._uniswap_v3_market = UniswapV3Market(chain_id=chain_id, fuses=self._fuses)
        self._rewards_fuses = []
        try:
            self._rewards_fuses = self._rewards_claim_manager.get_rewards_fuses()
        except ContractLogicError as e:
            log.warning("Failed to get rewards fuses: %s", e)
        self._ramses_v2_market = RamsesV2Market(
            chain_id=chain_id,
            transaction_executor=self._transaction_executor,
            rewards_claim_manager=self._rewards_claim_manager,
            fuses=self._fuses,
            rewards_fuses=self._rewards_fuses,
        )
        self._universal_market = UniversalMarket(chain_id=chain_id, fuses=self._fuses)
        self._gearbox_v3_market = GearboxV3Market(
            chain_id=chain_id,
            transaction_executor=self._transaction_executor,
            fuses=self._fuses,
        )
        self._fluid_instadapp_market = FluidInstadappMarket(
            chain_id=chain_id,
            transaction_executor=self._transaction_executor,
            fuses=self._fuses,
        )
        self._aave_v3_market = AaveV3Market(
            chain_id=chain_id,
            transaction_executor=self._transaction_executor,
            fuses=self._fuses,
        )
        self._compound_v3_market = CompoundV3Market(
            chain_id=chain_id,
            transaction_executor=self._transaction_executor,
            fuses=self._fuses,
        )
        self._usdc = self._initialize_asset(asset_symbol="USDC")
        self._usdt = self._initialize_asset(asset_symbol="USDT")
        self._weth = self._initialize_asset(asset_symbol="WETH")
        self._cbBTC = self._initialize_asset(asset_symbol="cbBTC")
        self._pepe = self._initialize_asset(asset_symbol="PEPE")

    def transaction_executor(self) -> TransactionExecutor:
        return self._transaction_executor

    def plasma_vault(self) -> PlasmaVault:
        return self._plasma_vault

    def access_manager(self) -> AccessManager:
        return self._access_manager

    def withdraw_manager(self) -> WithdrawManager:
        return self._withdraw_manager

    def rewards_claim_manager(self) -> RewardsClaimManager:
        return self._rewards_claim_manager

    def price_oracle_middleware(self) -> PriceOracleMiddleware:
        return self._price_oracle_middleware

    def usdc(self) -> ERC20:
        if not self._usdc:
            raise UnsupportedAsset()
        return self._usdc

    def cbBTC(self) -> ERC20:
        if not self._cbBTC:
            raise UnsupportedAsset()
        return self._cbBTC

    def usdt(self) -> ERC20:
        if not self._usdt:
            raise UnsupportedAsset()
        return self._usdt

    def weth(self) -> ERC20:
        if not self._weth:
            raise UnsupportedAsset()
        return self._weth

    def pepe(self) -> ERC20:
        if not self._pepe:
            raise UnsupportedAsset()
        return self._pepe

    def alpha(self) -> str:
        return self._transaction_executor.get_account_address()

    def uniswap_v3(self) -> UniswapV3Market:
        if not self._uniswap_v3_market.is_market_supported():
            raise UnsupportedMarketError(
                "Uniswap V3 Market is not supported by PlasmaVault"
            )
        return self._uniswap_v3_market

    def ramses_v2(self) -> RamsesV2Market:
        if not self._ramses_v2_market.is_market_supported():
            raise UnsupportedMarketError(
                "Ramses V2 Market is not supported by PlasmaVault"
            )
        return self._ramses_v2_market

    def gearbox_v3(self) -> GearboxV3Market:
        if not self._gearbox_v3_market.is_market_supported():
            raise UnsupportedMarketError(
                "Gearbox V3 Market is not supported by PlasmaVault"
            )
        return self._gearbox_v3_market

    def fluid_instadapp(self) -> FluidInstadappMarket:
        if not self._fluid_instadapp_market.is_market_supported():
            raise UnsupportedMarketError(
                "Fluid Instadapp Market is not supported by PlasmaVault"
            )
        return self._fluid_instadapp_market

    def aave_v3(self) -> AaveV3Market:
        if not self._aave_v3_market.is_market_supported():
            raise UnsupportedMarketError(
                "Aave V3 Market is not supported by PlasmaVault"
            )
        return self._aave_v3_market

    def compound_v3(self) -> CompoundV3Market:
        if not self._compound_v3_market.is_market_supported():
            raise UnsupportedMarketError(
                "Compound V3 Market is not supported by PlasmaVault"
            )
        return self._compound_v3_market

    def universal(self) -> UniversalMarket:
        if not self._universal_market.is_market_supported():
            raise UnsupportedMarketError(
                "Universal Market is not supported by PlasmaVault"
            )
        return self._universal_market

    def prank(self, address: str):
        self._transaction_executor.prank(address)

    def chain_id(self):
        return self._chain_id

    def _initialize_asset(self, asset_symbol: str) -> Optional[ERC20]:
        try:
            asset_address = AssetMapper.map(
                chain_id=self._chain_id, asset_symbol=asset_symbol
            )
            if asset_address:
                return ERC20(
                    transaction_executor=self._transaction_executor,
                    asset_address=asset_address,
                )
        except UnsupportedAsset:
            log.debug("Unsupported asset: %s", asset_symbol)

        return None

    def _initialize_rewards_fuses(self) -> list:
        try:
            return self._rewards_claim_manager.get_rewards_fuses()
        except ContractLogicError:
            return []

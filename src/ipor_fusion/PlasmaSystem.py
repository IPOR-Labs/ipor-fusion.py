from web3.exceptions import ContractLogicError

from ipor_fusion.AccessManager import AccessManager
from ipor_fusion.ERC20 import ERC20
from ipor_fusion.ExternalSystemsDataProvider import ExternalSystemsData
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.PlasmaVaultDataReader import PlasmaVaultData
from ipor_fusion.RewardsClaimManager import RewardsClaimManager
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.WithdrawManager import WithdrawManager
from ipor_fusion.error.UnsupportedMarketError import UnsupportedMarketError
from ipor_fusion.markets.AaveV3Market import AaveV3Market
from ipor_fusion.markets.CompoundV3Market import CompoundV3Market
from ipor_fusion.markets.FluidInstadappMarket import FluidInstadappMarket
from ipor_fusion.markets.GearboxV3Market import GearboxV3Market
from ipor_fusion.markets.RamsesV2Market import RamsesV2Market
from ipor_fusion.markets.UniswapV3Market import UniswapV3Market
from ipor_fusion.markets.UniversalMarket import UniversalMarket


# pylint: disable=too-many-instance-attributes
class PlasmaSystem:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        chain_id: int,
        plasma_vault_data: PlasmaVaultData,
        external_systems_data: ExternalSystemsData,
    ):
        self._transaction_executor = transaction_executor
        self._chain_id = chain_id
        self._plasma_vault_data = plasma_vault_data
        self._external_systems_data = external_systems_data

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
        self._usdc = ERC20(
            transaction_executor=transaction_executor,
            asset_address=external_systems_data.usdc_address,
        )
        self._usdt = ERC20(
            transaction_executor=transaction_executor,
            asset_address=external_systems_data.usdt_address,
        )
        self._fuses = self._plasma_vault.get_fuses()
        self._uniswap_v3_market = UniswapV3Market(fuses=self._fuses)
        self._rewards_fuses = []
        try:
            self._rewards_fuses = self._rewards_claim_manager.get_rewards_fuses()
        except ContractLogicError as e:
            print(f"Failed to get rewards fuses: {e}")
        self._ramses_v2_market = RamsesV2Market(
            transaction_executor=self._transaction_executor,
            rewards_claim_manager=self._rewards_claim_manager,
            fuses=self._fuses,
            rewards_fuses=self._rewards_fuses,
        )
        self._universal_market = UniversalMarket(fuses=self._fuses)
        self._gearbox_v3_market = GearboxV3Market(
            transaction_executor=self._transaction_executor,
            fuses=self._fuses,
        )
        self._fluid_instadapp_market = FluidInstadappMarket(
            transaction_executor=self._transaction_executor,
            fuses=self._fuses,
        )
        self._aave_v3_market = AaveV3Market(
            transaction_executor=self._transaction_executor,
            fuses=self._fuses,
        )
        self._compound_v3_market = CompoundV3Market(
            transaction_executor=self._transaction_executor,
            fuses=self._fuses,
        )
        self._weth = ERC20(
            transaction_executor=transaction_executor,
            asset_address=external_systems_data.weth_address,
        )

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

    def usdc(self) -> ERC20:
        return self._usdc

    def usdt(self) -> ERC20:
        return self._usdt

    def weth(self) -> ERC20:
        return self._weth

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

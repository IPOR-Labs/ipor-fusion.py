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
from ipor_fusion.markets.AaveV3Market import AaveV3Market
from ipor_fusion.markets.CompoundV3Market import CompoundV3Market
from ipor_fusion.markets.ERC4626Market import ERC4626Market
from ipor_fusion.markets.FluidInstadappMarket import FluidInstadappMarket
from ipor_fusion.markets.GearboxV3Market import GearboxV3Market
from ipor_fusion.markets.MorphoMarket import MorphoMarket
from ipor_fusion.markets.RamsesV2Market import RamsesV2Market
from ipor_fusion.markets.UniswapV3Market import UniswapV3Market
from ipor_fusion.markets.UniversalMarket import UniversalMarket

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

    def uniswap_v3(
        self,
        uniswap_v_3_swap_fuse: ChecksumAddress = None,
        uniswap_v_3_new_position_fuse: ChecksumAddress = None,
        uniswap_v_3_modify_position_fuse: ChecksumAddress = None,
        uniswap_v_3_collect_fuse: ChecksumAddress = None,
    ) -> UniswapV3Market:

        if uniswap_v_3_swap_fuse is None:
            uniswap_v_3_swap_fuse = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="UniswapV3SwapFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        if uniswap_v_3_new_position_fuse is None:
            uniswap_v_3_new_position_fuse = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="UniswapV3NewPositionFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        if uniswap_v_3_modify_position_fuse is None:
            uniswap_v_3_modify_position_fuse = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="UniswapV3ModifyPositionFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        if uniswap_v_3_collect_fuse is None:
            uniswap_v_3_collect_fuse = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="UniswapV3CollectFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        return UniswapV3Market(
            chain_id=self._chain_id,
            uniswap_v_3_swap_fuse=uniswap_v_3_swap_fuse,
            uniswap_v_3_new_position_fuse=uniswap_v_3_new_position_fuse,
            uniswap_v_3_modify_position_fuse=uniswap_v_3_modify_position_fuse,
            uniswap_v_3_collect_fuse=uniswap_v_3_collect_fuse,
        )

    def ramses_v2(
        self,
        ramses_v_2_new_position_fuse_address: ChecksumAddress = None,
        ramses_v_2_modify_position_fuse_address: ChecksumAddress = None,
        ramses_v_2_collect_fuse_address: ChecksumAddress = None,
        ramses_v_2_claim_fuse_address: ChecksumAddress = None,
    ) -> RamsesV2Market:

        if ramses_v_2_new_position_fuse_address is None:
            ramses_v_2_new_position_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="RamsesV2NewPositionFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        if ramses_v_2_modify_position_fuse_address is None:
            ramses_v_2_modify_position_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="RamsesV2ModifyPositionFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        if ramses_v_2_collect_fuse_address is None:
            ramses_v_2_collect_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="RamsesV2CollectFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        rewards_fuses = []
        try:
            rewards_fuses = self.rewards_claim_manager().get_rewards_fuses()
        except ContractLogicError:
            log.warning("Failed to get rewards fuses")

        if ramses_v_2_claim_fuse_address is None:
            ramses_v_2_claim_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="RamsesClaimFuse",
                fuses=rewards_fuses,
            )

        if not rewards_fuses:
            for rewards_fuse in FuseMapper.map(self._chain_id, "RamsesClaimFuse"):
                if self.rewards_claim_manager().is_reward_fuse_supported(rewards_fuse):
                    ramses_v_2_claim_fuse_address = rewards_fuse

        return RamsesV2Market(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            ramses_v_2_new_position_fuse_address=ramses_v_2_new_position_fuse_address,
            ramses_v_2_modify_position_fuse_address=ramses_v_2_modify_position_fuse_address,
            ramses_v_2_collect_fuse_address=ramses_v_2_collect_fuse_address,
            ramses_v_2_claim_fuse_address=ramses_v_2_claim_fuse_address,
        )

    def gearbox_v3(
        self,
        d_usdcv_3_address: ChecksumAddress = None,
        erc_4626_supply_fuse_market_id_3_address: ChecksumAddress = None,
        gearbox_v3_farm_supply_fuse_address: ChecksumAddress = None,
        farmd_usdcv_3_address: ChecksumAddress = None,
    ) -> GearboxV3Market:

        if erc_4626_supply_fuse_market_id_3_address is None:
            erc_4626_supply_fuse_market_id_3_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="Erc4626SupplyFuseMarketId3",
                fuses=self.plasma_vault().get_fuses(),
            )

        if gearbox_v3_farm_supply_fuse_address is None:
            gearbox_v3_farm_supply_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="GearboxV3FarmSupplyFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        return GearboxV3Market(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            d_usdcv_3_address=d_usdcv_3_address,
            farmd_usdcv_3_address=farmd_usdcv_3_address,
            erc_4626_supply_fuse_market_id_3_address=erc_4626_supply_fuse_market_id_3_address,
            gearbox_v3_farm_supply_fuse_address=gearbox_v3_farm_supply_fuse_address,
        )

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

    def morpho(
        self,
        morpho_supply_fuse_address: ChecksumAddress = None,
        morpho_flash_loan_fuse_address: ChecksumAddress = None,
        morpho_blue_claim_fuse_address: ChecksumAddress = None,
    ) -> MorphoMarket:

        if morpho_supply_fuse_address is None:
            morpho_supply_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="MorphoSupplyFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        if morpho_flash_loan_fuse_address is None:
            morpho_flash_loan_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="MorphoFlashLoanFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        if morpho_blue_claim_fuse_address is None:
            morpho_blue_claim_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="MorphoClaimFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        return MorphoMarket(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            plasma_vault_address=self._plasma_vault_address,
            morpho_supply_fuse_address=morpho_supply_fuse_address,
            morpho_flash_loan_fuse_address=morpho_flash_loan_fuse_address,
            morpho_blue_claim_fuse_address=morpho_blue_claim_fuse_address,
        )

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

    def universal(
        self, universal_token_swapper_fuse_address: ChecksumAddress = None
    ) -> UniversalMarket:
        if universal_token_swapper_fuse_address is None:
            universal_token_swapper_fuse_address = FuseMapper.find(
                chain_id=self._chain_id,
                fuse_name="UniversalTokenSwapperFuse",
                fuses=self.plasma_vault().get_fuses(),
            )

        return UniversalMarket(
            chain_id=self._chain_id,
            transaction_executor=self._transaction_executor,
            universal_token_swapper_fuse_address=universal_token_swapper_fuse_address,
        )

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

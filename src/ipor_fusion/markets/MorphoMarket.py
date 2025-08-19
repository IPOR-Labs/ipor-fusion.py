from typing import List

from eth_typing import ChecksumAddress, ChainId
from web3 import Web3

from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.fuse.MorphoBlueClaimFuse import MorphoBlueClaimFuse
from ipor_fusion.fuse.MorphoBlueSupplyFuse import MorphoBlueSupplyFuse
from ipor_fusion.fuse.MorphoFlashLoanFuse import MorphoFlashLoanFuse
from ipor_fusion.markets.morpho.MorphoContract import MorphoContract
from ipor_fusion.types import Amount, MorphoBlueMarketId


class MorphoMarket:

    class MorphoPosition:
        supply_amount: Amount
        borrow_amount: Amount
        collateral_amount: Amount

        def __init__(
            self,
            supply_amount: Amount,
            borrow_amount: Amount,
            collateral_amount: Amount,
        ):
            self.supply_amount = supply_amount
            self.borrow_amount = borrow_amount
            self.collateral_amount = collateral_amount

    def __init__(
        self,
        chain_id: int,
        transaction_executor: TransactionExecutor,
        plasma_vault_address: ChecksumAddress,
        morpho_supply_fuse_address: ChecksumAddress,
        morpho_flash_loan_fuse_address: ChecksumAddress,
        morpho_blue_claim_fuse_address: ChecksumAddress,
    ):
        if transaction_executor is None:
            raise ValueError("transaction_executor is required")

        self._chain_id = chain_id
        self._transaction_executor = transaction_executor
        self._plasma_vault_address = plasma_vault_address

        self._morpho_flash_loan_fuse = morpho_flash_loan_fuse_address

        self._morpho_blue_supply_fuse = MorphoBlueSupplyFuse(morpho_supply_fuse_address)
        self._morpho_flash_loan_fuse = MorphoFlashLoanFuse(
            morpho_flash_loan_fuse_address
        )
        self._morpho_blue_claim_fuse = MorphoBlueClaimFuse(
            morpho_blue_claim_fuse_address
        )

    def supply(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        if self._morpho_blue_supply_fuse is None:
            raise UnsupportedFuseError(
                "MorphoBlueSupplyFuse is not supported by PlasmaVault"
            )
        return self._morpho_blue_supply_fuse.supply(market_id, amount)

    def position(
        self, chain_id: ChainId, morpho_blue_market_id: MorphoBlueMarketId
    ) -> MorphoPosition:
        morpho = MorphoContract(
            transaction_executor=self._transaction_executor,
            address=MorphoMarket.get_morpho(chain_id),
        )

        market = morpho.market(morpho_blue_market_id)
        position = morpho.position(morpho_blue_market_id, self._plasma_vault_address)

        borrow_amount = 0
        if market.total_borrow_shares > 0:
            borrow_amount = (
                position.borrow_shares
                * market.total_borrow_assets
                / market.total_borrow_shares
            )

        supply_amount = 0
        if market.total_borrow_shares > 0:
            supply_amount = (
                position.supply_shares
                * market.total_supply_assets
                / market.total_supply_shares
            )

        return MorphoMarket.MorphoPosition(
            supply_amount, borrow_amount, position.collateral
        )

    def withdraw(self, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        if self._morpho_blue_supply_fuse is None:
            raise UnsupportedFuseError(
                "MorphoBlueSupplyFuse is not supported by PlasmaVault"
            )
        return self._morpho_blue_supply_fuse.withdraw(market_id, amount)

    def flash_loan(
        self, asset_address: ChecksumAddress, amount: Amount, actions: List[FuseAction]
    ) -> FuseAction:
        if self._morpho_flash_loan_fuse is None:
            raise UnsupportedFuseError(
                "MorphoFlashLoanFuse is not supported by PlasmaVault"
            )
        return self._morpho_flash_loan_fuse.flash_loan(asset_address, amount, actions)

    @staticmethod
    def get_morpho(chain_id: ChainId) -> ChecksumAddress:
        """
        Get the Morpho protocol address for a given chain ID.

        Args:
            chain_id: The blockchain network chain ID

        Returns:
            str: The Morpho protocol contract address

        Raises:
            ValueError: If the chain ID is not supported
        """
        if chain_id == 1:  # Ethereum mainnet
            return Web3.to_checksum_address(
                "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
            )

        if chain_id == 8453:  # Base network
            return Web3.to_checksum_address(
                "0xBBBBBbbBBb9cC5e90e3b3Af64bdAF62C37EEFFCb"
            )

        raise ValueError(f"Morpho address not found for chain id: {chain_id}")

    def claim_rewards(
        self, universal_rewards_distributor, rewards_token, claimable: int, proof
    ) -> FuseAction:
        if self._morpho_blue_claim_fuse is None:
            raise UnsupportedFuseError(
                "MorphoBlueClaimFuse is not supported by PlasmaVault"
            )
        return self._morpho_blue_claim_fuse.claim(
            universal_rewards_distributor, rewards_token, claimable, proof
        )

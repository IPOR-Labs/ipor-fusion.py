from dataclasses import dataclass

from web3 import Web3

from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.TransactionExecutor import TransactionExecutor


@dataclass
class PlasmaVaultData:
    plasma_vault_address: str
    access_manager_address: str
    withdraw_manager_address: str
    asset_address: str
    rewards_claim_manager_address: str
    fuses: [str]


class PlasmaVaultDataReader:
    def __init__(self, transaction_executor: TransactionExecutor):
        self._transaction_executor = transaction_executor

    def read(self, plasma_vault_address: str) -> PlasmaVaultData:
        plasma_vault = PlasmaVault(self._transaction_executor, plasma_vault_address)
        access_manager_address = Web3.to_checksum_address(
            plasma_vault.get_access_manager_address()
        )
        asset_address = Web3.to_checksum_address(
            plasma_vault.underlying_asset_address()
        )
        withdraw_manager_address_checksum = None
        withdraw_manager_address = plasma_vault.withdraw_manager_address()
        if withdraw_manager_address:
            withdraw_manager_address_checksum = Web3.to_checksum_address(
                withdraw_manager_address
            )
        rewards_claim_manager_address = Web3.to_checksum_address(
            plasma_vault.get_rewards_claim_manager_address()
        )
        fuses = plasma_vault.get_fuses()
        checksum_fuses = [Web3.to_checksum_address(fuse) for fuse in fuses]
        return PlasmaVaultData(
            plasma_vault_address=plasma_vault_address,
            access_manager_address=access_manager_address,
            withdraw_manager_address=withdraw_manager_address_checksum,
            asset_address=asset_address,
            rewards_claim_manager_address=rewards_claim_manager_address,
            fuses=checksum_fuses,
        )

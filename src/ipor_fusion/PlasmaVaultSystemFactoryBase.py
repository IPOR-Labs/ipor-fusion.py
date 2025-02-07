from eth_typing import ChecksumAddress

from ipor_fusion.PlasmaSystem import PlasmaSystem
from ipor_fusion.TransactionExecutor import TransactionExecutor


class PlasmaVaultSystemFactoryBase:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
    ):
        self._transaction_executor = transaction_executor

    def get(
        self,
        plasma_vault_address: ChecksumAddress,
        withdraw_manager_address: ChecksumAddress = None,
    ) -> PlasmaSystem:
        chain_id = self._transaction_executor.chain_id()
        return PlasmaSystem(
            transaction_executor=self._transaction_executor,
            chain_id=chain_id,
            plasma_vault_address=plasma_vault_address,
            withdraw_manager_address=withdraw_manager_address,
        )

from ipor_fusion.PlasmaSystem import PlasmaSystem
from ipor_fusion.PlasmaVaultDataReader import PlasmaVaultDataReader
from ipor_fusion.TransactionExecutor import TransactionExecutor


class PlasmaVaultSystemFactoryBase:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
    ):
        self._transaction_executor = transaction_executor

    def get(self, plasma_vault_address: str) -> PlasmaSystem:
        plasma_vault_data_reader = PlasmaVaultDataReader(self._transaction_executor)
        plasma_vault_data = plasma_vault_data_reader.read(plasma_vault_address)
        chain_id = self._transaction_executor.chain_id()
        return PlasmaSystem(
            transaction_executor=self._transaction_executor,
            chain_id=chain_id,
            plasma_vault_data=plasma_vault_data,
        )

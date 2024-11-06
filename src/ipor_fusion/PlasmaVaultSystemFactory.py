from web3 import Web3

from ipor_fusion.ExternalSystemsDataProvider import ExternalSystemsDataProvider
from ipor_fusion.PlasmaVaultDataReader import PlasmaVaultDataReader
from ipor_fusion.PlasmaSystem import PlasmaSystem
from ipor_fusion.TransactionExecutor import TransactionExecutor


class PlasmaVaultSystemFactory:

    def __init__(
        self,
        provider_url: str,
        alpha_private_key: str,
    ):
        self._provider_url = provider_url
        self._alpha_private_key = alpha_private_key

    def get(self, plasma_vault_address: str) -> PlasmaSystem:
        web3 = Web3(Web3.HTTPProvider(self._provider_url))
        transaction_executor = TransactionExecutor(web3, self._alpha_private_key)
        plasma_vault_data_reader = PlasmaVaultDataReader(transaction_executor)
        plasma_vault_data = plasma_vault_data_reader.read(plasma_vault_address)
        chain_id = web3.eth.chain_id
        external_systems_data_provider = ExternalSystemsDataProvider(
            transaction_executor, chain_id
        )
        external_systems_data = external_systems_data_provider.get()
        return PlasmaSystem(
            transaction_executor=transaction_executor,
            chain_id=chain_id,
            plasma_vault_data=plasma_vault_data,
            external_systems_data=external_systems_data,
        )

from ipor_fusion.AccessManager import AccessManager
from ipor_fusion.ERC20 import ERC20
from ipor_fusion.ExternalSystemsDataProvider import ExternalSystemsData
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.PlasmaVaultDataReader import PlasmaVaultData
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.WithdrawManager import WithdrawManager


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
            transaction_executor, plasma_vault_data.plasma_vault_address
        )
        self._access_manager = AccessManager(
            transaction_executor, plasma_vault_data.access_manager_address
        )
        self._withdraw_manager = WithdrawManager(
            transaction_executor, plasma_vault_data.withdraw_manager_address
        )
        self._usdc = ERC20(transaction_executor, external_systems_data.usdc_address)
        self._usdt = ERC20(transaction_executor, external_systems_data.usdt_address)

    def plasma_vault(self) -> PlasmaVault:
        return self._plasma_vault

    def access_manager(self):
        return self._access_manager

    def withdraw_manager(self):
        return self._withdraw_manager

    def usdc(self):
        return self._usdc

    def usdt(self):
        return self._usdt

    def alpha(self):
        return self._transaction_executor.get_account_address()

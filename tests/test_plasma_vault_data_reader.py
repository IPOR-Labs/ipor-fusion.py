import os

from web3 import Web3, HTTPProvider

from constants import ANVIL_WALLET_PRIVATE_KEY, ARBITRUM
from ipor_fusion.PlasmaVaultDataReader import PlasmaVaultDataReader
from ipor_fusion.TransactionExecutor import TransactionExecutor


def test_plasma_vault_data_reader():
    provider_url = os.getenv("ARBITRUM_PROVIDER_URL")
    web3 = Web3(HTTPProvider(provider_url))
    transaction_executor = TransactionExecutor(web3, ANVIL_WALLET_PRIVATE_KEY)

    plasma_vault_data = PlasmaVaultDataReader(transaction_executor).read(
        ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT
    )

    assert (
        plasma_vault_data.plasma_vault_address == ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT
    )
    assert (
        plasma_vault_data.withdraw_manager_address
        == ARBITRUM.PILOT.SCHEDULED.WITHDRAW_MANAGER
    )
    assert (
        plasma_vault_data.access_manager_address
        == ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER
    )

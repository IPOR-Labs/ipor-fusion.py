import os

from web3 import Web3, HTTPProvider

from ipor_fusion.PlasmaVaultDataReader import PlasmaVaultDataReader
from ipor_fusion.TransactionExecutor import TransactionExecutor
from tests import constants
from tests.constants import ANVIL_WALLET_PRIVATE_KEY


def test_plasma_vault_data_reader():
    provider_url = os.getenv("ARBITRUM_PROVIDER_URL")
    web3 = Web3(HTTPProvider(provider_url))
    transaction_executor = TransactionExecutor(web3, ANVIL_WALLET_PRIVATE_KEY)

    plasma_vault_data = PlasmaVaultDataReader(transaction_executor).read(
        constants.ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT
    )

    assert (
        plasma_vault_data.plasma_vault_address
        == constants.ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT
    )
    assert (
        plasma_vault_data.withdraw_manager_address
        == constants.ARBITRUM.PILOT.SCHEDULED.WITHDRAW_MANAGER
    )
    assert (
        plasma_vault_data.access_manager_address
        == constants.ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER
    )

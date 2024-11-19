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
    assert plasma_vault_data.withdraw_manager_address == Web3.to_checksum_address(
        "0x8066Ce248a0dC6E303f795B108b2572498B552B4"
    )
    assert plasma_vault_data.access_manager_address == Web3.to_checksum_address(
        "0xF9A6C0E19FDfc580453b2800A18d8faCF2E42933"
    )

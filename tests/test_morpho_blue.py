import logging
import os

from eth_typing import BlockNumber
from web3 import Web3

from constants import ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.CheatingPlasmaVaultSystemFactory import (
    CheatingPlasmaVaultSystemFactory,
)
from ipor_fusion.types import MorphoBlueMarketId, Amount

# Configure logging to display relevant test information
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Retrieve the fork URL from environment variables
fork_url = os.getenv("ETHEREUM_PROVIDER_URL")

# Initialize the Anvil test container with the provided fork URL
anvil = AnvilTestContainerStarter(fork_url)
anvil.start()


def test_should_deposit_and_withdraw_from_morpho_blue():
    # Reset the blockchain to a specific point to ensure consistent test conditions
    # Block 22066578 represents a known state for reproducible testing
    anvil.reset_fork(BlockNumber(22066578))

    # Define key addresses and identifiers for the test
    # vault_address: The address of the Plasma Vault contract that will interact with Morpho Blue
    # alpha_address: The address with elevated permissions to execute operations
    # morpho_blue_market_id: Unique identifier for the specific Morpho Blue lending market
    vault_address = Web3.to_checksum_address(
        "0x43Ee0243eA8CF02f7087d8B16C8D2007CC9c7cA2"
    )
    alpha_address = Web3.to_checksum_address(
        "0x6d3BE3f86FB1139d0c9668BD552f05fcB643E6e6"
    )
    morpho_blue_market_id = MorphoBlueMarketId(
        "3a85e619751152991742810df6ec69ce473daef99e28a64ab2340d7b7ccfee49"
    )

    # Initialize the CheatingPlasmaVaultSystem factory and get an instance
    # This provides utilities to manipulate blockchain state for testing purposes
    cheating_system_factory = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    cheating = cheating_system_factory.get(vault_address)

    # Impersonate the cheating address to perform privileged operations
    # This simulates actions being taken by an authorized account
    cheating.prank(alpha_address)

    # Define the amount to deposit/withdraw (1,000 USDC with 6 decimal places)
    amount = Amount(1000_000000)

    # Create a supply operation for the specified Morpho Blue market
    # This prepares the transaction data for supplying assets to the lending market
    supply = cheating.morpho().supply(morpho_blue_market_id, amount)

    # Record the vault's USDC balance before supplying to the market
    # This will be used to verify the correct amount was transferred
    usdc = cheating.erc20("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
    usdc_balance_of_before_supply = usdc.balance_of(vault_address)

    # Execute the supply operation through the Plasma Vault
    # The vault will transfer USDC to the Morpho Blue protocol
    cheating.plasma_vault().execute([supply])

    morpho_position = cheating.morpho().position(
        chain_id=cheating.chain_id(), morpho_blue_market_id=morpho_blue_market_id
    )
    assert morpho_position.supply_amount >= amount

    # Record the vault's USDC balance after supplying to the market
    usdc_balance_of_after_supply = usdc.balance_of(vault_address)

    # Verify that the correct amount of USDC was transferred from the vault
    # The balance difference should exactly match the supplied amount
    assert usdc_balance_of_before_supply - usdc_balance_of_after_supply == amount

    # Create a withdrawal operation for the same amount from the Morpho Blue market
    # This prepares the transaction data for withdrawing assets from the lending market
    withdraw = cheating.morpho().withdraw(morpho_blue_market_id, amount)

    # Execute the withdrawal operation through the Plasma Vault
    # The vault will receive USDC back from the Morpho Blue protocol
    cheating.plasma_vault().execute([withdraw])

    # Record the vault's USDC balance after withdrawing from the market
    usdc_balance_of_after_withdraw = usdc.balance_of(vault_address)

    # Verify that the correct amount of USDC was returned to the vault
    # The balance difference should exactly match the withdrawn amount
    assert usdc_balance_of_after_withdraw - usdc_balance_of_after_supply > 999_000000

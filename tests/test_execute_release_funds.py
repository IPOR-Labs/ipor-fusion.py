import logging
import os

from constants import ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.CheatingPlasmaVaultSystemFactory import (
    CheatingPlasmaVaultSystemFactory,
)
from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles

# Configure logging to display relevant test information
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Retrieve the fork URL from environment variables
fork_url = os.getenv("ARBITRUM_PROVIDER_URL")

# Initialize the Anvil test container with the provided fork URL
anvil = AnvilTestContainerStarter(fork_url)
anvil.start()


def test_should_release_funds():
    """
    Test that funds can be released appropriately from a Plasma Vault.

    This test evaluates the process of setting up the vault, performing a deposit,
    and making a successful withdrawal by simulating blockchain time changes.
    """

    # Reset the blockchain state to a specific block number for test consistency
    anvil.reset_fork(285000000)

    # Define the user account and vault address for the test
    user_account = "0x1714400FF23dB4aF24F9fd64e7039e6597f18C2b"
    vault_address = "0x05231e2fB2F580F043D7760a5CFb4Ec6F01656E9"

    # Set up the Plasma Vault System Factory instance
    system_factory = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )

    # Create an alpha instance from the system factory
    alpha = system_factory.get(vault_address)

    # Set up the Cheating Plasma Vault System to manipulate roles
    cheating_system_factory = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )

    # Initialize a user in the cheating system
    user = cheating_system_factory.get(alpha.plasma_vault().address())

    # Grant necessary roles to the user to enable interaction with the vault
    user.prank(alpha.access_manager().owner())
    user.access_manager().grant_role(Roles.ALPHA_ROLE, alpha.alpha(), 0)
    user.access_manager().grant_role(Roles.WHITELIST_ROLE, user_account, 0)

    # Setup initial values for depositing funds
    amount = 1000_000000  # Equivalent to 100 * 1e6 in USDC

    # Approve and deposit USDC to the Plasma Vault for the specified user account
    user.prank(user_account)
    user.usdc().approve(alpha.plasma_vault().address(), amount)
    user.plasma_vault().deposit(amount, user_account)

    # Calculate the maximum amount the user can withdraw from the vault
    to_withdraw = alpha.plasma_vault().max_withdraw(user_account)

    # Simulate a withdrawal request for the available amount
    user.withdraw_manager().request(to_withdraw)

    # Advance blockchain time to simulate pending period for request processing
    anvil.move_time(60 * 60)

    # Verify that the correct amount has been requested for withdrawal
    requested_amount, release_timestamp = (
        alpha.withdraw_manager().get_pending_requests_info()
    )
    assert requested_amount == to_withdraw

    # Advance blockchain time to prepare for release of requested funds
    anvil.move_time(60)

    # Release the requested funds at the correct timestamp
    alpha.withdraw_manager().release_funds(timestamp=release_timestamp)

    # Advance blockchain time to simulate final withdrawal state
    anvil.move_time(60 * 60)

    # Capture user's balance before withdrawal
    user_balance_before = alpha.usdc().balance_of(user_account)

    # Determine the current max withdrawal amount again
    to_withdraw = alpha.plasma_vault().max_withdraw(user_account)

    # Execute the withdrawal by the user
    user.plasma_vault().withdraw(
        assets=to_withdraw, receiver=user_account, owner=user_account
    )

    # Verify that the user's balance has increased by the correct amount after withdrawal
    user_balance_after = alpha.usdc().balance_of(user_account)
    user_balance_change = user_balance_after - user_balance_before
    assert user_balance_change == to_withdraw

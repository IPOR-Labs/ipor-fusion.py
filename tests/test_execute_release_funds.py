import logging
import os

from eth_typing import BlockNumber
from web3 import Web3

from constants import ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.CheatingPlasmaVaultSystemFactory import (
    CheatingPlasmaVaultSystemFactory,
)
from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles
from ipor_fusion.types import Amount, Period

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
    anvil.reset_fork(BlockNumber(285000000))

    # Define the user account and vault address for the test
    user_account = Web3.to_checksum_address(
        "0x1714400FF23dB4aF24F9fd64e7039e6597f18C2b"
    )
    vault_address = Web3.to_checksum_address(
        "0x05231e2fB2F580F043D7760a5CFb4Ec6F01656E9"
    )

    # Set up the Plasma Vault System Factory instance
    system_factory = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )

    # Create an alpha instance from the system factory
    alpha = system_factory.get(vault_address)

    usdc = alpha.erc20("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

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
    amount = Amount(1000_000000)  # Equivalent to 100 * 1e6 in USDC

    # Approve and deposit USDC to the Plasma Vault for the specified user account
    user_usdc = user.erc20("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    user.prank(user_account)
    user_usdc.approve(alpha.plasma_vault().address(), amount)
    user.plasma_vault().deposit(amount, user_account)

    # Calculate the maximum amount the user can withdraw from the vault
    to_withdraw = alpha.plasma_vault().max_withdraw(user_account)

    # Simulate a withdrawal request for the available amount
    user.withdraw_manager().request(to_withdraw)

    # Advance blockchain time to simulate pending period for request processing
    anvil.move_time(Period.HOUR)

    # Verify that the correct amount has been requested for withdrawal
    requested_amount, release_timestamp = (
        alpha.withdraw_manager().get_pending_requests_info()
    )
    assert requested_amount == to_withdraw

    # Advance blockchain time to prepare for release of requested funds
    anvil.move_time(Period.MINUTE)

    # Release the requested funds at the correct timestamp
    alpha.withdraw_manager().release_funds(timestamp=release_timestamp)

    # Advance blockchain time to simulate final withdrawal state
    anvil.move_time(Period.HOUR)

    # Capture user's balance before withdrawal
    user_balance_before = usdc.balance_of(user_account)

    # Determine the current max withdrawal amount again
    to_withdraw = alpha.plasma_vault().max_withdraw(user_account)

    # Execute the withdrawal by the user
    user.plasma_vault().withdraw(
        assets=to_withdraw, receiver=user_account, owner=user_account
    )

    # Verify that the user's balance has increased by the correct amount after withdrawal
    user_balance_after = usdc.balance_of(user_account)
    user_balance_change = user_balance_after - user_balance_before
    assert user_balance_change == to_withdraw


def test_should_release_funds_shares():
    # Reset the blockchain state to a specific block number for test consistency
    # This ensures the test starts from a known blockchain state
    anvil.reset_fork(BlockNumber(315570373))

    # Define the test accounts and contract addresses
    # user_account: The account that will deposit and withdraw funds
    # vault_address: The address of the Plasma Vault contract
    # alpha_address: The address that will be granted the ALPHA_ROLE for approving withdrawals
    user_account = Web3.to_checksum_address(
        "0x1714400FF23dB4aF24F9fd64e7039e6597f18C2b"
    )
    vault_address = Web3.to_checksum_address(
        "0x272Cb09e2d6237304E4D2BcB9DEf40032aA0ebB1"
    )
    alpha_address = Web3.to_checksum_address(
        "0xB2Fc9e5c92577bE694E7EBbE76Eeb5977bec4D9A"
    )

    # Initialize the CheatingPlasmaVaultSystem for testing
    # This provides a way to interact with the vault system and manipulate its state
    cheating = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(vault_address)

    # Get references to key contracts in the vault system
    vault = cheating.plasma_vault()
    withdraw_manager = cheating.withdraw_manager()
    usdc = cheating.erc20("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")

    # Set up the roles and permissions required for the test
    # First, impersonate the access manager's owner to grant roles
    cheating.prank(cheating.access_manager().owner())

    # Grant ALPHA_ROLE to alpha_address (allows releasing funds)
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, alpha_address, 0)

    # Grant WHITELIST_ROLE to user_account (allows deposits and withdrawals)
    cheating.access_manager().grant_role(Roles.WHITELIST_ROLE, user_account, 0)

    # Prepare for deposit: Define the amount to deposit (1,000 USDC with 6 decimals)
    amount = Amount(1000_000000)  # 1,000 USDC (with 6 decimal places)

    # Execute deposit workflow:
    # 1. Impersonate the user account
    cheating.prank(user_account)
    # 2. Approve the vault to spend the user's USDC
    usdc.approve(vault.address(), amount)
    # 3. Deposit USDC into the vault
    cheating.plasma_vault().deposit(amount, user_account)

    # Calculate the maximum amount the user can withdraw from the vault
    # This may differ from the deposit amount due to fees or gained yield
    max_withdraw = vault.max_withdraw(user_account)

    # Convert the withdrawal amount to vault shares represent ownership of the vault's assets
    shares = vault.convert_to_shares(max_withdraw)

    # Begin the withdrawal process:
    # 1. Impersonate the user to request a withdrawal
    cheating.prank(user_account)
    # 2. Submit a withdrawal request for the calculated shares
    withdraw_manager.request_shares(shares)

    # Move blockchain time forward by 1 hour to simulate waiting period
    anvil.move_time(Period.HOUR)

    # Process the withdrawal request:
    # 1. Impersonate the alpha role account
    cheating.prank(alpha_address)
    # 2. Release the requested funds (approve the withdrawal)
    # Note: Using a timestamp slightly before current block to test timestamp validation
    withdraw_manager.release_funds(
        timestamp=anvil.current_block_timestamp() - 1, shares=shares
    )

    # Move blockchain time forward by another hour
    anvil.move_time(Period.HOUR)

    # Record user's USDC balance before completing the withdrawal
    user_balance_before = usdc.balance_of(user_account)

    # Complete the withdrawal process:
    # 1. Impersonate the user again
    cheating.prank(user_account)
    # 2. Redeem the shares to receive USDC tokens
    vault.redeem_from_request(shares=shares, receiver=user_account, owner=user_account)

    # Record user's USDC balance after withdrawal to verify the received amount
    user_balance_after = usdc.balance_of(user_account)
    user_balance_change = user_balance_after - user_balance_before

    # Verify that the user received the expected amount (allowing for a small fee)
    # The user should receive at least 999 USDC from their 1000 USDC deposit
    assert user_balance_change > 999_000000  # Expect less than 1000 due to fees

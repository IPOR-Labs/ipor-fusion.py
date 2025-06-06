import logging
import os

from web3 import Web3

from constants import ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles

# Set up logging to track test execution details
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Get provider URL from environment variables for fork-based testing
fork_url = os.getenv("BASE_PROVIDER_URL")

# Initialize a local fork using Anvil for isolated testing
# This creates a sandboxed environment with a copy of blockchain state
anvil = AnvilTestContainerStarter(fork_url)
anvil.start()


def test_supply_borrow_in_flash_loan():
    """
    Test a flash loan operation that supplies WStETH as collateral to Aave V3 and borrows WETH.

    This test demonstrates how Plasma Vault can execute complex DeFi operations atomically
    using flash loans for capital efficiency.
    """
    # Reset fork to a specific block (30431901) to ensure test reproducibility
    # This guarantees the same blockchain state for every test run
    anvil.reset_fork(30431901)

    # Define key addresses needed for testing
    atomist = Web3.to_checksum_address(
        "0xF6a9bd8F6DC537675D499Ac1CA14f2c55d8b5569"
    )  # Protocol admin with permission management capabilities
    vault_address = Web3.to_checksum_address(
        "0xc4c00d8b323f37527eeda27c87412378be9f68ec"
    )  # Target Plasma Vault contract to test
    wsteth_address = Web3.to_checksum_address(
        "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452"
    )  # Wrapped staked ETH token used as collateral
    wsteth_holder = Web3.to_checksum_address(
        "0x31b7538090C8584FED3a053FD183E202c26f9a3e"
    )  # Account with WStETH balance to initialize the test
    weth_address = Web3.to_checksum_address(
        "0x4200000000000000000000000000000000000006"
    )  # Wrapped ETH token to be borrowed

    # Set loan-to-value ratio for borrowing (90% of collateral value)
    # This determines how much can be borrowed relative to collateral value
    LTV = 0.9

    # Initialize the Plasma Vault system with connection to our local Anvil instance
    # This provides access to all system components and interfaces
    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),  # Connect to local Anvil node
        private_key=ANVIL_WALLET_PRIVATE_KEY,  # Use test wallet for transactions
    ).get(vault_address)

    # Configure access permissions for test participants
    # The 'cheater' method allows impersonating accounts for testing purposes
    # ALPHA_ROLE allows interaction with protocol core functions
    system.cheater(atomist).access_manager().grant_role(
        Roles.ALPHA_ROLE, system.alpha(), 0
    )
    # WHITELIST_ROLE permits the holder to deposit assets into the vault
    system.cheater(atomist).access_manager().grant_role(
        Roles.WHITELIST_ROLE, wsteth_holder, 0
    )

    # Set up initial deposit of 1 WStETH (1e18 in wei denomination)
    # Wei is the smallest unit of Ethereum (1 ETH = 10^18 wei)
    initial_deposit = int(1e18)

    # Approve the vault to transfer WStETH from the holder's account
    # ERC20 tokens require explicit approval before transferring
    system.cheater(wsteth_holder).erc20(wsteth_address).approve(
        spender=system.plasma_vault().address(),  # Authorize the vault contract
        amount=initial_deposit,  # Amount to approve for transfer
    )

    # Deposit WStETH into the Plasma Vault to initialize testing state
    # This simulates a user depositing funds into the vault
    system.cheater(wsteth_holder).plasma_vault().deposit(
        assets=initial_deposit,  # Amount of assets to deposit
        receiver=wsteth_holder,  # Recipient of shares representing the deposit
    )

    # Verify the deposit was successful by checking vault balance
    # This assertion confirms the tokens were transferred correctly
    assert system.erc20(wsteth_address).balance_of(vault_address) == 1e18

    # Store current WStETH balance for use in subsequent operations
    wsteth_balance = system.erc20(wsteth_address).balance_of(vault_address)

    # Use entire WStETH balance as collateral for subsequent operations
    wsteth_collateral_amount = wsteth_balance

    # STEP 1: Create action to supply WStETH as collateral to Aave V3
    # This action defines a transaction to supply tokens to the lending pool
    # E-mode=1 enables enhanced LTV for assets in the same risk category (ETH correlated assets)
    # This action will be executed during the flash loan
    supply = system.aave_v3().supply(
        asset_address=wsteth_address,  # Token to supply as collateral
        amount=wsteth_collateral_amount,  # Amount to supply
        e_mode=1,  # Efficiency mode for higher borrowing capacity
    )

    # Get current market prices to calculate maximum borrowing capacity
    # Price oracle provides real-time price data for on-chain assets
    wsteth_price = system.price_oracle_middleware().get_asset_price(wsteth_address)
    weth_price = system.price_oracle_middleware().get_asset_price(weth_address)

    # STEP 2: Create action to borrow WETH from Aave V3 based on supplied collateral
    # Calculate maximum safe borrow amount based on LTV and current asset prices
    # Converting price units and applying the loan-to-value ratio to determine safe borrowing limit
    weth_borrow_amount = int(
        wsteth_collateral_amount * LTV * wsteth_price.readable() / weth_price.readable()
    )

    # Create a borrow action for execution within the flash loan
    borrow = system.aave_v3().borrow(
        asset_address=weth_address,  # Token to borrow
        amount=weth_borrow_amount,  # Amount to borrow based on collateral value
    )

    # STEP 3: Create flash loan transaction that executes both supply and borrow actions
    # The flash loan temporarily provides the needed WStETH, which is repaid within the same transaction
    # This allows executing the entire operation without needing the capital upfront
    flash_loan = system.morpho().flash_loan(
        amount=int(
            wsteth_collateral_amount
        ),  # Amount to flash loan (same as collateral)
        asset_address=wsteth_address,  # Token to flash loan
        actions=[supply, borrow],  # List of actions to execute during the flash loan
    )

    # Execute the flash loan operation through the Plasma Vault
    # This sends the transaction to the blockchain and processes all nested operations
    system.plasma_vault().execute([flash_loan])

    # Verify that the borrowed WETH was successfully received by the vault
    # This confirms the entire supply-borrow transaction was executed correctly
    # If this assertion passes, the test has successfully demonstrated a working flash loan flow
    assert system.erc20(weth_address).balance_of(vault_address) == weth_borrow_amount

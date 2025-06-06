import logging
import os

from web3 import Web3

from constants import ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles

# Configure logging to display relevant test information
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Retrieve the fork URL from environment variables for connecting to Ethereum mainnet
fork_url = os.getenv("BASE_PROVIDER_URL")

# Initialize the Anvil test container with the provided fork URL
# This creates a local instance of Ethereum mainnet for testing
anvil = AnvilTestContainerStarter(fork_url)
anvil.start()


def test_supply_borrow_in_flash_loan():
    # Reset fork to a specific block number to ensure a consistent testing environment
    anvil.reset_fork(30431901)

    # Define key addresses for the test
    atomist = Web3.to_checksum_address(
        "0xF6a9bd8F6DC537675D499Ac1CA14f2c55d8b5569"
    )  # Admin/controller address
    vault_address = Web3.to_checksum_address(
        "0xc4c00d8b323f37527eeda27c87412378be9f68ec"
    )  # Plasma Vault contract address
    wsteth_address = Web3.to_checksum_address(
        "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452"
    )  # WStETH token address
    wsteth_holder = Web3.to_checksum_address(
        "0x31b7538090C8584FED3a053FD183E202c26f9a3e"
    )  # Address with WStETH balance
    weth_address = Web3.to_checksum_address(
        "0x4200000000000000000000000000000000000006"
    )  # WETH token address

    LTV = 0.9

    # Initialize the Plasma Vault system with the test provider
    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(vault_address)

    # Set up permissions: grant ALPHA_ROLE to the system alpha and WHITELIST_ROLE to the WStETH holder
    system.cheater(atomist).access_manager().grant_role(
        Roles.ALPHA_ROLE, system.alpha(), 0
    )
    system.cheater(atomist).access_manager().grant_role(
        Roles.WHITELIST_ROLE, wsteth_holder, 0
    )

    # Approve the Plasma Vault to spend WStETH from the holder's account
    initial_deposit = int(1e18)
    system.cheater(wsteth_holder).erc20(wsteth_address).approve(
        spender=system.plasma_vault().address(), amount=initial_deposit
    )

    # Deposit WStETH into the Plasma Vault
    system.cheater(wsteth_holder).plasma_vault().deposit(
        assets=initial_deposit, receiver=wsteth_holder
    )

    # Verify that the vault now holds 1 WStETH
    assert system.erc20(wsteth_address).balance_of(vault_address) == 1e18
    wsteth_balance = system.erc20(wsteth_address).balance_of(vault_address)

    wsteth_collateral_amount = wsteth_balance

    # STEP 1: Supply WStETH as collateral to Aave V3
    # This enables borrowing against this collateral
    # E-mode 1 is used for higher efficiency borrowing within the same asset class
    supply = system.aave_v3().supply(
        asset_address=wsteth_address, amount=wsteth_collateral_amount, e_mode=1
    )

    wsteth_price = system.price_oracle_middleware().get_asset_price(wsteth_address)
    weth_price = system.price_oracle_middleware().get_asset_price(weth_address)

    # STEP 2: Borrow WETH from Aave V3 using the supplied WStETH as collateral
    weth_borrow_amount = int(
        wsteth_collateral_amount * LTV * wsteth_price.readable() / weth_price.readable()
    )
    borrow = system.aave_v3().borrow(
        asset_address=weth_address, amount=weth_borrow_amount
    )

    flash_loan = system.morpho().flash_loan(
        amount=int(wsteth_collateral_amount),
        asset_address=wsteth_address,
        actions=[supply, borrow],
    )

    system.plasma_vault().execute([flash_loan])

    # Verify that the borrowed WETH is now in the vault
    assert system.erc20(weth_address).balance_of(vault_address) == weth_borrow_amount

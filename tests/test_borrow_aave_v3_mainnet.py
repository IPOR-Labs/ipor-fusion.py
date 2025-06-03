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
fork_url = os.getenv("ETHEREUM_PROVIDER_URL")

# Initialize the Anvil test container with the provided fork URL
# This creates a local instance of Ethereum mainnet for testing
anvil = AnvilTestContainerStarter(fork_url)
anvil.start()


def test_should_borrow_aave_v3():
    """
    Test the full Aave V3 borrow-repay lifecycle using a Plasma Vault.
    This test demonstrates how to:
    1. Set up permissions and roles
    2. Deposit collateral (WBTC)
    3. Supply collateral to Aave V3
    4. Borrow another asset (WETH)
    5. Repay the borrowed asset
    6. Withdraw the original collateral
    """
    # Reset fork to a specific block number to ensure a consistent testing environment
    anvil.reset_fork(22616438)

    # Define key addresses for the test
    atomist = Web3.to_checksum_address(
        "0x46B48240f61C831B85fCf4c198C98028Ab8EE68d"
    )  # Admin/controller address
    vault_address = Web3.to_checksum_address(
        "0x1fdf5dc3F915Cb40E0AD5690DE51E3cB464d1BAD"
    )  # Plasma Vault contract address
    wbtc_address = Web3.to_checksum_address(
        "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"
    )  # WBTC token address
    wbtc_holder = Web3.to_checksum_address(
        "0xE940ae8cF59fE2709BBc572CBAD2633fB45Abf46"
    )  # Address with WBTC balance
    weth_address = Web3.to_checksum_address(
        "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
    )  # WETH token address

    # Initialize the Plasma Vault system with the test provider
    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(vault_address)

    # Set up permissions: grant ALPHA_ROLE to the system alpha and WHITELIST_ROLE to the WBTC holder
    system.cheater(atomist).access_manager().grant_role(
        Roles.ALPHA_ROLE, system.alpha(), 0
    )
    system.cheater(atomist).access_manager().grant_role(
        Roles.WHITELIST_ROLE, wbtc_holder, 0
    )

    # Define the collateral amount (1 WBTC, with 8 decimals)
    wbtc_collateral_amount = int(1e8)

    # Approve the Plasma Vault to spend WBTC from the holder's account
    system.cheater(wbtc_holder).erc20(wbtc_address).approve(
        spender=system.plasma_vault().address(), amount=wbtc_collateral_amount
    )

    # Deposit WBTC into the Plasma Vault
    system.cheater(wbtc_holder).plasma_vault().deposit(
        assets=wbtc_collateral_amount, receiver=wbtc_holder
    )

    # Verify that the vault now holds 1 WBTC
    assert system.erc20(wbtc_address).balance_of(vault_address) == 1e8

    # Grant market substrates to the vault - this is necessary for the vault to interact with Aave V3
    # Substrates define which assets the vault can interact with in the protocol
    anvil.grant_market_substrates(
        _from=atomist,
        plasma_vault=vault_address,
        market_id=1,
        substrates=[
            "0000000000000000000000002260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  # WBTC address
            "000000000000000000000000C02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH address
        ],
    )

    # STEP 1: Supply WBTC as collateral to Aave V3
    # This enables borrowing against this collateral
    # E-mode 1 is used for higher efficiency borrowing within the same asset class
    supply = system.aave_v3().supply(
        asset_address=wbtc_address, amount=wbtc_collateral_amount, e_mode=1
    )
    system.plasma_vault().execute([supply])

    # Verify that WBTC has been transferred from the vault to Aave
    assert system.erc20(wbtc_address).balance_of(vault_address) == 0

    # STEP 2: Borrow WETH from Aave V3 using the supplied WBTC as collateral
    weth_borrow_amount = int(20e18)  # 20 WETH (with 18 decimals)
    borrow = system.aave_v3().borrow(
        asset_address=weth_address, amount=weth_borrow_amount
    )
    system.plasma_vault().execute([borrow])

    # Verify that the borrowed WETH is now in the vault
    assert system.erc20(weth_address).balance_of(vault_address) == weth_borrow_amount

    # STEP 3: Repay the borrowed WETH back to Aave V3
    repay = system.aave_v3().repay(
        asset_address=weth_address, amount=weth_borrow_amount
    )
    system.plasma_vault().execute([repay])

    # Verify that WETH has been repaid and is no longer in the vault
    assert system.erc20(weth_address).balance_of(vault_address) == 0

    # STEP 4: Withdraw the original WBTC collateral from Aave V3
    # Withdraw slightly less than the full amount to avoid potential precision issues
    # Aave sometimes fails when attempting to withdraw 100% of the collateral
    withdraw = system.aave_v3().withdraw(
        asset_address=wbtc_address, amount=int(wbtc_collateral_amount * 0.99999)
    )
    system.plasma_vault().execute([withdraw])

    # Verify that the withdrawn WBTC is back in the vault (minus the small amount left as dust)
    assert system.erc20(wbtc_address).balance_of(vault_address) == int(
        wbtc_collateral_amount * 0.99999
    )

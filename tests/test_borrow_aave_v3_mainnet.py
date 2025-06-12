import logging
import os

from web3 import Web3

from constants import ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.IporFusionMarkets import IporFusionMarkets
from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles
from ipor_fusion.helpers import Addresses
from ipor_fusion.types import Amount

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
    wbtc_holder = Web3.to_checksum_address(
        "0xE940ae8cF59fE2709BBc572CBAD2633fB45Abf46"
    )  # Address with WBTC balance

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
    system.cheater(wbtc_holder).erc20(Addresses.ETHEREUM_WBTC_ADDRESS).approve(
        spender=system.plasma_vault().address(), amount=wbtc_collateral_amount
    )

    # Deposit WBTC into the Plasma Vault
    system.cheater(wbtc_holder).plasma_vault().deposit(
        assets=wbtc_collateral_amount, receiver=wbtc_holder
    )

    # Verify that the vault now holds 1 WBTC
    assert (
        system.erc20(Addresses.ETHEREUM_WBTC_ADDRESS).balance_of(vault_address) == 1e8
    )

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
        asset_address=Addresses.ETHEREUM_WBTC_ADDRESS,
        amount=wbtc_collateral_amount,
        e_mode=1,
    )
    system.plasma_vault().execute([supply])

    # Verify that WBTC has been transferred from the vault to Aave
    assert system.erc20(Addresses.ETHEREUM_WBTC_ADDRESS).balance_of(vault_address) == 0

    # STEP 2: Borrow WETH from Aave V3 using the supplied WBTC as collateral
    weth_borrow_amount = int(20e18)  # 20 WETH (with 18 decimals)
    borrow = system.aave_v3().borrow(
        asset_address=Addresses.ETHEREUM_WETH_ADDRESS, amount=weth_borrow_amount
    )
    system.plasma_vault().execute([borrow])

    # Verify that the borrowed WETH is now in the vault
    assert (
        system.erc20(Addresses.ETHEREUM_WETH_ADDRESS).balance_of(vault_address)
        == weth_borrow_amount
    )

    # STEP 3: Repay the borrowed WETH back to Aave V3
    repay = system.aave_v3().repay(
        asset_address=Addresses.ETHEREUM_WETH_ADDRESS, amount=weth_borrow_amount
    )
    system.plasma_vault().execute([repay])

    # Verify that WETH has been repaid and is no longer in the vault
    assert system.erc20(Addresses.ETHEREUM_WETH_ADDRESS).balance_of(vault_address) == 0

    # STEP 4: Withdraw the original WBTC collateral from Aave V3
    # Withdraw slightly less than the full amount to avoid potential precision issues
    # Aave sometimes fails when attempting to withdraw 100% of the collateral
    withdraw = system.aave_v3().withdraw(
        asset_address=Addresses.ETHEREUM_WBTC_ADDRESS,
        amount=int(wbtc_collateral_amount * 0.99999),
    )
    system.plasma_vault().execute([withdraw])

    # Verify that the withdrawn WBTC is back in the vault (minus the small amount left as dust)
    assert system.erc20(Addresses.ETHEREUM_WBTC_ADDRESS).balance_of(
        vault_address
    ) == int(wbtc_collateral_amount * 0.99999)


def test_should_deposit_to_plasma_vault():
    """
    Test complex DeFi workflow demonstrating:
    1. WBTC Plasma Vault setup with proper roles and permissions
    2. WETH Plasma Vault configuration with cross-vault interactions
    3. Multi-step operation: deposit WBTC → supply to Aave V3 → borrow WETH → deposit to ERC4626 vault
    This test showcases vault-to-vault asset flows and cross-protocol integrations.
    """
    # Reset fork to specific block for consistent test environment
    anvil.reset_fork(22687555)

    # Define primary addresses for WBTC Plasma Vault operations
    atomist = Web3.to_checksum_address(
        "0x46B48240f61C831B85fCf4c198C98028Ab8EE68d"
    )  # Primary admin/controller with governance permissions
    vault_address = Web3.to_checksum_address(
        "0x1fdf5dc3F915Cb40E0AD5690DE51E3cB464d1BAD"
    )  # Main WBTC Plasma Vault contract address
    withdraw_manager_address = Web3.to_checksum_address(
        "0xdaF066a6B51499941299B566d1B124678eBC2b3c"
    )  # Withdrawal manager for the WBTC vault
    wbtc_holder = Web3.to_checksum_address(
        "0xE940ae8cF59fE2709BBc572CBAD2633fB45Abf46"
    )  # Account holding sufficient WBTC for testing
    erc4626_fuse_address = Web3.to_checksum_address(
        "0x970b4f5522685D4826eceb0377B3DdBF12836dFd"
    )  # ERC4626 fuse adapter for vault interactions
    weth_vault_address = Web3.to_checksum_address(
        "0x9824dCdac89F208Bf8b5Cb5C4Dc41F04a0878607"
    )  # Target WETH Plasma Vault for depositing borrowed assets

    # Initialize WBTC Plasma Vault system with withdrawal manager
    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(
        plasma_vault_address=vault_address,
        withdraw_manager_address=withdraw_manager_address,
    )

    # Grant essential roles for vault operations
    # ALPHA_ROLE: Allows execution of vault strategies and operations
    system.cheater(atomist).access_manager().grant_role(
        Roles.ALPHA_ROLE, system.alpha(), 0
    )
    # WHITELIST_ROLE: Permits the WBTC holder to interact with the vault
    system.cheater(atomist).access_manager().grant_role(
        Roles.WHITELIST_ROLE, wbtc_holder, 0
    )

    # Configure market substrates for ERC4626 vault interactions
    # Market ID 100013 represents the ERC4626 integration market
    anvil.grant_market_substrates(
        _from=atomist,
        plasma_vault=vault_address,
        market_id=100013,
        substrates=[
            "0000000000000000000000009824dCdac89F208Bf8b5Cb5C4Dc41F04a0878607"
        ],  # WETH vault substrate
    )

    # Configure Aave V3 market substrates for lending/borrowing operations
    # Enables vault to interact with WBTC and WETH on Aave V3 protocol
    anvil.grant_market_substrates(
        _from=atomist,
        plasma_vault=vault_address,
        market_id=IporFusionMarkets.AAVE_V3,
        substrates=[
            "0000000000000000000000002260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",  # WBTC substrate for Aave V3
            "000000000000000000000000C02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",  # WETH substrate for Aave V3
        ],
    )

    # Define collateral amount: 1 WBTC (8 decimal places)
    wbtc_collateral_amount = Amount(int(1e8))

    # Approve WBTC spending by the Plasma Vault
    system.cheater(wbtc_holder).erc20(Addresses.ETHEREUM_WBTC_ADDRESS).approve(
        spender=system.plasma_vault().address(), amount=wbtc_collateral_amount
    )

    # Execute initial deposit of WBTC into the Plasma Vault
    system.cheater(wbtc_holder).plasma_vault().deposit(
        assets=wbtc_collateral_amount, receiver=wbtc_holder
    )

    # Configure WETH Plasma Vault system for cross-vault operations
    weth_vault_system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(plasma_vault_address=weth_vault_address)

    # WETH vault admin address for permission management
    weth_atomist = Web3.to_checksum_address(
        "0xf2C6a2225BE9829eD77263b032E3D92C52aE6694"
    )

    # Grant WHITELIST_ROLE to the WBTC vault, enabling it to deposit into WETH vault
    weth_vault_system.cheater(weth_atomist).access_manager().grant_role(
        role_id=Roles.WHITELIST_ROLE, account=vault_address, execution_delay=0
    )

    # Adjust WETH vault supply cap to accommodate the incoming deposit
    # Reducing cap to 1/4 of original to ensure sufficient capacity
    cap = weth_vault_system.plasma_vault().get_total_supply_cap()
    weth_vault_system.cheater(weth_atomist).plasma_vault().set_total_supply_cap(
        int(cap / 4)
    )

    # Verify initial WBTC deposit is in the vault
    assert (
        system.erc20(Addresses.ETHEREUM_WBTC_ADDRESS).balance_of(vault_address) == 1e8
    )

    # STEP 1: Supply WBTC as collateral to Aave V3
    # E-mode 1 enables higher borrowing efficiency within BTC asset category
    supply_aave = system.aave_v3().supply(
        asset_address=Addresses.ETHEREUM_WBTC_ADDRESS,
        amount=wbtc_collateral_amount,
        e_mode=1,
    )
    system.plasma_vault().execute([supply_aave])

    # Verify WBTC has been transferred to Aave V3 (vault balance should be 0)
    assert system.erc20(Addresses.ETHEREUM_WBTC_ADDRESS).balance_of(vault_address) == 0

    # STEP 2: Borrow WETH against the WBTC collateral
    weth_borrow_amount = Amount(int(20e18))  # 20 WETH (18 decimal places)
    borrow = system.aave_v3().borrow(
        asset_address=Addresses.ETHEREUM_WETH_ADDRESS, amount=weth_borrow_amount
    )
    system.plasma_vault().execute([borrow])

    # Verify borrowed WETH is now in the WBTC vault
    assert (
        system.erc20(Addresses.ETHEREUM_WETH_ADDRESS).balance_of(vault_address)
        == weth_borrow_amount
    )

    # Log the borrowed amount for monitoring purposes
    log.info("weth_borrow_amount: %s", weth_borrow_amount / 1e18)

    # STEP 3: Prepare to deposit borrowed WETH into the WETH Plasma Vault via ERC4626 fuse
    supply_erc4626 = system.erc4626(fuse_address=erc4626_fuse_address).supply(
        vault_address=weth_vault_address, amount=weth_borrow_amount
    )

    # Verify WETH is still in the WBTC vault before final transfer
    assert (
        system.erc20(Addresses.ETHEREUM_WETH_ADDRESS).balance_of(vault_address)
        == weth_borrow_amount
    )

    # STEP 4: Execute the cross-vault deposit - transfer WETH to the WETH vault
    system.plasma_vault().execute([supply_erc4626])

    # Verify final state: WETH has been successfully transferred to the WETH vault
    # WBTC vault should have no WETH remaining
    assert system.erc20(Addresses.ETHEREUM_WETH_ADDRESS).balance_of(vault_address) == 0

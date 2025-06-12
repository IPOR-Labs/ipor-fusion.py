import logging
import os

from web3 import Web3

from constants import ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles
from ipor_fusion.helpers import Addresses
from ipor_fusion.helpers.AerodromeSwapHelper import AerodromeSwapHelper
from ipor_fusion.helpers.BalanceLogger import BalanceLogger
from ipor_fusion.types import Amount

# Configure logging to track detailed test execution flow
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Retrieve blockchain fork URL from environment for connecting to Base network
# This allows testing against real blockchain state without using mainnet
fork_url = os.getenv("BASE_PROVIDER_URL")

# Initialize Anvil - a local Ethereum node that forks from the Base network
# Anvil provides an isolated testing environment with snapshot/reset capabilities
anvil = AnvilTestContainerStarter(fork_url)
anvil.start()


def test_supply_borrow_in_flash_loan():
    """
    Test demonstrating a leveraged DeFi strategy using flash loans and Aave V3.

    This test implements a "looping" strategy where:
    1. Flash loan WStETH (10x the vault's balance)
    2. Supply all WStETH (original + flash loaned) as collateral to Aave V3
    3. Borrow WETH against this collateral (90% LTV)
    4. Swap borrowed WETH back to WStETH via Aerodrome DEX
    5. Repay the flash loan with the swapped WStETH

    The result is a leveraged position where the vault has increased WStETH exposure
    while maintaining a borrowing position on Aave V3. This amplifies both potential
    gains and risks from WStETH price movements.
    """
    # Reset blockchain state to a specific block for deterministic testing
    # Block 30431901 represents a known good state with adequate liquidity
    anvil.reset_fork(30431901)

    # Key protocol addresses for permission management and vault interaction
    atomist = Web3.to_checksum_address(
        "0xF6a9bd8F6DC537675D499Ac1CA14f2c55d8b5569"
    )  # Protocol governance address with admin privileges
    vault_address = Web3.to_checksum_address(
        "0xc4c00d8b323f37527eeda27c87412378be9f68ec"
    )  # Plasma Vault contract being tested - manages user funds and DeFi interactions
    wsteth_holder = Web3.to_checksum_address(
        "0xf0bb20865277aBd641a307eCe5Ee04E79073416C"
    )  # Whale address holding significant WStETH balance for testing

    # Leverage configuration - determines the aggressiveness of the strategy
    LAVERAGE = 10  # 10x leverage multiplier
    LTV = 1 - 1 / LAVERAGE  # Loan-to-Value ratio = 0.9 (90%)
    # This means we can borrow up to 90% of our collateral value

    # Initialize the Plasma Vault system with local Anvil connection
    # This creates interfaces to all protocol components (vault, markets, oracles)
    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),  # Local fork URL
        private_key=ANVIL_WALLET_PRIVATE_KEY,  # Test account private key
    ).get(vault_address)

    # Set up role-based access control for test execution
    # Without proper roles, transactions would be rejected by the access manager

    # Grant ALPHA_ROLE to the system's alpha account
    # ALPHA_ROLE permits execution of core vault operations and strategy management
    system.cheater(atomist).access_manager().grant_role(
        Roles.ALPHA_ROLE, system.alpha(), 0
    )

    # Grant WHITELIST_ROLE to the WStETH holder
    # WHITELIST_ROLE is required to deposit assets into the vault
    system.cheater(atomist).access_manager().grant_role(
        Roles.WHITELIST_ROLE, wsteth_holder, 0
    )

    # Initial setup: deposit 1 WStETH into the vault to establish a base position
    initial_deposit = int(1e18)  # 1 WStETH in wei (18 decimals)

    # ERC20 approval is mandatory before any token transfer
    # This authorizes the vault contract to transfer tokens from the holder
    system.cheater(wsteth_holder).erc20(Addresses.BASE_WSTETH).approve(
        spender=system.plasma_vault().address(),
        amount=initial_deposit,
    )

    # Execute the deposit transaction
    # This mints vault shares to the depositor in exchange for the underlying asset
    system.cheater(wsteth_holder).plasma_vault().deposit(
        assets=initial_deposit,
        receiver=wsteth_holder,  # Share recipient
    )

    # Verify successful deposit by checking vault's token balance
    assert system.erc20(Addresses.BASE_WSTETH).balance_of(vault_address) == 1e18

    # Calculate the leverage strategy parameters
    wsteth_balance = system.erc20(Addresses.BASE_WSTETH).balance_of(vault_address)

    # Total collateral amount = initial balance × leverage multiplier
    # This represents the total WStETH we'll supply to Aave (including flash loan)
    wsteth_collateral_amount = wsteth_balance * LAVERAGE

    # === STRATEGY EXECUTION PHASE ===

    # STEP 1: Prepare Aave V3 supply action
    # This action will supply the leveraged amount of WStETH as collateral
    # E-mode=1 enables efficiency mode for ETH-correlated assets, providing higher LTV
    supply = system.aave_v3().supply(
        asset_address=Addresses.BASE_WSTETH,
        amount=wsteth_collateral_amount,  # 10 WStETH (1 original + 9 flash loaned)
        e_mode=1,  # Efficiency mode for better capital efficiency
    )

    # STEP 2: Calculate optimal borrow amount using price oracles
    # Price oracles provide real-time market data to determine asset values
    wsteth_price = system.price_oracle_middleware().get_asset_price(
        Addresses.BASE_WSTETH
    )
    weth_price = system.price_oracle_middleware().get_asset_price(Addresses.BASE_WETH)

    # Calculate maximum safe borrowing amount considering:
    # - Total collateral value in USD
    # - LTV ratio (90%)
    # - Price difference between collateral and borrowed asset
    weth_borrow_amount = int(
        wsteth_collateral_amount * LTV * wsteth_price.readable() / weth_price.readable()
    )

    # Prepare Aave V3 borrow action
    # This will borrow WETH against the supplied WStETH collateral
    borrow = system.aave_v3().borrow(
        asset_address=Addresses.BASE_WETH,
        amount=weth_borrow_amount,
    )

    # STEP 3: Prepare DEX swap to close the loop
    # Swap borrowed WETH back to WStETH to repay the flash loan
    # Using Aerodrome DEX which provides deep liquidity for ETH pairs on Base
    swap = AerodromeSwapHelper.create_aerodrome_swap_exact_input(
        system=system,
        token_in=Addresses.BASE_WETH,  # Sell borrowed WETH
        token_out=Addresses.BASE_WSTETH,  # Buy WStETH to repay flash loan
        amount_in=weth_borrow_amount,
        min_amount_out=0,  # No slippage protection for testing (don't do this in production!)
        deadline=(
            get_current_timestamp(system).timestamp + 1000
        ),  # 1000 second deadline
    )

    # STEP 4: Create the flash loan transaction that orchestrates everything
    # Flash loans allow borrowing assets without collateral within a single transaction
    # All borrowed funds must be repaid before the transaction completes
    flash_loan = system.morpho().flash_loan(
        amount=Amount(wsteth_collateral_amount - wsteth_balance),  # Flash loan 9 WStETH
        asset_address=Addresses.BASE_WSTETH,
        actions=[
            supply,  # Supply 10 WStETH as collateral
            borrow,  # Borrow WETH against collateral
            swap,  # Swap WETH back to WStETH for repayment
        ],
    )

    # Log balances before strategy execution for comparison
    BalanceLogger.log_balances(system, "before leveraging strategy")

    # Execute the complete leveraged strategy in a single atomic transaction
    # If any step fails, the entire transaction reverts, ensuring no partial execution
    system.plasma_vault().execute([flash_loan])

    # Log final balances to verify strategy success
    BalanceLogger.log_balances(system=system, msg="after leveraging strategy")

    # Expected outcome:
    # - Vault has increased WStETH collateral position on Aave
    # - Vault has a WETH debt position on Aave
    # - Net effect: leveraged exposure to WStETH price movements


def get_current_timestamp(system):
    """
    Retrieve the current blockchain timestamp.

    This is used for setting swap deadlines and ensuring transactions
    are executed within a reasonable timeframe.
    """
    web3 = system.transaction_executor().get_web3()
    latest_block_number = web3.eth.block_number
    latest_block = web3.eth.get_block(latest_block_number)
    return latest_block

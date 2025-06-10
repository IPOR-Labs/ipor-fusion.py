import logging
import os

from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from constants import ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.PlasmaSystem import PlasmaSystem
from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles
from ipor_fusion.types import Amount

# Set up logging to track test execution details
logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Get provider URL from environment variables for fork-based testing
fork_url = os.getenv("BASE_PROVIDER_URL")

# Initialize a local fork using Anvil for isolated testing
# This creates a sandboxed environment with a copy of blockchain state
anvil = AnvilTestContainerStarter(fork_url)
anvil.start()

wsteth_address = Web3.to_checksum_address(
    "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452"
)  # WStEth token address
wsteth_holder = Web3.to_checksum_address(
    "0xf0bb20865277aBd641a307eCe5Ee04E79073416C"
)  # Address with WStEth balance
weth_address = Web3.to_checksum_address(
    "0x4200000000000000000000000000000000000006"
)  # WETH token address
variableDebtBasWETH_address = Web3.to_checksum_address(
    "0x24e6e0795b3c7c71D965fCc4f371803d1c1DcA1E"
)
aBaswstETH_address = Web3.to_checksum_address(
    "0x99CBC45ea5bb7eF3a5BC08FB1B7E56bB2442Ef0D"
)


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

    # Set loan-to-value ratio for borrowing (90% of collateral value)
    # This determines how much can be borrowed relative to collateral value
    LAVERAGE = 10
    LTV = 1 - 1 / LAVERAGE  # 0.9

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
    wsteth_collateral_amount = wsteth_balance * LAVERAGE

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

    swap = create_aerodrome_swap_exact_input(
        system=system,
        token_in=weth_address,
        token_out=wsteth_address,
        amount_in=weth_borrow_amount,
        min_amount_out=0,
        deadline=(get_current_timestamp(system).timestamp + 1000),
    )

    # STEP 3: Create flash loan transaction that executes both supply and borrow actions
    # The flash loan temporarily provides the needed WStETH, which is repaid within the same transaction
    # This allows executing the entire operation without needing the capital upfront
    flash_loan = system.morpho().flash_loan(
        amount=Amount(
            wsteth_collateral_amount
        ),  # Amount to flash loan (same as collateral)
        asset_address=wsteth_address,  # Token to flash loan
        actions=[
            supply,
            borrow,
            swap,
        ],  # List of actions to execute during the flash loan
    )

    log_balances(system, "before loop")

    # Execute the flash loan operation through the Plasma Vault
    # This sends the transaction to the blockchain and processes all nested operations
    system.plasma_vault().execute([flash_loan])

    log_balances(system, "after loop")


def get_current_timestamp(system):
    web3 = system.transaction_executor().get_web3()
    latest_block_number = web3.eth.block_number
    latest_block = web3.eth.get_block(latest_block_number)
    return latest_block


def create_aerodrome_swap_exact_input(
    system: PlasmaSystem,
    token_in: ChecksumAddress,
    token_out: ChecksumAddress,
    amount_in: int,
    min_amount_out: int,
    deadline: int,
):
    AERODROME_ROUTER_ADDRESS = Web3.to_checksum_address(
        0xBE6D8F0D05CC4BE24D5167A3EF062215BE6D18A5
    )
    EXECUTOR = Web3.to_checksum_address(0x591435C065FCE9713C8B112FCBF5AF98B8975CB3)

    # Configure swap parameters
    targets = [token_in, AERODROME_ROUTER_ADDRESS]

    # Prepare transfer function call
    function_selector_0 = function_signature_to_4byte_selector(
        "approve(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"], [AERODROME_ROUTER_ADDRESS, amount_in]
    )
    function_call_0 = function_selector_0 + function_args_0

    # Configure swap path with 0.1% fee tier
    path = get_aerodrome_path(token_in, token_out)

    # Prepare execute function call
    function_selector_1 = function_signature_to_4byte_selector(
        "exactInput((bytes,address,uint256,uint256,uint256))"
    )
    function_args_1 = encode(
        ["(bytes,address,uint256,uint256,uint256)"],
        [[path, EXECUTOR, deadline, amount_in, min_amount_out]],
    )
    function_call_1 = function_selector_1 + function_args_1

    # Combine function calls and create swap instruction
    data = [function_call_0, function_call_1]

    return system.universal().swap(
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        targets=targets,
        data=data,
    )


class UnsupportedPathException(BaseException):
    pass


def get_aerodrome_path(token_in, token_out):
    if token_in == weth_address and token_out == wsteth_address:
        return encode_packed(
            ["address", "uint24", "address"],
            [token_in, 1, token_out],
        )

    if token_in == wsteth_address and token_out == weth_address:
        return encode_packed(
            ["address", "uint24", "address"],
            [token_in, 1, token_out],
        )

    raise UnsupportedPathException("token_in={token_in}, token_out={token_out}")


def log_balances(system, msg):
    log.info("[%s]", msg)
    log.info(
        "    wsteth: %s WStEth",
        system.erc20(wsteth_address).balance_of(system.plasma_vault().address()) / 1e18,
    )
    log.info(
        "      weth: %s WEth",
        system.erc20(weth_address).balance_of(system.plasma_vault().address()) / 1e18,
    )
    log.info(
        "collateral: %s WStEth",
        system.erc20(aBaswstETH_address).balance_of(system.plasma_vault().address())
        / 1e18,
    )
    log.info(
        "  borrowed: %s WEth",
        system.erc20(variableDebtBasWETH_address).balance_of(
            system.plasma_vault().address()
        )
        / 1e18,
    )
    log.info("----")

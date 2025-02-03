import logging
import os

from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_utils import function_signature_to_4byte_selector

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
fork_url = os.getenv("BASE_PROVIDER_URL")

# Initialize the Anvil test container with the provided fork URL
anvil = AnvilTestContainerStarter(fork_url)
anvil.start()


def test_should_swap_on_base():
    """
    Test to verify the swapping functionality on Base network.
    This test simulates swapping cbBTC for USDC using Uniswap V3 router.
    """
    # Reset fork to a specific block for consistent test environment
    anvil.reset_fork(24383840)

    # Define test parameters
    # User account that will perform the swap
    user_account = "0x17548bc38669D3D6590C861E505716245b4598bB"
    # Target vault address for the swap operation
    vault_address = "0x55d8d6e5F17F153f3250b229D5AAc9437e908a77"

    # SETUP PHASE
    # Initialize the system with proper permissions and roles
    system_factory = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    alpha = system_factory.get(vault_address)

    # Set up cheating system for role manipulation
    cheating_system_factory = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    user = cheating_system_factory.get(alpha.plasma_vault().address())

    # Grant necessary roles to enable vault interaction
    # First, impersonate an atomist to grant roles
    user.prank(alpha.access_manager().atomists()[0])
    user.access_manager().grant_role(Roles.ALPHA_ROLE, alpha.alpha(), 0)
    user.access_manager().grant_role(Roles.WHITELIST_ROLE, user_account, 0)

    # DEPOSIT PHASE
    # Setup initial deposit of 1 cbBTC
    amount = 1_00000000  # 1 cbBTC with proper decimals

    # Approve and deposit cbBTC to the Plasma Vault
    user.prank(user_account)
    user.cbBTC().approve(alpha.plasma_vault().address(), amount)
    user.plasma_vault().deposit(amount, user_account)

    # SWAP CONFIGURATION
    # Define Uniswap V3 router address for swap execution
    uniswap_v_3_universal_router_address = "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"
    targets = [alpha.cbBTC().address(), uniswap_v_3_universal_router_address]

    # Prepare first transaction: Transfer cbBTC to router
    function_selector_0 = function_signature_to_4byte_selector(
        "transfer(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"],
        [uniswap_v_3_universal_router_address, (int(amount / 2))],
    )
    function_call_0 = function_selector_0 + function_args_0

    # Configure swap path: cbBTC -> USDC with 0.5% fee tier
    path = encode_packed(
        ["address", "uint24", "address"],
        [alpha.cbBTC().address(), 500, alpha.usdc().address()],
    )

    # Prepare swap parameters
    inputs = [
        encode(
            ["address", "uint256", "uint256", "bytes", "bool"],
            [
                "0x0000000000000000000000000000000000000001",  # Recipient address (placeholder)
                (int(amount / 2)),  # Amount to swap
                0,  # Minimum amount out (set to 0 for test)
                path,  # Swap path
                False,  # Whether to unwrap WETH
            ],
        )
    ]

    # Prepare second transaction: Execute swap via router
    function_selector_1 = function_signature_to_4byte_selector("execute(bytes,bytes[])")
    function_args_1 = encode(
        ["bytes", "bytes[]"], [encode_packed(["bytes1"], [bytes.fromhex("00")]), inputs]
    )
    function_call_1 = function_selector_1 + function_args_1

    # Combine transactions and create swap instruction
    data = [function_call_0, function_call_1]
    swap = alpha.universal().swap(
        alpha.cbBTC().address(), alpha.usdc().address(), int(amount / 2), targets, data
    )

    # EXECUTION AND VERIFICATION
    # Record balances before swap
    cbBTC_balance_before = alpha.cbBTC().balance_of(alpha.plasma_vault().address())
    usdc_balance_before = alpha.usdc().balance_of(alpha.plasma_vault().address())

    # Execute the swap
    alpha.plasma_vault().execute([swap])

    # Record balances after swap
    cbBTC_balance_after = alpha.cbBTC().balance_of(alpha.plasma_vault().address())
    usdc_balance_after = alpha.usdc().balance_of(alpha.plasma_vault().address())

    # Verify the swap was successful
    assert (
        cbBTC_balance_before >= amount
    ), "Initial cbBTC balance should be at least the deposited amount"
    assert (
        usdc_balance_before < 1_000000
    ), "Initial USDC balance should be negligible (dust)"
    assert cbBTC_balance_after >= (
        amount / 2
    ), "Should have at least half of initial cbBTC after swap"
    assert (
        usdc_balance_after > 45000_000000
    ), "Should have received at least 45k USDC from swap"


def test_should_swap_on_base_2():
    """
    Test to verify the swapping functionality on Base network.
    This test simulates swapping weth for USDC using Uniswap V3 router.
    """
    # Reset fork to a specific block for consistent test environment
    anvil.reset_fork(25162895)

    # Define test parameters
    # User account that will perform the swap
    user_account = "0x621e7c767004266c8109e83143ab0Da521B650d6"
    # Target vault address for the swap operation
    vault_address = "0x85b7927B6d721638b575972111F4CE6DaCb7D33C"

    # SETUP PHASE
    # Initialize the system with proper permissions and roles
    system_factory = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    alpha = system_factory.get(vault_address)

    # Set up cheating system for role manipulation
    cheating_system_factory = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    user = cheating_system_factory.get(alpha.plasma_vault().address())

    # Grant necessary roles to enable vault interaction
    # First, impersonate an atomist to grant roles
    user.prank(alpha.access_manager().atomists()[0])
    user.access_manager().grant_role(Roles.ALPHA_ROLE, alpha.alpha(), 0)
    user.access_manager().grant_role(Roles.WHITELIST_ROLE, user_account, 0)

    # DEPOSIT PHASE
    # Setup initial deposit of 1 weth
    amount = int(1e18)  # 1 weth with proper decimals

    # Approve and deposit weth to the Plasma Vault
    user.prank(user_account)
    user.weth().approve(alpha.plasma_vault().address(), amount)
    user.plasma_vault().deposit(amount, user_account)

    # SWAP CONFIGURATION
    # Define Uniswap V3 router address for swap execution
    uniswap_v_3_universal_router_address = "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"
    targets = [alpha.weth().address(), uniswap_v_3_universal_router_address]

    # Prepare first transaction: Transfer weth to router
    function_selector_0 = function_signature_to_4byte_selector(
        "transfer(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"],
        [uniswap_v_3_universal_router_address, (int(amount / 2))],
    )
    function_call_0 = function_selector_0 + function_args_0

    # Configure swap path: weth -> USDC with 0.5% fee tier
    path = encode_packed(
        ["address", "uint24", "address"],
        [alpha.weth().address(), 500, alpha.usdc().address()],
    )

    # Prepare swap parameters
    inputs = [
        encode(
            ["address", "uint256", "uint256", "bytes", "bool"],
            [
                "0x0000000000000000000000000000000000000001",  # Recipient address (placeholder)
                (int(amount / 2)),  # Amount to swap
                0,  # Minimum amount out (set to 0 for test)
                path,  # Swap path
                False,  # Whether to unwrap WETH
            ],
        )
    ]

    # Prepare second transaction: Execute swap via router
    function_selector_1 = function_signature_to_4byte_selector("execute(bytes,bytes[])")
    function_args_1 = encode(
        ["bytes", "bytes[]"], [encode_packed(["bytes1"], [bytes.fromhex("00")]), inputs]
    )
    function_call_1 = function_selector_1 + function_args_1

    # Combine transactions and create swap instruction
    data = [function_call_0, function_call_1]
    swap = alpha.universal().swap(
        alpha.weth().address(), alpha.usdc().address(), int(amount / 2), targets, data
    )

    # EXECUTION AND VERIFICATION
    # Record balances before swap
    weth_balance_before = alpha.weth().balance_of(alpha.plasma_vault().address())
    usdc_balance_before = alpha.usdc().balance_of(alpha.plasma_vault().address())

    # Execute the swap
    alpha.plasma_vault().execute([swap])

    # Record balances after swap
    weth_balance_after = alpha.weth().balance_of(alpha.plasma_vault().address())
    usdc_balance_after = alpha.usdc().balance_of(alpha.plasma_vault().address())

    # Verify the swap was successful
    assert (
        weth_balance_before >= amount
    ), "Initial weth balance should be at least the deposited amount"
    assert (
        usdc_balance_before < 1_000000
    ), "Initial USDC balance should be negligible (dust)"
    assert weth_balance_after >= (
        amount / 2
    ), "Should have at least half of initial weth after swap"
    assert (
        usdc_balance_after > 1500_000000
    ), "Should have received at least 45k USDC from swap"

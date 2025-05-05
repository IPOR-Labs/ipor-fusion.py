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


# pylint: disable=too-many-locals
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
    cheating = cheating_system_factory.get(alpha.plasma_vault().address())

    # Grant necessary roles to enable vault interaction
    # First, impersonate an atomist to grant roles
    cheating.prank(alpha.access_manager().atomists()[0])
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, alpha.alpha(), 0)
    cheating.access_manager().grant_role(Roles.WHITELIST_ROLE, user_account, 0)

    # DEPOSIT PHASE
    # Setup initial deposit of 1 cbBTC
    amount = 1_00000000  # 1 cbBTC with proper decimals

    # Approve and deposit cbBTC to the Plasma Vault
    cheating.prank(user_account)
    user_cbBTC = cheating.erc20("0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf")
    user_cbBTC.approve(alpha.plasma_vault().address(), amount)
    cheating.plasma_vault().deposit(amount, user_account)

    # SWAP CONFIGURATION
    # Define Uniswap V3 router address for swap execution
    alpha_cbBTC = alpha.erc20("0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf")
    uniswap_v_3_universal_router_address = "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"
    targets = [alpha_cbBTC.address(), uniswap_v_3_universal_router_address]

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
    alpha_USDC = alpha.erc20("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
    path = encode_packed(
        ["address", "uint24", "address"],
        [alpha_cbBTC.address(), 500, alpha_USDC.address()],
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
        alpha_cbBTC.address(), alpha_USDC.address(), int(amount / 2), targets, data
    )

    # EXECUTION AND VERIFICATION
    # Record balances before swap
    cbBTC_balance_before = alpha_cbBTC.balance_of(alpha.plasma_vault().address())
    usdc_balance_before = alpha_USDC.balance_of(alpha.plasma_vault().address())

    # Execute the swap
    alpha.plasma_vault().execute([swap])

    # Record balances after swap
    cbBTC_balance_after = alpha_cbBTC.balance_of(alpha.plasma_vault().address())
    usdc_balance_after = alpha_USDC.balance_of(alpha.plasma_vault().address())

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


def test_should_swap_weth_to_pepe_on_base():
    """
    Test to verify the swapping functionality on Base network.
    This test simulates swapping wETH for PEPE using Uniswap V3 router.
    """
    # Reset fork to a specific block for consistent test environment
    anvil.reset_fork(25894923)

    vault_address = "0x85b7927B6d721638b575972111F4CE6DaCb7D33C"
    alpha_address = "0xd16A8D5bD6B2cD5499bD55239bc980F09991b5fd"

    # Set up cheating system for role manipulation
    cheating_system_factory = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    alpha = cheating_system_factory.get(vault_address)
    alpha.prank(alpha_address)

    alpha_weth = alpha.erc20("0x4200000000000000000000000000000000000006")
    alpha_pepe = alpha.erc20("0x52b492a33E447Cdb854c7FC19F1e57E8BfA1777D")
    amount = alpha_weth.balance_of(vault_address)

    # SWAP CONFIGURATION
    # Define Uniswap V3 router address for swap execution
    uniswap_v_3_universal_router_address = "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"
    targets = [alpha_weth.address(), uniswap_v_3_universal_router_address]

    # Prepare first transaction: Transfer WETH to router
    function_selector_0 = function_signature_to_4byte_selector(
        "transfer(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"],
        [uniswap_v_3_universal_router_address, amount],
    )
    function_call_0 = function_selector_0 + function_args_0

    # Configure swap path: WETH -> PEPE with 0.5% fee tier
    path = encode_packed(
        ["address", "uint24", "address"],
        [alpha_weth.address(), 10000, alpha_pepe.address()],
    )

    # Prepare swap parameters
    inputs = [
        encode(
            ["address", "uint256", "uint256", "bytes", "bool"],
            [
                "0x0000000000000000000000000000000000000001",  # Recipient address (placeholder)
                amount,  # Amount to swap
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
        alpha_weth.address(), alpha_pepe.address(), amount, targets, data
    )

    # EXECUTION AND VERIFICATION
    # Record balances before swap
    weth_balance_before = alpha_weth.balance_of(alpha.plasma_vault().address())
    pepe_balance_before = alpha_pepe.balance_of(alpha.plasma_vault().address())

    # Execute the swap
    alpha.prank(alpha_address)
    alpha.plasma_vault().execute([swap])

    # Record balances after swap
    weth_balance_after = alpha_weth.balance_of(alpha.plasma_vault().address())
    pepe_balance_after = alpha_pepe.balance_of(alpha.plasma_vault().address())

    assert weth_balance_before == 5000000000000000
    assert weth_balance_after == 0
    assert pepe_balance_before == 0
    assert pepe_balance_after == int(174743647_876992680486051295)

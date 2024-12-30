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
    # Reset the blockchain state to a specific block number for test consistency
    anvil.reset_fork(24383840)

    # Define the user account and vault address for the test
    user_account = "0x17548bc38669D3D6590C861E505716245b4598bB"
    vault_address = "0x55d8d6e5F17F153f3250b229D5AAc9437e908a77"

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
    user.prank(alpha.access_manager().atomists()[0])
    user.access_manager().grant_role(Roles.ALPHA_ROLE, alpha.alpha(), 0)
    user.access_manager().grant_role(Roles.WHITELIST_ROLE, user_account, 0)

    # Setup initial values for depositing funds
    amount = 1_00000000  # 1 cbBTC

    # Approve and deposit cbBTC to the Plasma Vault for the specified user account
    user.prank(user_account)
    user.cbBTC().approve(alpha.plasma_vault().address(), amount)
    user.plasma_vault().deposit(amount, user_account)

    uniswap_v_3_universal_router_address = "0x3fC91A3afd70395Cd496C647d5a6CC9D4B2b7FAD"

    # Define swap targets
    targets = [alpha.cbBTC().address(), uniswap_v_3_universal_router_address]

    # Create the first function call to transfer USDC to the universal router
    function_selector_0 = function_signature_to_4byte_selector(
        "transfer(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"],
        [uniswap_v_3_universal_router_address, (int(amount / 2))],
    )
    function_call_0 = function_selector_0 + function_args_0

    # Encode the path for the swap (USDC to USDT)
    path = encode_packed(
        ["address", "uint24", "address"],
        [alpha.cbBTC().address(), 500, alpha.usdc().address()],
    )

    # Prepare inputs for the execute function call
    inputs = [
        encode(
            ["address", "uint256", "uint256", "bytes", "bool"],
            [
                "0x0000000000000000000000000000000000000001",
                (int(amount / 2)),
                0,
                path,
                False,
            ],
        )
    ]

    # Create the second function call to execute the swap
    function_selector_1 = function_signature_to_4byte_selector("execute(bytes,bytes[])")
    function_args_1 = encode(
        ["bytes", "bytes[]"], [encode_packed(["bytes1"], [bytes.fromhex("00")]), inputs]
    )
    function_call_1 = function_selector_1 + function_args_1

    # Combine both function calls into the swap transaction
    data = [function_call_0, function_call_1]
    swap = alpha.universal().swap(
        alpha.cbBTC().address(), alpha.usdc().address(), int(amount / 2), targets, data
    )

    cbBTC_balance_before = alpha.cbBTC().balance_of(alpha.plasma_vault().address())
    usdc_balance_before = alpha.usdc().balance_of(alpha.plasma_vault().address())

    # Execute the swap transaction
    alpha.plasma_vault().execute([swap])

    cbBTC_balance_after = alpha.cbBTC().balance_of(alpha.plasma_vault().address())
    usdc_balance_after = alpha.usdc().balance_of(alpha.plasma_vault().address())

    assert cbBTC_balance_before >= amount
    assert usdc_balance_before < 1_000000  # less than 1 USDC (dust)

    assert cbBTC_balance_after >= (amount / 2)
    assert usdc_balance_after > 45000_000000  # more than 45k USDC

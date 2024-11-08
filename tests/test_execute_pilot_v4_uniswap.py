import logging
import time

import pytest
from eth_abi import decode, encode
from eth_abi.packed import encode_packed
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import TxReceipt

from constants import (
    ANVIL_WALLET,
    ARBITRUM,
)
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.Roles import Roles
from ipor_fusion.fuse.UniswapV3CollectFuse import UniswapV3CollectFuse
from ipor_fusion.fuse.UniswapV3ModifyPositionFuse import UniswapV3ModifyPositionFuse
from ipor_fusion.fuse.UniswapV3NewPositionFuse import UniswapV3NewPositionFuse
from ipor_fusion.fuse.UniswapV3SwapFuse import UniswapV3SwapFuse
from ipor_fusion.fuse.UniversalTokenSwapperFuse import UniversalTokenSwapperFuse

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

uniswap_v3_swap_fuse = UniswapV3SwapFuse(ARBITRUM.PILOT.V4.UNISWAP_V3_SWAP_FUSE)
uniswap_v3_new_position_fuse = UniswapV3NewPositionFuse(
    ARBITRUM.PILOT.V4.UNISWAP_V3_NEW_POSITION_SWAP_FUSE
)
uniswap_v_3_modify_position_fuse = UniswapV3ModifyPositionFuse(
    ARBITRUM.PILOT.V4.UNISWAP_V3_MODIFY_POSITION_SWAP_FUSE
)
uniswap_v_3_collect_fuse = UniswapV3CollectFuse(
    ARBITRUM.PILOT.V4.UNISWAP_V3_COLLECT_SWAP_FUSE
)
universal_token_swapper_fuse = UniversalTokenSwapperFuse(
    ARBITRUM.PILOT.V4.UNIVERSAL_TOKEN_SWAPPER_FUSE
)


@pytest.fixture(scope="module", name="plasma_vault")
def plasma_vault_fixture(cheating_transaction_executor) -> PlasmaVault:
    return PlasmaVault(
        transaction_executor=cheating_transaction_executor,
        plasma_vault_address=ARBITRUM.PILOT.V4.PLASMA_VAULT,
    )


def test_should_swap_when_one_hop_uniswap_v3(anvil, plasma_vault, usdc, usdt):
    # Setup: Reset state and grant necessary roles
    anvil.reset_fork(254084008)
    anvil.grant_role(ARBITRUM.PILOT.V4.ACCESS_MANAGER, ANVIL_WALLET, Roles.ALPHA_ROLE)

    # Record initial balances before swap
    vault_usdc_balance_before_swap = usdc.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)
    vault_usdt_balance_before_swap = usdt.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)

    # Define swap targets
    targets = [ARBITRUM.USDC, ARBITRUM.UNISWAP.V3.UNIVERSAL_ROUTER]

    # Create the first function call to transfer USDC to the universal router
    function_selector_0 = function_signature_to_4byte_selector(
        "transfer(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"], [ARBITRUM.UNISWAP.V3.UNIVERSAL_ROUTER, (int(100e6))]
    )
    function_call_0 = function_selector_0 + function_args_0

    # Encode the path for the swap (USDC to USDT)
    path = encode_packed(
        ["address", "uint24", "address"], [ARBITRUM.USDC, 100, ARBITRUM.USDT]
    )

    # Prepare inputs for the execute function call
    inputs = [
        encode(
            ["address", "uint256", "uint256", "bytes", "bool"],
            [
                "0x0000000000000000000000000000000000000001",
                (int(100e6)),
                (int(99e6)),
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
    swap = universal_token_swapper_fuse.swap(
        ARBITRUM.USDC, ARBITRUM.USDT, int(100e6), targets, data
    )

    # Execute the swap transaction
    plasma_vault.execute([swap])

    # Record balances after the swap
    vault_usdc_balance_after_swap = usdc.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)
    vault_usdt_balance_after_swap = usdt.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)

    # Calculate balance changes
    vault_usdc_balance_change = (
        vault_usdc_balance_after_swap - vault_usdc_balance_before_swap
    )
    vault_usdt_balance_change = (
        vault_usdt_balance_after_swap - vault_usdt_balance_before_swap
    )

    # Assertions to verify the results of the swap
    assert vault_usdc_balance_change == -int(
        100e6
    ), "USDC balance should decrease by the deposit amount"
    assert (
        98e6 < vault_usdt_balance_change < 100e6
    ), "USDT balance change should be between 98e6 and 100e6"


def test_should_swap_when_multiple_hop(anvil, plasma_vault, usdc, usdt):
    # Reset state and grant necessary roles
    anvil.reset_fork(254084008)
    anvil.grant_role(ARBITRUM.PILOT.V4.ACCESS_MANAGER, ANVIL_WALLET, Roles.ALPHA_ROLE)

    # Record initial balances
    vault_usdc_balance_before_swap = usdc.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)
    vault_usdt_balance_before_swap = usdt.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)

    # Define swap targets and data for multi-hop
    targets = [ARBITRUM.USDC, ARBITRUM.UNISWAP.V3.UNIVERSAL_ROUTER]

    # First function call: transfer depositAmount of USDC to router
    function_selector_0 = function_signature_to_4byte_selector(
        "transfer(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"], [ARBITRUM.UNISWAP.V3.UNIVERSAL_ROUTER, (int(100e6))]
    )
    function_call_0 = function_selector_0 + function_args_0

    # Path encoding for USDC -> WETH -> USDT swap
    path = encode_packed(
        ["address", "uint24", "address", "uint24", "address"],
        [ARBITRUM.USDC, 500, ARBITRUM.WETH, 3000, ARBITRUM.USDT],
    )

    # Second function call: execute swap with encoded path and parameters
    inputs = [
        encode(
            ["address", "uint256", "uint256", "bytes", "bool"],
            [
                "0x0000000000000000000000000000000000000001",
                int(100e6),
                int(99e6),
                path,
                False,
            ],
        )
    ]
    function_selector_1 = function_signature_to_4byte_selector("execute(bytes,bytes[])")
    function_args_1 = encode(
        ["bytes", "bytes[]"], [encode_packed(["bytes1"], [bytes.fromhex("00")]), inputs]
    )
    function_call_1 = function_selector_1 + function_args_1

    # Combined data for swap
    data = [function_call_0, function_call_1]

    # Initiate the swap through the plasma vault
    swap = universal_token_swapper_fuse.swap(
        ARBITRUM.USDC, ARBITRUM.USDT, int(100e6), targets, data
    )

    # Execute swap
    plasma_vault.execute([swap])

    # Record balances after swap
    vault_usdc_balance_after_swap = usdc.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)
    vault_usdt_balance_after_swap = usdt.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)

    # Calculate balance changes
    usdc_balance_change = vault_usdc_balance_after_swap - vault_usdc_balance_before_swap
    usdt_balance_change = vault_usdt_balance_after_swap - vault_usdt_balance_before_swap

    # Assertions on balance changes to confirm swap
    assert usdc_balance_change == -int(
        100e6
    ), "USDC balance change should match deposit amount (-100e6)"
    assert (
        98e6 < usdt_balance_change < 100e6
    ), "USDT balance change should be between 98e6 and 100e6"


def test_should_open_new_position_uniswap_v3(anvil, plasma_vault, usdc, usdt):
    # Reset state and grant necessary roles
    anvil.reset_fork(254084008)
    anvil.grant_role(ARBITRUM.PILOT.V4.ACCESS_MANAGER, ANVIL_WALLET, Roles.ALPHA_ROLE)

    # Swap USDC to USDT
    swap = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=100,
        token_in_amount=int(500e6),
        min_out_amount=0,
    )
    plasma_vault.execute([swap])

    # Check balances after swap
    vault_usdc_balance_after_swap = usdc.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)
    vault_usdt_balance_after_swap = usdt.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)

    # Create a new position with specified parameters
    new_position = uniswap_v3_new_position_fuse.new_position(
        token0=ARBITRUM.USDC,
        token1=ARBITRUM.USDT,
        fee=100,
        tick_lower=-100,
        tick_upper=101,
        amount0_desired=int(499e6),
        amount1_desired=int(499e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
    )

    # Execute the creation of the new position
    plasma_vault.execute([new_position])

    # Check balances after opening the new position
    vault_usdc_balance_after_new_position = usdc.balance_of(
        ARBITRUM.PILOT.V4.PLASMA_VAULT
    )
    vault_usdt_balance_after_new_position = usdt.balance_of(
        ARBITRUM.PILOT.V4.PLASMA_VAULT
    )

    # Assert on balance changes after creating the new position
    usdc_change = vault_usdc_balance_after_new_position - vault_usdc_balance_after_swap
    usdt_change = vault_usdt_balance_after_new_position - vault_usdt_balance_after_swap

    assert usdc_change == -int(
        499e6
    ), "USDC balance after new position does not match expected change of -499e6"
    assert (
        usdt_change == -489_152502
    ), "USDT balance after new position does not match expected change of -489_152502"


def test_should_collect_all_after_decrease_liquidity(anvil, plasma_vault, usdc, usdt):
    # Reset state and grant necessary roles
    anvil.reset_fork(254084008)
    anvil.grant_role(ARBITRUM.PILOT.V4.ACCESS_MANAGER, ANVIL_WALLET, Roles.ALPHA_ROLE)

    # Swap USDC to USDT
    swap = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=100,
        token_in_amount=int(500e6),
        min_out_amount=0,
    )
    plasma_vault.execute([swap])

    # Create a new position with specified parameters
    new_position = uniswap_v3_new_position_fuse.new_position(
        token0=ARBITRUM.USDC,
        token1=ARBITRUM.USDT,
        fee=100,
        tick_lower=-100,
        tick_upper=101,
        amount0_desired=int(499e6),
        amount1_desired=int(499e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
    )
    tx = plasma_vault.execute([new_position])

    # Extract data from the new position creation event
    _, new_token_id, liquidity, *_ = extract_enter_data_form_new_position_event(tx)

    # Decrease the liquidity of the newly created position
    decrease_action = uniswap_v_3_modify_position_fuse.decrease_position(
        token_id=new_token_id,
        liquidity=liquidity,
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
    )
    plasma_vault.execute([decrease_action])

    # Check balances before the collect action
    vault_usdc_balance_before = usdc.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)
    vault_usdt_balance_before = usdt.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)

    # Perform the collect action
    collect = uniswap_v_3_collect_fuse.collect(token_ids=[new_token_id])
    plasma_vault.execute([collect])

    # Check balances after the collect action
    vault_usdc_balance_after = usdc.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)
    vault_usdt_balance_after = usdt.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)

    collect_usdc_change = vault_usdc_balance_after - vault_usdc_balance_before
    collect_usdt_change = vault_usdt_balance_after - vault_usdt_balance_before

    # Assert on balance changes after collect action
    assert (
        498_000000 < collect_usdc_change < 500_000000
    ), "USDC balance after collect is out of expected range"
    assert (
        489_000000 < collect_usdt_change < 500_000000
    ), "USDT balance after collect is out of expected range"

    # Close the position
    close_position = uniswap_v3_new_position_fuse.close_position(
        token_ids=[new_token_id]
    )
    receipt = plasma_vault.execute([close_position])

    # Extract data from the position closing event
    _, close_token_id = extract_exit_data_form_new_position_event(receipt)

    # Assert that the token ID of the new position matches the closed position's token ID
    assert (
        new_token_id == close_token_id
    ), "Token ID of new position does not match closed position"


def test_should_increase_liquidity(anvil, plasma_vault, usdc, usdt):
    # Setup: Reset state and grant necessary roles
    anvil.reset_fork(254084008)
    anvil.grant_role(ARBITRUM.PILOT.V4.ACCESS_MANAGER, ANVIL_WALLET, Roles.ALPHA_ROLE)

    # Initial swap from USDC to USDT
    swap = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=100,
        token_in_amount=int(500e6),
        min_out_amount=0,
    )
    plasma_vault.execute([swap])

    # Create a new liquidity position
    new_position = uniswap_v3_new_position_fuse.new_position(
        token0=ARBITRUM.USDC,
        token1=ARBITRUM.USDT,
        fee=100,
        tick_lower=-100,
        tick_upper=101,
        amount0_desired=int(400e6),
        amount1_desired=int(400e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
    )
    receipt = plasma_vault.execute([new_position])

    # Extract the new token ID from the receipt
    _, new_token_id, *_ = extract_enter_data_form_new_position_event(receipt)

    # Prepare to increase liquidity for the existing position
    increase_position = uniswap_v_3_modify_position_fuse.increase_position(
        token0=ARBITRUM.USDC,
        token1=ARBITRUM.USDT,
        token_id=new_token_id,
        amount0_desired=int(99e6),
        amount1_desired=int(99e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
    )

    # Record balances before increasing liquidity
    vault_usdc_balance_before_increase = usdc.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)
    vault_usdt_balance_before_increase = usdt.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)

    # Execute the increase liquidity operation
    plasma_vault.execute([increase_position])

    # Record balances after increasing liquidity
    vault_usdc_balance_after_increase = usdc.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)
    vault_usdt_balance_after_increase = usdt.balance_of(ARBITRUM.PILOT.V4.PLASMA_VAULT)

    # Calculate balance changes
    increase_position_change_usdc = (
        vault_usdc_balance_after_increase - vault_usdc_balance_before_increase
    )
    increase_position_change_usdt = (
        vault_usdt_balance_after_increase - vault_usdt_balance_before_increase
    )

    # Assertions to verify the changes in balance
    assert increase_position_change_usdc == -int(
        99e6
    ), "USDC balance should decrease by 99,000,000"
    assert increase_position_change_usdt == -int(
        97_046288
    ), "USDT balance should decrease by 97,046,288"


def extract_enter_data_form_new_position_event(
    receipt: TxReceipt,
) -> (str, int, int, int, int, str, str, int, int, int):
    event_signature_hash = Web3.keccak(
        text="UniswapV3NewPositionFuseEnter(address,uint256,uint128,uint256,uint256,address,address,uint24,int24,int24)"
    )

    for evnet_log in receipt.logs:
        if evnet_log.topics[0] == event_signature_hash:
            decoded_data = decode(
                [
                    "address",
                    "uint256",
                    "uint128",
                    "uint256",
                    "uint256",
                    "address",
                    "address",
                    "uint24",
                    "int24",
                    "int24",
                ],
                evnet_log["data"],
            )
            (
                version,
                token_id,
                liquidity,
                amount0,
                amount1,
                sender,
                recipient,
                fee,
                tick_lower,
                tick_upper,
            ) = decoded_data
            return (
                version,
                token_id,
                liquidity,
                amount0,
                amount1,
                sender,
                recipient,
                fee,
                tick_lower,
                tick_upper,
            )
    return None, None, None, None, None, None, None, None, None, None


def extract_exit_data_form_new_position_event(receipt: TxReceipt) -> (str, int):
    event_signature_hash = Web3.keccak(
        text="UniswapV3NewPositionFuseExit(address,uint256)"
    )

    for event_log in receipt.logs:
        if event_log.topics[0] == event_signature_hash:
            decoded_data = decode(
                [
                    "address",
                    "uint256",
                ],
                event_log["data"],
            )
            (
                version,
                token_id,
            ) = decoded_data
            return (
                version,
                token_id,
            )
    return None, None

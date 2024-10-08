import logging
import os

import pytest
from eth_abi import decode, encode
from eth_abi.packed import encode_packed
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import TxReceipt

from commons import execute_transaction, read_token_balance
from constants import (
    ANVIL_WALLET,
    ARBITRUM,
)
from ipor_fusion.VaultExecuteCallFactory import VaultExecuteCallFactory
from ipor_fusion.fuse.UniswapV3CollectFuse import UniswapV3CollectFuse
from ipor_fusion.fuse.UniswapV3ModifyPositionFuse import UniswapV3ModifyPositionFuse
from ipor_fusion.fuse.UniswapV3NewPositionFuse import UniswapV3NewPositionFuse
from ipor_fusion.fuse.UniswapV3SwapFuse import UniswapV3SwapFuse
from ipor_fusion.fuse.UniversalTokenSwapperFuse import UniversalTokenSwapperFuse

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

ARBITRUM_PROVIDER_URL = "ARBITRUM_PROVIDER_URL"
FORK_URL = os.getenv(ARBITRUM_PROVIDER_URL)
if not FORK_URL:
    raise ValueError("Environment variable ARBITRUM_PROVIDER_URL must be set")

SET_ANVIL_WALLET_AS_PILOT_V4_ALPHA_COMMAND = [
    "cast",
    "send",
    "--unlocked",
    "--from",
    "0x4E3C666F0c898a9aE1F8aBB188c6A2CC151E17fC",
    ARBITRUM.PILOT.V4.ACCESS_MANAGER,
    "grantRole(uint64,address,uint32)()",
    "200",
    ANVIL_WALLET,
    "0",
]

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


@pytest.fixture(scope="module", name="vault_execute_call_factory")
def vault_execute_call_factory_fixture() -> VaultExecuteCallFactory:
    return VaultExecuteCallFactory()


@pytest.fixture(name="setup", autouse=True)
def setup_fixture(anvil):
    anvil.reset_fork(254084008)
    anvil.execute_in_container(SET_ANVIL_WALLET_AS_PILOT_V4_ALPHA_COMMAND)
    yield


def test_should_swap_when_one_hop_uniswap_v3(web3, account, vault_execute_call_factory):
    # given
    depositAmount = int(100e6)
    minOutAmount = int(99e6)

    vault_usdc_balance_before_swap = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
    )
    vault_usdt_balance_before_swap = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDT
    )

    targets = [ARBITRUM.USDC, ARBITRUM.UNISWAP.V3.UNIVERSAL_ROUTER]

    function_selector_0 = function_signature_to_4byte_selector(
        "transfer(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"], [ARBITRUM.UNISWAP.V3.UNIVERSAL_ROUTER, depositAmount]
    )
    function_call_0 = function_selector_0 + function_args_0

    path = encode_packed(
        ["address", "uint24", "address"],
        [ARBITRUM.USDC, 100, ARBITRUM.USDT],
    )
    inputs = [
        encode(
            ["address", "uint256", "uint256", "bytes", "bool"],
            [
                "0x0000000000000000000000000000000000000001",
                depositAmount,
                minOutAmount,
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

    data = [function_call_0, function_call_1]

    swap = universal_token_swapper_fuse.swap(
        ARBITRUM.USDC, ARBITRUM.USDT, depositAmount, targets, data
    )

    # when
    execute_transaction(
        web3,
        ARBITRUM.PILOT.V4.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(swap),
        account,
    )

    # then
    vault_usdc_balance_after_swap = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
    )
    vault_usdt_balance_after_swap = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDT
    )

    vault_usdc_balance_change = (
        vault_usdc_balance_after_swap - vault_usdc_balance_before_swap
    )
    vault_usdt_balance_change = (
        vault_usdt_balance_after_swap - vault_usdt_balance_before_swap
    )

    assert (
        vault_usdc_balance_change == -depositAmount
    ), "vault_usdc_balance_change == -depositAmount"
    assert (
        98e6 < vault_usdt_balance_change < 100e6
    ), "98e6 < vault_usdt_balance_change < 100e6"


def test_should_swap_when_multiple_hop(web3, account, vault_execute_call_factory):
    # given
    depositAmount = int(100e6)
    minOutAmount = int(99e6)

    vault_usdc_balance_before_swap = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
    )
    vault_usdt_balance_before_swap = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDT
    )

    targets = [ARBITRUM.USDC, ARBITRUM.UNISWAP.V3.UNIVERSAL_ROUTER]

    function_selector_0 = function_signature_to_4byte_selector(
        "transfer(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"], [ARBITRUM.UNISWAP.V3.UNIVERSAL_ROUTER, depositAmount]
    )
    function_call_0 = function_selector_0 + function_args_0

    path = encode_packed(
        ["address", "uint24", "address", "uint24", "address"],
        [ARBITRUM.USDC, 500, ARBITRUM.WETH, 3000, ARBITRUM.USDT],
    )
    inputs = [
        encode(
            ["address", "uint256", "uint256", "bytes", "bool"],
            [
                "0x0000000000000000000000000000000000000001",
                depositAmount,
                minOutAmount,
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

    data = [function_call_0, function_call_1]

    swap = universal_token_swapper_fuse.swap(
        ARBITRUM.USDC, ARBITRUM.USDT, depositAmount, targets, data
    )

    # when
    execute_transaction(
        web3,
        ARBITRUM.PILOT.V4.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(swap),
        account,
    )

    # then
    vault_usdc_balance_after_swap = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
    )
    vault_usdt_balance_after_swap = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDT
    )

    vault_usdc_balance_change = (
        vault_usdc_balance_after_swap - vault_usdc_balance_before_swap
    )
    vault_usdt_balance_change = (
        vault_usdt_balance_after_swap - vault_usdt_balance_before_swap
    )

    assert (
        vault_usdc_balance_change == -depositAmount
    ), "vault_usdc_balance_change == -depositAmount"
    assert (
        98e6 < vault_usdt_balance_change < 100e6
    ), "98e6 < vault_usdt_balance_change < 100e6"


def test_should_open_new_position_uniswap_v3(web3, account, vault_execute_call_factory):
    # given
    timestamp = web3.eth.get_block("latest")["timestamp"]

    token_in_amount = int(500e6)
    min_out_amount = 0
    fee = 100

    swap = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=fee,
        token_in_amount=token_in_amount,
        min_out_amount=min_out_amount,
    )

    execute_transaction(
        web3,
        ARBITRUM.PILOT.V4.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(swap),
        account,
    )

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
        deadline=timestamp + 100,
    )

    vault_usdc_balance_after_swap = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
    )
    vault_usdt_balance_after_swap = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDT
    )

    # when
    execute_transaction(
        web3,
        ARBITRUM.PILOT.V4.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(new_position),
        account,
    )

    # then
    vault_usdc_balance_after_new_position = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
    )
    vault_usdt_balance_after_new_position = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDT
    )

    assert vault_usdc_balance_after_new_position - vault_usdc_balance_after_swap == -int(
        499e6
    ), "vault_usdc_balance_after_new_position - vault_usdc_balance_after_swap == -499e6"
    assert (
        vault_usdt_balance_after_new_position - vault_usdt_balance_after_swap
        == -489_152502
    ), "vault_usdt_balance_after_new_position - vault_usdt_balance_after_swap == -499e6"


def test_should_collect_all_after_decrease_liquidity(
    web3, account, vault_execute_call_factory
):
    # given
    timestamp = web3.eth.get_block("latest")["timestamp"]

    token_in_amount = int(500e6)
    min_out_amount = 0
    fee = 100

    action = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=fee,
        token_in_amount=token_in_amount,
        min_out_amount=min_out_amount,
    )

    execute_transaction(
        web3,
        ARBITRUM.PILOT.V4.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(action),
        account,
    )

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
        deadline=timestamp + 100,
    )

    receipt = execute_transaction(
        web3,
        ARBITRUM.PILOT.V4.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(new_position),
        account,
    )

    (
        _,
        new_token_id,
        liquidity,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
    ) = extract_enter_data_form_new_position_event(receipt)

    decrease_action = uniswap_v_3_modify_position_fuse.decrease_position(
        token_id=new_token_id,
        liquidity=liquidity,
        amount0_min=0,
        amount1_min=0,
        deadline=timestamp + 100000,
    )

    execute_transaction(
        web3,
        ARBITRUM.PILOT.V4.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(decrease_action),
        account,
    )

    vault_usdc_balance_before_collect = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
    )
    vault_usdt_balance_before_collect = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDT
    )

    enter_action = uniswap_v_3_collect_fuse.collect(
        token_ids=[new_token_id],
    )

    execute_transaction(
        web3,
        ARBITRUM.PILOT.V4.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(enter_action),
        account,
    )

    # then
    vault_usdc_balance_after_collect = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
    )
    vault_usdt_balance_after_collect = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDT
    )

    collect_usdc_change = (
        vault_usdc_balance_after_collect - vault_usdc_balance_before_collect
    )
    collect_usdt_change = (
        vault_usdt_balance_after_collect - vault_usdt_balance_before_collect
    )

    assert (
        int(498_000000) < collect_usdc_change < int(500_000000)
    ), "int(498_000000) < collect_usdc_change < int(500_000000)"
    assert (
        int(489_000000) < collect_usdt_change < int(500_000000)
    ), "int(489_000000) < collect_usdt_change < int(500_000000)"

    close_position_action = uniswap_v3_new_position_fuse.close_position(
        token_ids=[new_token_id]
    )

    receipt = execute_transaction(
        web3,
        ARBITRUM.PILOT.V4.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(
            close_position_action
        ),
        account,
    )

    (
        _,
        close_token_id,
    ) = extract_exit_data_form_new_position_event(receipt)

    assert new_token_id == close_token_id, "new_token_id == close_token_id"


def test_should_increase_liquidity(web3, account, vault_execute_call_factory):
    # given
    timestamp = web3.eth.get_block("latest")["timestamp"]

    token_in_amount = int(500e6)
    min_out_amount = 0
    fee = 100

    action = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=fee,
        token_in_amount=token_in_amount,
        min_out_amount=min_out_amount,
    )

    function_swap = vault_execute_call_factory.create_execute_call_from_action(action)

    execute_transaction(
        web3,
        ARBITRUM.PILOT.V4.PLASMA_VAULT,
        function_swap,
        account,
    )

    position_action = uniswap_v3_new_position_fuse.new_position(
        token0=ARBITRUM.USDC,
        token1=ARBITRUM.USDT,
        fee=100,
        tick_lower=-100,
        tick_upper=101,
        amount0_desired=int(400e6),
        amount1_desired=int(400e6),
        amount0_min=0,
        amount1_min=0,
        deadline=timestamp + 100,
    )

    receipt = execute_transaction(
        web3,
        ARBITRUM.PILOT.V4.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(position_action),
        account,
    )

    (
        _,
        new_token_id,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
    ) = extract_enter_data_form_new_position_event(receipt)

    # Increase Uniswap V3 position

    increase_action = uniswap_v_3_modify_position_fuse.increase_position(
        token0=ARBITRUM.USDC,
        token1=ARBITRUM.USDT,
        token_id=new_token_id,
        amount0_desired=int(99e6),
        amount1_desired=int(99e6),
        amount0_min=0,
        amount1_min=0,
        deadline=timestamp + 100,
    )

    vault_usdc_balance_before_increase = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
    )
    vault_usdt_balance_before_increase = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDT
    )

    # when
    execute_transaction(
        web3,
        ARBITRUM.PILOT.V4.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(increase_action),
        account,
    )

    # then
    vault_usdc_balance_after_increase = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
    )
    vault_usdt_balance_after_increase = read_token_balance(
        web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDT
    )

    increase_position_change_usdc = (
        vault_usdc_balance_after_increase - vault_usdc_balance_before_increase
    )
    increase_position_change_usdt = (
        vault_usdt_balance_after_increase - vault_usdt_balance_before_increase
    )

    assert (
        increase_position_change_usdc == -99_000000
    ), "increase_position_change_usdc == -99_000000"
    assert (
        increase_position_change_usdt == -97_046288
    ), "increase_position_change_usdt == -97_046288"


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

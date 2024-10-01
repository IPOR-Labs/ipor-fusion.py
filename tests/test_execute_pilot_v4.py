import logging
import os

import pytest
from eth_abi import decode
from web3 import Web3
from web3.types import TxReceipt

from commons import execute_transaction, read_token_balance
from constants import (
    ANVIL_WALLET,
    USDC,
    USDT,
    SWAP_FUSE_UNISWAP_V3_ADDRESS,
    PLASMA_VAULT_V4,
    IPOR_FUSION_V4_ACCESS_MANAGER_USDC_ADDRESS,
    NEW_POSITION_SWAP_FUSE_UNISWAP_V3_ADDRESS,
    MODIFY_POSITION_SWAP_FUSE_UNISWAP_V3_ADDRESS,
    COLLECT_SWAP_FUSE_UNISWAP_V3_ADDRESS,
)
from ipor_fusion_sdk.VaultExecuteCallFactory import VaultExecuteCallFactory
from ipor_fusion_sdk.fuse.UniswapV3CollectFuse import UniswapV3CollectFuse
from ipor_fusion_sdk.fuse.UniswapV3ModifyPositionFuse import UniswapV3ModifyPositionFuse
from ipor_fusion_sdk.fuse.UniswapV3NewPositionFuse import UniswapV3NewPositionFuse
from ipor_fusion_sdk.fuse.UniswapV3SwapFuse import UniswapV3SwapFuse
from ipor_fusion_sdk.operation.BaseOperation import MarketId
from ipor_fusion_sdk.operation.ClosePosition import ClosePosition
from ipor_fusion_sdk.operation.Collect import Collect
from ipor_fusion_sdk.operation.DecreasePosition import DecreasePosition
from ipor_fusion_sdk.operation.IncreasePosition import IncreasePosition
from ipor_fusion_sdk.operation.NewPosition import NewPosition
from ipor_fusion_sdk.operation.Swap import Swap

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
    IPOR_FUSION_V4_ACCESS_MANAGER_USDC_ADDRESS,
    "grantRole(uint64,address,uint32)()",
    "200",
    ANVIL_WALLET,
    "0",
]


@pytest.fixture(scope="module", name="vault_execute_call_factory")
def vault_execute_call_factory_fixture() -> VaultExecuteCallFactory:
    uniswap_v3_swap_fuse = UniswapV3SwapFuse(SWAP_FUSE_UNISWAP_V3_ADDRESS)
    uniswap_v_3_new_position_fuse = UniswapV3NewPositionFuse(
        NEW_POSITION_SWAP_FUSE_UNISWAP_V3_ADDRESS
    )
    uniswap_v_3_modify_position_fuse = UniswapV3ModifyPositionFuse(
        MODIFY_POSITION_SWAP_FUSE_UNISWAP_V3_ADDRESS
    )
    uniswap_v_3_collect_fuse = UniswapV3CollectFuse(
        COLLECT_SWAP_FUSE_UNISWAP_V3_ADDRESS
    )
    return VaultExecuteCallFactory(
        {
            uniswap_v3_swap_fuse,
            uniswap_v_3_new_position_fuse,
            uniswap_v_3_modify_position_fuse,
            uniswap_v_3_collect_fuse,
        }
    )


@pytest.fixture(name="setup", autouse=True)
def setup_fixture(anvil):
    anvil.reset_fork(254084008)
    anvil.execute_in_container(SET_ANVIL_WALLET_AS_PILOT_V4_ALPHA_COMMAND)
    yield


def test_should_swap_when_one_hop_uniswap_v3(web3, account, vault_execute_call_factory):
    # given
    token_in_amount = int(100e6)
    min_out_amount = 0
    fee = 100

    swap = Swap(
        MarketId(UniswapV3SwapFuse.PROTOCOL_ID, "swap"),
        USDC,
        USDT,
        fee,
        token_in_amount,
        min_out_amount,
    )

    function = vault_execute_call_factory.create_execute_call([swap])

    vault_usdc_balance_before = read_token_balance(web3, PLASMA_VAULT_V4, USDC)
    vault_usdt_balance_before = read_token_balance(web3, PLASMA_VAULT_V4, USDT)

    # when
    execute_transaction(web3, PLASMA_VAULT_V4, function, account)

    # then
    vault_usdc_balance_after = read_token_balance(web3, PLASMA_VAULT_V4, USDC)
    vault_usdt_balance_after = read_token_balance(web3, PLASMA_VAULT_V4, USDT)

    assert (
        vault_usdc_balance_after - vault_usdc_balance_before == -token_in_amount
    ), "vault_usdc_balance_before - vault_usdc_balance_after == token_in_amount"
    assert vault_usdt_balance_after - vault_usdt_balance_before > int(
        90e6
    ), "vault_usdt_balance_after - vault_usdt_balance_before  > 90e6"


def test_should_open_two_new_position_uniswap_v3(
    web3, account, vault_execute_call_factory
):
    # given
    timestamp = web3.eth.get_block("latest")["timestamp"]

    token_in_amount = int(500e6)
    min_out_amount = 0
    fee = 100

    swap = Swap(
        MarketId(UniswapV3SwapFuse.PROTOCOL_ID, "swap"),
        USDC,
        USDT,
        fee,
        token_in_amount,
        min_out_amount,
    )

    function_swap = vault_execute_call_factory.create_execute_call([swap])

    execute_transaction(web3, PLASMA_VAULT_V4, function_swap, account)

    new_position = NewPosition(
        market_id=MarketId(UniswapV3SwapFuse.PROTOCOL_ID, "new-position"),
        token0=USDC,
        token1=USDT,
        fee=100,
        tick_lower=-100,
        tick_upper=101,
        amount0_desired=int(499e6),
        amount1_desired=int(499e6),
        amount0_min=0,
        amount1_min=0,
        deadline=timestamp + 100,
    )

    function = vault_execute_call_factory.create_execute_call([new_position])

    vault_usdc_balance_after_swap = read_token_balance(web3, PLASMA_VAULT_V4, USDC)
    vault_usdt_balance_after_swap = read_token_balance(web3, PLASMA_VAULT_V4, USDT)

    # when
    execute_transaction(web3, PLASMA_VAULT_V4, function, account)

    # then
    vault_usdc_balance_after_new_position = read_token_balance(
        web3, PLASMA_VAULT_V4, USDC
    )
    vault_usdt_balance_after_new_position = read_token_balance(
        web3, PLASMA_VAULT_V4, USDT
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

    swap = Swap(
        MarketId(UniswapV3SwapFuse.PROTOCOL_ID, "swap"),
        USDC,
        USDT,
        fee,
        token_in_amount,
        min_out_amount,
    )

    execute_transaction(
        web3,
        PLASMA_VAULT_V4,
        vault_execute_call_factory.create_execute_call([swap]),
        account,
    )

    new_position = NewPosition(
        market_id=MarketId(UniswapV3SwapFuse.PROTOCOL_ID, "new-position"),
        token0=USDC,
        token1=USDT,
        fee=100,
        tick_lower=-100,
        tick_upper=101,
        amount0_desired=int(499e6),
        amount1_desired=int(499e6),
        amount0_min=0,
        amount1_min=0,
        deadline=timestamp + 100,
    )

    function = vault_execute_call_factory.create_execute_call([new_position])

    receipt = execute_transaction(web3, PLASMA_VAULT_V4, function, account)

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

    # Decrease Uniswap V3 position
    decrease_position = DecreasePosition(
        market_id=MarketId(UniswapV3ModifyPositionFuse.PROTOCOL_ID, "modify-position"),
        token_id=new_token_id,
        liquidity=liquidity,
        amount0_min=0,
        amount1_min=0,
        deadline=timestamp + 100000,
    )

    execute_transaction(
        web3,
        PLASMA_VAULT_V4,
        vault_execute_call_factory.create_execute_call([decrease_position]),
        account,
    )

    vault_usdc_balance_before_collect = read_token_balance(web3, PLASMA_VAULT_V4, USDC)
    vault_usdt_balance_before_collect = read_token_balance(web3, PLASMA_VAULT_V4, USDT)

    # Collect
    collect = Collect(
        market_id=MarketId(UniswapV3CollectFuse.PROTOCOL_ID, "collect"),
        token_ids=[new_token_id],
    )
    execute_transaction(
        web3,
        PLASMA_VAULT_V4,
        vault_execute_call_factory.create_execute_call([collect]),
        account,
    )

    # when

    # then
    vault_usdc_balance_after_collect = read_token_balance(web3, PLASMA_VAULT_V4, USDC)
    vault_usdt_balance_after_collect = read_token_balance(web3, PLASMA_VAULT_V4, USDT)

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

    close_position = ClosePosition(
        market_id=MarketId(UniswapV3NewPositionFuse.PROTOCOL_ID, "new-position"),
        token_ids=[new_token_id],
    )

    receipt = execute_transaction(
        web3,
        PLASMA_VAULT_V4,
        vault_execute_call_factory.create_execute_call([close_position]),
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

    swap = Swap(
        MarketId(UniswapV3SwapFuse.PROTOCOL_ID, "swap"),
        USDC,
        USDT,
        fee,
        token_in_amount,
        min_out_amount,
    )

    execute_transaction(
        web3,
        PLASMA_VAULT_V4,
        vault_execute_call_factory.create_execute_call([swap]),
        account,
    )

    new_position = NewPosition(
        market_id=MarketId(UniswapV3SwapFuse.PROTOCOL_ID, "new-position"),
        token0=USDC,
        token1=USDT,
        fee=100,
        tick_lower=-100,
        tick_upper=101,
        amount0_desired=int(400e6),
        amount1_desired=int(400e6),
        amount0_min=0,
        amount1_min=0,
        deadline=timestamp + 100,
    )

    function = vault_execute_call_factory.create_execute_call([new_position])

    receipt = execute_transaction(web3, PLASMA_VAULT_V4, function, account)

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
    increase_position = IncreasePosition(
        market_id=MarketId(UniswapV3ModifyPositionFuse.PROTOCOL_ID, "modify-position"),
        token0=USDC,
        token1=USDT,
        token_id=new_token_id,
        amount0_desired=int(99e6),
        amount1_desired=int(99e6),
        amount0_min=0,
        amount1_min=0,
        deadline=timestamp + 100,
    )

    vault_usdc_balance_before_increase = read_token_balance(web3, PLASMA_VAULT_V4, USDC)
    vault_usdt_balance_before_increase = read_token_balance(web3, PLASMA_VAULT_V4, USDT)

    # when
    execute_transaction(
        web3,
        PLASMA_VAULT_V4,
        vault_execute_call_factory.create_execute_call([increase_position]),
        account,
    )

    # then
    vault_usdc_balance_after_increase = read_token_balance(web3, PLASMA_VAULT_V4, USDC)
    vault_usdt_balance_after_increase = read_token_balance(web3, PLASMA_VAULT_V4, USDT)

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

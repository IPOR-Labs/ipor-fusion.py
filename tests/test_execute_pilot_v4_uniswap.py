import os
import time

import pytest
from eth_abi import decode, encode
from eth_abi.packed import encode_packed
from eth_typing import BlockNumber
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import TxReceipt

from addresses import ARBITRUM_USDC, ARBITRUM_USDT, ARBITRUM_WETH
from constants import (
    ANVIL_WALLET,
    ARBITRUM_PILOT_V4_PLASMA_VAULT,
    ARBITRUM_UNISWAP_V3_SWAP_FUSE,
    ARBITRUM_V4_UNISWAP_V3_NEW_POSITION_FUSE,
    ARBITRUM_UNISWAP_V3_MODIFY_POSITION_FUSE,
    ARBITRUM_UNISWAP_V3_COLLECT_FUSE,
    ARBITRUM_UNIVERSAL_SWAP_FUSE,
)
from ipor_fusion.testing import AnvilTestContainerStarter, ForkedWeb3Context
from ipor_fusion import Roles, PlasmaVault, AccessManager, ERC20
from ipor_fusion.fuses import (
    UniswapV3SwapFuse,
    UniswapV3NewPositionFuse,
    UniswapV3ModifyPositionFuse,
    UniswapV3CollectFuse,
    UniversalTokenSwapperFuse,
)

fork_url = os.environ["ARBITRUM_PROVIDER_URL"]


@pytest.fixture(scope="module")
def anvil():
    with AnvilTestContainerStarter(fork_url, BlockNumber(254084008)) as a:
        yield a


uniswap_v3_universal_router = Web3.to_checksum_address(
    "0x5E325eDA8064b456f4781070C0738d849c824258"
)


def test_should_swap_when_one_hop_uniswap_v3(anvil):
    anvil.reset_fork(254084008)

    vault_address = ARBITRUM_PILOT_V4_PLASMA_VAULT
    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )

    # Grant roles
    owner = access_manager.owner()
    forked_ctx.prank(owner)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)

    forked_ctx.prank(ANVIL_WALLET)

    usdc = ERC20(forked_ctx, ARBITRUM_USDC)
    usdt = ERC20(forked_ctx, ARBITRUM_USDT)

    vault_usdc_balance_before = usdc.balance_of(vault_address)
    vault_usdt_balance_before = usdt.balance_of(vault_address)

    targets = [ARBITRUM_USDC, uniswap_v3_universal_router]

    function_selector_0 = function_signature_to_4byte_selector(
        "transfer(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"], [uniswap_v3_universal_router, int(100e6)]
    )
    function_call_0 = function_selector_0 + function_args_0

    path = encode_packed(
        ["address", "uint24", "address"],
        [ARBITRUM_USDC, 100, ARBITRUM_USDT],
    )

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
        ["bytes", "bytes[]"],
        [encode_packed(["bytes1"], [bytes.fromhex("00")]), inputs],
    )
    function_call_1 = function_selector_1 + function_args_1

    data = [function_call_0, function_call_1]
    universal = UniversalTokenSwapperFuse(ARBITRUM_UNIVERSAL_SWAP_FUSE)
    swap = universal.swap(
        token_in=ARBITRUM_USDC,
        token_out=ARBITRUM_USDT,
        amount_in=int(100e6),
        targets=targets,
        data=data,
    )

    plasma_vault.execute([swap])

    vault_usdc_balance_after = usdc.balance_of(vault_address)
    vault_usdt_balance_after = usdt.balance_of(vault_address)

    usdc_change = vault_usdc_balance_after - vault_usdc_balance_before
    usdt_change = vault_usdt_balance_after - vault_usdt_balance_before

    assert usdc_change == -int(100e6)
    assert 98e6 < usdt_change < 100e6


def test_should_swap_when_multiple_hop(anvil):
    anvil.reset_fork(254084008)

    vault_address = ARBITRUM_PILOT_V4_PLASMA_VAULT
    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )

    owner = access_manager.owner()
    forked_ctx.prank(owner)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)

    forked_ctx.prank(ANVIL_WALLET)

    usdc = ERC20(forked_ctx, ARBITRUM_USDC)
    usdt = ERC20(forked_ctx, ARBITRUM_USDT)

    vault_usdc_balance_before = usdc.balance_of(vault_address)
    vault_usdt_balance_before = usdt.balance_of(vault_address)

    targets = [ARBITRUM_USDC, uniswap_v3_universal_router]

    function_selector_0 = function_signature_to_4byte_selector(
        "transfer(address,uint256)"
    )
    function_args_0 = encode(
        ["address", "uint256"], [uniswap_v3_universal_router, int(100e6)]
    )
    function_call_0 = function_selector_0 + function_args_0

    # Multi-hop: USDC -> WETH -> USDT
    path = encode_packed(
        ["address", "uint24", "address", "uint24", "address"],
        [ARBITRUM_USDC, 500, ARBITRUM_WETH, 3000, ARBITRUM_USDT],
    )

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
        ["bytes", "bytes[]"],
        [encode_packed(["bytes1"], [bytes.fromhex("00")]), inputs],
    )
    function_call_1 = function_selector_1 + function_args_1

    data = [function_call_0, function_call_1]
    universal = UniversalTokenSwapperFuse(ARBITRUM_UNIVERSAL_SWAP_FUSE)
    swap = universal.swap(
        token_in=ARBITRUM_USDC,
        token_out=ARBITRUM_USDT,
        amount_in=int(100e6),
        targets=targets,
        data=data,
    )

    plasma_vault.execute([swap])

    vault_usdc_balance_after = usdc.balance_of(vault_address)
    vault_usdt_balance_after = usdt.balance_of(vault_address)

    usdc_change = vault_usdc_balance_after - vault_usdc_balance_before
    usdt_change = vault_usdt_balance_after - vault_usdt_balance_before

    assert usdc_change == -int(100e6)
    assert 98e6 < usdt_change < 100e6


def test_should_open_new_position_uniswap_v3(anvil):
    anvil.reset_fork(254084008)

    vault_address = ARBITRUM_PILOT_V4_PLASMA_VAULT
    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )

    owner = access_manager.owner()
    forked_ctx.prank(owner)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)

    forked_ctx.prank(ANVIL_WALLET)

    usdc = ERC20(forked_ctx, ARBITRUM_USDC)
    usdt = ERC20(forked_ctx, ARBITRUM_USDT)

    uniswap_swap = UniswapV3SwapFuse(ARBITRUM_UNISWAP_V3_SWAP_FUSE)
    uniswap_new_pos = UniswapV3NewPositionFuse(ARBITRUM_V4_UNISWAP_V3_NEW_POSITION_FUSE)

    # Swap USDC to USDT first
    swap = uniswap_swap.swap(
        token_in=ARBITRUM_USDC,
        token_out=ARBITRUM_USDT,
        fee=100,
        amount_in=int(500e6),
        min_amount_out=0,
    )
    plasma_vault.execute([swap])

    vault_usdc_after_swap = usdc.balance_of(vault_address)
    vault_usdt_after_swap = usdt.balance_of(vault_address)

    # Create new position
    new_position = uniswap_new_pos.new_position(
        token0=ARBITRUM_USDC,
        token1=ARBITRUM_USDT,
        fee=100,
        tick_lower=-100,
        tick_upper=101,
        amount0_desired=int(499e6),
        amount1_desired=int(499e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
    )

    plasma_vault.execute([new_position])

    vault_usdc_after_position = usdc.balance_of(vault_address)
    vault_usdt_after_position = usdt.balance_of(vault_address)

    usdc_change = vault_usdc_after_position - vault_usdc_after_swap
    usdt_change = vault_usdt_after_position - vault_usdt_after_swap

    assert usdc_change == -int(499e6)
    assert usdt_change == -489_152502


def test_should_collect_all_after_decrease_liquidity(anvil):
    anvil.reset_fork(254084008)

    vault_address = ARBITRUM_PILOT_V4_PLASMA_VAULT
    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )

    owner = access_manager.owner()
    forked_ctx.prank(owner)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)

    forked_ctx.prank(ANVIL_WALLET)

    usdc = ERC20(forked_ctx, ARBITRUM_USDC)
    usdt = ERC20(forked_ctx, ARBITRUM_USDT)

    uniswap_swap = UniswapV3SwapFuse(ARBITRUM_UNISWAP_V3_SWAP_FUSE)
    uniswap_new_pos = UniswapV3NewPositionFuse(ARBITRUM_V4_UNISWAP_V3_NEW_POSITION_FUSE)
    uniswap_modify = UniswapV3ModifyPositionFuse(
        ARBITRUM_UNISWAP_V3_MODIFY_POSITION_FUSE
    )
    uniswap_collect = UniswapV3CollectFuse(ARBITRUM_UNISWAP_V3_COLLECT_FUSE)

    # Swap USDC to USDT
    swap = uniswap_swap.swap(
        token_in=ARBITRUM_USDC,
        token_out=ARBITRUM_USDT,
        fee=100,
        amount_in=int(500e6),
        min_amount_out=0,
    )
    plasma_vault.execute([swap])

    # Create new position
    new_position = uniswap_new_pos.new_position(
        token0=ARBITRUM_USDC,
        token1=ARBITRUM_USDT,
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

    _, new_token_id, liquidity, *_ = extract_enter_data_from_new_position_event(tx)

    # Decrease liquidity
    decrease = uniswap_modify.decrease_liquidity(
        token_id=new_token_id,
        liquidity=liquidity,
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
    )
    plasma_vault.execute([decrease])

    vault_usdc_before_collect = usdc.balance_of(vault_address)
    vault_usdt_before_collect = usdt.balance_of(vault_address)

    # Collect
    collect = uniswap_collect.collect([new_token_id])
    plasma_vault.execute([collect])

    vault_usdc_after_collect = usdc.balance_of(vault_address)
    vault_usdt_after_collect = usdt.balance_of(vault_address)

    usdc_change = vault_usdc_after_collect - vault_usdc_before_collect
    usdt_change = vault_usdt_after_collect - vault_usdt_before_collect

    assert 498_000000 < usdc_change < 500_000000
    assert 489_000000 < usdt_change < 500_000000

    # Close position
    close = uniswap_new_pos.close_position([new_token_id])
    receipt = plasma_vault.execute([close])

    _, close_token_id = extract_exit_data_from_new_position_event(receipt)

    assert new_token_id == close_token_id


def test_should_increase_liquidity(anvil):
    anvil.reset_fork(254084008)

    vault_address = ARBITRUM_PILOT_V4_PLASMA_VAULT
    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )

    owner = access_manager.owner()
    forked_ctx.prank(owner)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)

    forked_ctx.prank(ANVIL_WALLET)

    usdc = ERC20(forked_ctx, ARBITRUM_USDC)
    usdt = ERC20(forked_ctx, ARBITRUM_USDT)

    uniswap_swap = UniswapV3SwapFuse(ARBITRUM_UNISWAP_V3_SWAP_FUSE)
    uniswap_new_pos = UniswapV3NewPositionFuse(ARBITRUM_V4_UNISWAP_V3_NEW_POSITION_FUSE)
    uniswap_modify = UniswapV3ModifyPositionFuse(
        ARBITRUM_UNISWAP_V3_MODIFY_POSITION_FUSE
    )

    # Swap USDC to USDT
    swap = uniswap_swap.swap(
        token_in=ARBITRUM_USDC,
        token_out=ARBITRUM_USDT,
        fee=100,
        amount_in=int(500e6),
        min_amount_out=0,
    )
    plasma_vault.execute([swap])

    # Create new position
    new_position = uniswap_new_pos.new_position(
        token0=ARBITRUM_USDC,
        token1=ARBITRUM_USDT,
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

    _, new_token_id, *_ = extract_enter_data_from_new_position_event(receipt)

    vault_usdc_before_increase = usdc.balance_of(vault_address)
    vault_usdt_before_increase = usdt.balance_of(vault_address)

    # Increase liquidity
    increase = uniswap_modify.increase_liquidity(
        token0=ARBITRUM_USDC,
        token1=ARBITRUM_USDT,
        token_id=new_token_id,
        amount0_desired=int(99e6),
        amount1_desired=int(99e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
    )
    plasma_vault.execute([increase])

    vault_usdc_after_increase = usdc.balance_of(vault_address)
    vault_usdt_after_increase = usdt.balance_of(vault_address)

    usdc_change = vault_usdc_after_increase - vault_usdc_before_increase
    usdt_change = vault_usdt_after_increase - vault_usdt_before_increase

    assert usdc_change == -int(99e6)
    assert usdt_change == -int(97_046288)


def extract_enter_data_from_new_position_event(receipt: TxReceipt):
    event_signature = Web3.keccak(
        text="UniswapV3NewPositionFuseEnter(address,uint256,uint128,uint256,uint256,address,address,uint24,int24,int24)"
    )

    for log in receipt["logs"]:
        if log["topics"][0] == event_signature:
            decoded = decode(
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
                log["data"],
            )
            return decoded
    return (None,) * 10


def extract_exit_data_from_new_position_event(receipt: TxReceipt):
    event_signature = Web3.keccak(text="UniswapV3NewPositionFuseExit(address,uint256)")

    for log in receipt["logs"]:
        if log["topics"][0] == event_signature:
            decoded = decode(["address", "uint256"], log["data"])
            return decoded
    return None, None

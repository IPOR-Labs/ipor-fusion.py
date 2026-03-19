import os
import time

from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from eth_typing import BlockNumber
from web3.types import TxReceipt

from constants import (
    ARBITRUM_PILOT_V5_PLASMA_VAULT,
    ANVIL_WALLET,
    DAY,
    MONTH,
    ARBITRUM_UNISWAP_V3_SWAP_FUSE,
    ARBITRUM_RAMSES_V2_NEW_POSITION_FUSE,
    ARBITRUM_RAMSES_V2_MODIFY_POSITION_FUSE,
    ARBITRUM_RAMSES_V2_COLLECT_FUSE,
    ARBITRUM_RAMSES_CLAIM_FUSE,
)
from UniswapV3UniversalRouter import UniswapV3UniversalRouter
from ipor_fusion.testing import AnvilTestContainerStarter, ForkedWeb3Context
from ipor_fusion import Roles, PlasmaVault, AccessManager, ERC20, RewardsManager
from ipor_fusion.fuses import (
    UniswapV3SwapFuse,
    RamsesV2NewPositionFuse,
    RamsesV2ModifyPositionFuse,
    RamsesV2CollectFuse,
    RamsesClaimFuse,
    extract_ramses_new_position_events,
)
from ipor_fusion.addresses import (
    ARBITRUM_USDC,
    ARBITRUM_USDT,
    ARBITRUM_WETH,
    ARBITRUM_RAM_TOKEN,
    ARBITRUM_XRAM_TOKEN,
)

provider_url = os.environ["ARBITRUM_PROVIDER_URL"]

anvil = AnvilTestContainerStarter(provider_url, BlockNumber(261946538))
anvil.start()

uniswap_v_3_universal_router_address = Web3.to_checksum_address(
    "0x5E325eDA8064b456f4781070C0738d849c824258"
)


def test_should_open_new_position_ramses_v2():
    # setup
    anvil.reset_fork(261946538)

    vault_address = ARBITRUM_PILOT_V5_PLASMA_VAULT
    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )

    owner = access_manager.owner()
    forked_ctx.prank(owner)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)

    forked_ctx.prank(ANVIL_WALLET)

    # given
    usdc = ERC20(forked_ctx, ARBITRUM_USDC)
    usdt = ERC20(forked_ctx, ARBITRUM_USDT)

    uniswap_swap = UniswapV3SwapFuse(ARBITRUM_UNISWAP_V3_SWAP_FUSE)
    ramses_new_pos = RamsesV2NewPositionFuse(ARBITRUM_RAMSES_V2_NEW_POSITION_FUSE)

    swap = uniswap_swap.swap(
        token_in=ARBITRUM_USDC,
        token_out=ARBITRUM_USDT,
        fee=100,
        amount_in=int(500e6),
        min_amount_out=0,
    )

    plasma_vault.execute([swap])

    vault_usdc_balance_after_swap = usdc.balance_of(vault_address)
    vault_usdt_balance_after_swap = usdt.balance_of(vault_address)

    new_position = ramses_new_pos.new_position(
        token0=ARBITRUM_USDC,
        token1=ARBITRUM_USDT,
        fee=50,
        tick_lower=-100,
        tick_upper=100,
        amount0_desired=int(499e6),
        amount1_desired=int(499e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
        ve_ram_token_id=0,
    )

    # when
    plasma_vault.execute([new_position])

    # then
    vault_usdc_balance_after_new_position = usdc.balance_of(vault_address)
    vault_usdt_balance_after_new_position = usdt.balance_of(vault_address)

    assert (
        vault_usdc_balance_after_new_position - vault_usdc_balance_after_swap
        == -int(456_205368)
    ), ("new_position_usdc_change == -int(456_205368)")
    assert (
        vault_usdt_balance_after_new_position - vault_usdt_balance_after_swap
        == -int(499_000000)
    ), ("new_position_usdt_change == -int(499000000)")


def test_should_collect_all_after_decrease_liquidity():
    # given
    anvil.reset_fork(261946538)

    vault_address = ARBITRUM_PILOT_V5_PLASMA_VAULT
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
    ramses_new_pos = RamsesV2NewPositionFuse(ARBITRUM_RAMSES_V2_NEW_POSITION_FUSE)
    ramses_modify = RamsesV2ModifyPositionFuse(ARBITRUM_RAMSES_V2_MODIFY_POSITION_FUSE)
    ramses_collect = RamsesV2CollectFuse(ARBITRUM_RAMSES_V2_COLLECT_FUSE)

    swap_action = uniswap_swap.swap(
        token_in=ARBITRUM_USDC,
        token_out=ARBITRUM_USDT,
        fee=100,
        amount_in=int(500e6),
        min_amount_out=0,
    )

    new_position = ramses_new_pos.new_position(
        token0=ARBITRUM_USDC,
        token1=ARBITRUM_USDT,
        fee=50,
        tick_lower=-100,
        tick_upper=100,
        amount0_desired=int(499e6),
        amount1_desired=int(499e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
        ve_ram_token_id=0,
    )

    receipt = plasma_vault.execute([swap_action, new_position])

    new_position_event = extract_ramses_new_position_events(receipt)[0]

    new_token_id = new_position_event.token_id

    decrease_action = ramses_modify.decrease_liquidity(
        token_id=new_token_id,
        liquidity=new_position_event.liquidity,
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100000,
    )

    plasma_vault.execute([decrease_action])

    vault_usdc_balance_before_collect = usdc.balance_of(vault_address)
    vault_usdt_balance_before_collect = usdt.balance_of(vault_address)

    collect_action = ramses_collect.collect([new_token_id])

    plasma_vault.execute([collect_action])

    # then
    vault_usdc_balance_after_collect = usdc.balance_of(vault_address)
    vault_usdt_balance_after_collect = usdt.balance_of(vault_address)

    assert (
        vault_usdc_balance_after_collect - vault_usdc_balance_before_collect
        == 456205367
    ), "collect_usdc_change == 456205367"
    assert (
        vault_usdt_balance_after_collect - vault_usdt_balance_before_collect
        == 498999999
    ), "collect_usdt_change == 456205367"

    close_position_action = ramses_new_pos.close_position([new_token_id])

    receipt = plasma_vault.execute([close_position_action])
    _, close_token_id = extract_data_form_new_position_exit_event(receipt)

    assert new_token_id == close_token_id, "new_token_id == close_token_id"


def test_should_increase_liquidity():
    # given
    anvil.reset_fork(261946538)  # 261946538 - 1002 USDC on pilot V5

    vault_address = ARBITRUM_PILOT_V5_PLASMA_VAULT
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
    ramses_new_pos = RamsesV2NewPositionFuse(ARBITRUM_RAMSES_V2_NEW_POSITION_FUSE)
    ramses_modify = RamsesV2ModifyPositionFuse(ARBITRUM_RAMSES_V2_MODIFY_POSITION_FUSE)

    swap = uniswap_swap.swap(
        token_in=ARBITRUM_USDC,
        token_out=ARBITRUM_USDT,
        fee=100,
        amount_in=(int(500e6)),
        min_amount_out=0,
    )

    new_position = ramses_new_pos.new_position(
        token0=ARBITRUM_USDC,
        token1=ARBITRUM_USDT,
        fee=50,
        tick_lower=-100,
        tick_upper=100,
        amount0_desired=int(300e6),
        amount1_desired=int(300e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
        ve_ram_token_id=0,
    )

    receipt = plasma_vault.execute([swap, new_position])

    _, new_token_id, *_ = extract_data_form_new_position_enter_event(receipt)

    # Increase position
    increase_action = ramses_modify.increase_liquidity(
        token0=ARBITRUM_USDC,
        token1=ARBITRUM_USDT,
        token_id=new_token_id,
        amount0_desired=int(99e6),
        amount1_desired=int(99e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
    )

    vault_usdc_balance_before_increase = usdc.balance_of(vault_address)
    vault_usdt_balance_before_increase = usdt.balance_of(vault_address)

    # when
    plasma_vault.execute([increase_action])

    # then
    vault_usdc_balance_after_increase = usdc.balance_of(vault_address)
    vault_usdt_balance_after_increase = usdt.balance_of(vault_address)

    assert (
        vault_usdc_balance_after_increase - vault_usdc_balance_before_increase
        == -90_509683
    ), "increase_position_change_usdc == -90_509683"
    assert (
        vault_usdt_balance_after_increase - vault_usdt_balance_before_increase
        == -99_000000
    ), "increase_position_change_usdt == -90_509683"


def test_should_claim_rewards_from_ramses_v2_swap_and_transfer_to_rewards_manager():
    # given
    anvil.reset_fork(261946538)

    vault_address = ARBITRUM_PILOT_V5_PLASMA_VAULT
    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )
    rewards = RewardsManager(
        forked_ctx, plasma_vault.get_rewards_claim_manager_address()
    )

    owner = access_manager.owner()
    forked_ctx.prank(owner)
    access_manager.grant_role(Roles.ATOMIST_ROLE, ANVIL_WALLET, 0)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)
    access_manager.grant_role(Roles.CLAIM_REWARDS_ROLE, ANVIL_WALLET, 0)
    access_manager.grant_role(Roles.TRANSFER_REWARDS_ROLE, ANVIL_WALLET, 0)

    forked_ctx.prank(ANVIL_WALLET)

    usdc = ERC20(forked_ctx, ARBITRUM_USDC)

    uniswap_swap = UniswapV3SwapFuse(ARBITRUM_UNISWAP_V3_SWAP_FUSE)
    ramses_new_pos = RamsesV2NewPositionFuse(ARBITRUM_RAMSES_V2_NEW_POSITION_FUSE)
    ramses_claim = RamsesClaimFuse(ARBITRUM_RAMSES_CLAIM_FUSE)

    swap = uniswap_swap.swap(
        token_in=ARBITRUM_USDC,
        token_out=ARBITRUM_USDT,
        fee=100,
        amount_in=int(500e6),
        min_amount_out=0,
    )

    new_position = ramses_new_pos.new_position(
        token0=ARBITRUM_USDC,
        token1=ARBITRUM_USDT,
        fee=50,
        tick_lower=-100,
        tick_upper=100,
        amount0_desired=int(499e6),
        amount1_desired=int(499e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
        ve_ram_token_id=0,
    )

    tx_result = plasma_vault.execute([swap, new_position])

    _, new_token_id, *_ = extract_data_form_new_position_enter_event(tx_result)

    # One month later
    anvil.move_time(MONTH)

    claim_action = ramses_claim.claim(
        token_ids=[new_token_id],
        token_rewards=[
            [
                ARBITRUM_RAM_TOKEN,
                ARBITRUM_XRAM_TOKEN,
            ]
        ],
    )

    # Claim RAM rewards
    rewards.claim_rewards([claim_action])

    ram_after_claim = ERC20(forked_ctx, ARBITRUM_RAM_TOKEN).balance_of(rewards.address)

    assert ram_after_claim > 0

    # Transfer RAM to ALPHA wallet
    rewards.transfer(ARBITRUM_RAM_TOKEN, ANVIL_WALLET, ram_after_claim)

    ram_after_transfer = ERC20(forked_ctx, ARBITRUM_RAM_TOKEN).balance_of(ANVIL_WALLET)
    assert ram_after_transfer > 0

    usdc_before_swap_ram = usdc.balance_of(ANVIL_WALLET)

    weth = ERC20(forked_ctx, ARBITRUM_WETH)

    # Create context for uniswap router (needs signer)
    uniswap_v3_universal_router = UniswapV3UniversalRouter(
        ctx=forked_ctx,
        universal_router_address=uniswap_v_3_universal_router_address,
    )

    # swap RAM -> USDC
    path = [
        ARBITRUM_RAM_TOKEN,
        10000,
        weth.address,
        500,
        ARBITRUM_USDC,
    ]
    uniswap_v3_universal_router.swap(ARBITRUM_RAM_TOKEN, path, ram_after_transfer)

    usdc_after_swap_ram = usdc.balance_of(ANVIL_WALLET)

    rewards_in_usdc = usdc_after_swap_ram - usdc_before_swap_ram
    assert rewards_in_usdc > 0

    # Transfer USDC to rewards_claim_manager
    forked_ctx.send(
        ARBITRUM_USDC,
        function_signature_to_4byte_selector_transfer(rewards.address, rewards_in_usdc),
    )

    usdc_after_transfer = usdc.balance_of(ANVIL_WALLET)
    assert usdc_after_transfer == 0

    # Update balance on rewards_claim_manager
    rewards.update_balance()
    rewards_claim_manager_balance_before_vesting = usdc.balance_of(rewards.address)
    assert rewards_claim_manager_balance_before_vesting > 0

    rewards.update_balance()

    # One month later
    anvil.move_time(DAY)
    rewards.update_balance()

    rewards_claim_manager_balance_after_vesting = usdc.balance_of(rewards.address)

    assert rewards_claim_manager_balance_after_vesting == 0


def function_signature_to_4byte_selector_transfer(to, amount):
    sig = function_signature_to_4byte_selector("transfer(address,uint256)")
    return sig + encode(["address", "uint256"], [to, amount])


def extract_data_form_new_position_enter_event(
    receipt: TxReceipt,
) -> (str, int, int, int, int, str, str, int, int, int):
    event_signature_hash = Web3.keccak(
        text="RamsesV2NewPositionFuseEnter(address,uint256,uint128,uint256,uint256,address,address,uint24,int24,int24)"
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


def extract_data_form_new_position_exit_event(receipt: TxReceipt) -> (str, int):
    event_signature_hash = Web3.keccak(
        text="RamsesV2NewPositionFuseExit(address,uint256)"
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

"""PILOT v5 (Arbitrum) — Ramses V2 LP positions via `eth_simulateV1`.

Mirrors `test_execute_pilot_v5_ramses.py`. Three of four flows ported here:
open new position, decrease+collect+close (two-step for token_id), increase
liquidity (two-step). The fourth test (claim rewards) uses `move_time(MONTH)` —
multi-block already supported, but reward emissions depend on epoch-aligned
state that may not match exact assertions across forks; left as TODO.
"""

from __future__ import annotations

import logging

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3

from _simulate import assert_all_success
from addresses import (
    ARBITRUM_USDC,
    ARBITRUM_USDT,
    ARBITRUM_RAM_TOKEN,
    ARBITRUM_XRAM_TOKEN,
)
from constants import (
    ANVIL_WALLET,
    ARBITRUM_PILOT_V5_PLASMA_VAULT,
    ARBITRUM_UNISWAP_V3_SWAP_FUSE,
    ARBITRUM_RAMSES_V2_NEW_POSITION_FUSE,
    ARBITRUM_RAMSES_V2_MODIFY_POSITION_FUSE,
    ARBITRUM_RAMSES_V2_COLLECT_FUSE,
    ARBITRUM_RAMSES_CLAIM_FUSE,
    MONTH,
)
from ipor_fusion import (
    Web3Context,
    PlasmaVault,
    AccessManager,
    RewardsManager,
    Roles,
    VaultSimulator,
)
from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.fuses import (
    UniswapV3SwapFuse,
    RamsesV2NewPositionFuse,
    RamsesV2ModifyPositionFuse,
    RamsesV2CollectFuse,
    RamsesClaimFuse,
)
from ipor_fusion.types import ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PINNED_BLOCK = 261946538  # mirrors anvil.reset_fork(...)
DEADLINE_OFFSET = 100000


def _encode_call(signature: str, *args) -> bytes:
    selector = function_signature_to_4byte_selector(signature)
    types = _parse_param_types(signature)
    return selector + encode(types, list(args)) if types else selector


def _setup_alpha(sim, access_manager, owner):
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.ALPHA_ROLE, ANVIL_WALLET, 0
        ),
        from_=owner,
    )


def _extract_ramses_new_position(execute_logs):
    """Decode RamsesV2NewPositionFuseEnter event. Returns (token_id, liquidity)."""
    target_topic = HexBytes(
        Web3.keccak(
            text=(
                "RamsesV2NewPositionFuseEnter(address,uint256,uint128,uint256,"
                "uint256,address,address,uint24,int24,int24)"
            )
        )
    )
    from eth_abi import decode as abi_decode

    for log_dict in execute_logs:
        topics = log_dict.get("topics") or []
        if topics and HexBytes(topics[0]) == target_topic:
            data = HexBytes(log_dict["data"])
            decoded = abi_decode(
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
                bytes(data),
            )
            return decoded[1], decoded[2]
    raise AssertionError("RamsesV2NewPositionFuseEnter event not found in logs")


def test_simulate_open_new_position_ramses_v2(web3_arb):
    """Swap USDC→USDT then mint a Ramses V2 LP position."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V5_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()
    deadline = (
        int(web3_arb.eth.get_block(PINNED_BLOCK)["timestamp"]) + DEADLINE_OFFSET
    )

    uniswap_swap = UniswapV3SwapFuse(ARBITRUM_UNISWAP_V3_SWAP_FUSE)
    ramses_new_pos = RamsesV2NewPositionFuse(ARBITRUM_RAMSES_V2_NEW_POSITION_FUSE)

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_alpha(sim, access_manager, owner)
    sim.execute(
        [
            uniswap_swap.swap(
                token_in=ARBITRUM_USDC,
                token_out=ARBITRUM_USDT,
                fee=100,
                amount_in=int(500e6),
                min_amount_out=0,
            )
        ]
    )
    sim.observe(
        "usdc_after_swap", ARBITRUM_USDC, "balanceOf(address)", (vault_address,)
    )
    sim.observe(
        "usdt_after_swap", ARBITRUM_USDT, "balanceOf(address)", (vault_address,)
    )
    sim.execute(
        [
            ramses_new_pos.new_position(
                token0=ARBITRUM_USDC,
                token1=ARBITRUM_USDT,
                fee=50,
                tick_lower=-100,
                tick_upper=100,
                amount0_desired=int(499e6),
                amount1_desired=int(499e6),
                amount0_min=0,
                amount1_min=0,
                deadline=deadline,
                ve_ram_token_id=0,
            )
        ]
    )
    sim.observe(
        "usdc_after_new", ARBITRUM_USDC, "balanceOf(address)", (vault_address,)
    )
    sim.observe(
        "usdt_after_new", ARBITRUM_USDT, "balanceOf(address)", (vault_address,)
    )
    result = sim.run()

    log.info("observations=%s", result.observations)
    assert_all_success(result)
    assert (
        result.get("usdc_after_new") - result.get("usdc_after_swap") == -456_205368
    )
    assert (
        result.get("usdt_after_new") - result.get("usdt_after_swap") == -499_000000
    )


def _build_ramses_open_sim(web3_arb, ctx, vault_address, deadline, amount_desired):
    """Build a sim that swaps USDC→USDT and opens a Ramses position with given amounts."""
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()

    uniswap_swap = UniswapV3SwapFuse(ARBITRUM_UNISWAP_V3_SWAP_FUSE)
    ramses_new_pos = RamsesV2NewPositionFuse(ARBITRUM_RAMSES_V2_NEW_POSITION_FUSE)

    sim = VaultSimulator(
        web3=web3_arb,
        vault=vault_address,
        alpha=ANVIL_WALLET,
        block=hex(PINNED_BLOCK),
    )
    _setup_alpha(sim, access_manager, owner)
    sim.execute(
        [
            uniswap_swap.swap(
                token_in=ARBITRUM_USDC,
                token_out=ARBITRUM_USDT,
                fee=100,
                amount_in=int(500e6),
                min_amount_out=0,
            ),
            ramses_new_pos.new_position(
                token0=ARBITRUM_USDC,
                token1=ARBITRUM_USDT,
                fee=50,
                tick_lower=-100,
                tick_upper=100,
                amount0_desired=amount_desired,
                amount1_desired=amount_desired,
                amount0_min=0,
                amount1_min=0,
                deadline=deadline,
                ve_ram_token_id=0,
            ),
        ]
    )
    return sim


def test_simulate_collect_all_after_decrease_liquidity(web3_arb):
    """Open Ramses position → decrease liquidity → collect → close. Two-step sim."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V5_PLASMA_VAULT
    deadline = (
        int(web3_arb.eth.get_block(PINNED_BLOCK)["timestamp"]) + DEADLINE_OFFSET
    )

    # Step 1: extract token_id from new_position event logs
    sim1 = _build_ramses_open_sim(
        web3_arb, ctx, vault_address, deadline, int(499e6)
    )
    result1 = sim1.run()
    assert_all_success(result1)
    new_token_id, liquidity = _extract_ramses_new_position(result1.execute_logs)
    log.info("minted ramses token_id=%s liquidity=%s", new_token_id, liquidity)

    # Step 2: replay + decrease + collect + close
    ramses_modify = RamsesV2ModifyPositionFuse(ARBITRUM_RAMSES_V2_MODIFY_POSITION_FUSE)
    ramses_collect = RamsesV2CollectFuse(ARBITRUM_RAMSES_V2_COLLECT_FUSE)
    ramses_new_pos = RamsesV2NewPositionFuse(ARBITRUM_RAMSES_V2_NEW_POSITION_FUSE)

    sim2 = _build_ramses_open_sim(
        web3_arb, ctx, vault_address, deadline, int(499e6)
    )
    sim2.execute(
        [
            ramses_modify.decrease_liquidity(
                token_id=new_token_id,
                liquidity=liquidity,
                amount0_min=0,
                amount1_min=0,
                deadline=deadline,
            )
        ]
    )
    sim2.observe(
        "usdc_before_collect",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (vault_address,),
    )
    sim2.observe(
        "usdt_before_collect",
        ARBITRUM_USDT,
        "balanceOf(address)",
        (vault_address,),
    )
    sim2.execute([ramses_collect.collect([new_token_id])])
    sim2.observe(
        "usdc_after_collect",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (vault_address,),
    )
    sim2.observe(
        "usdt_after_collect",
        ARBITRUM_USDT,
        "balanceOf(address)",
        (vault_address,),
    )
    sim2.execute([ramses_new_pos.close_position([new_token_id])])
    result2 = sim2.run()

    log.info("step2 observations=%s", result2.observations)
    assert_all_success(result2)
    usdc_change = result2.get("usdc_after_collect") - result2.get("usdc_before_collect")
    usdt_change = result2.get("usdt_after_collect") - result2.get("usdt_before_collect")
    assert usdc_change == 456205367
    assert usdt_change == 498999999


def test_simulate_increase_liquidity(web3_arb):
    """Open Ramses position → increase liquidity. Two-step."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V5_PLASMA_VAULT
    deadline = (
        int(web3_arb.eth.get_block(PINNED_BLOCK)["timestamp"]) + DEADLINE_OFFSET
    )

    # Step 1: extract token_id from event
    sim1 = _build_ramses_open_sim(
        web3_arb, ctx, vault_address, deadline, int(300e6)
    )
    result1 = sim1.run()
    assert_all_success(result1)
    new_token_id, _ = _extract_ramses_new_position(result1.execute_logs)
    log.info("minted ramses token_id=%s", new_token_id)

    # Step 2: replay + increase
    ramses_modify = RamsesV2ModifyPositionFuse(ARBITRUM_RAMSES_V2_MODIFY_POSITION_FUSE)

    sim2 = _build_ramses_open_sim(
        web3_arb, ctx, vault_address, deadline, int(300e6)
    )
    sim2.observe(
        "usdc_before",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (vault_address,),
    )
    sim2.observe(
        "usdt_before",
        ARBITRUM_USDT,
        "balanceOf(address)",
        (vault_address,),
    )
    sim2.execute(
        [
            ramses_modify.increase_liquidity(
                token0=ARBITRUM_USDC,
                token1=ARBITRUM_USDT,
                token_id=new_token_id,
                amount0_desired=int(99e6),
                amount1_desired=int(99e6),
                amount0_min=0,
                amount1_min=0,
                deadline=deadline,
            )
        ]
    )
    sim2.observe(
        "usdc_after",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (vault_address,),
    )
    sim2.observe(
        "usdt_after",
        ARBITRUM_USDT,
        "balanceOf(address)",
        (vault_address,),
    )
    result2 = sim2.run()

    log.info("step2 observations=%s", result2.observations)
    assert_all_success(result2)
    usdc_change = result2.get("usdc_after") - result2.get("usdc_before")
    usdt_change = result2.get("usdt_after") - result2.get("usdt_before")
    assert usdc_change == -90_509683
    assert usdt_change == -99_000000


def test_simulate_claim_rewards_after_one_month(web3_arb):
    """Open Ramses LP position, advance 1 month, claim RAM rewards.

    Simplified port of `test_should_claim_rewards_from_ramses_v2_swap_and_transfer`.
    The original then swaps RAM→USDC via universal router (off-vault user action)
    and exercises rewards manager vesting; we keep just the claim step which is
    the core invariant: time advancement on a Ramses position accrues claimable
    RAM rewards. Two-step sim: extract token_id from new position event, then
    replay setup + multi-block move-time + claim.
    """
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V5_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    rewards = RewardsManager(ctx, plasma_vault.get_rewards_claim_manager_address())
    owner = access_manager.owner()
    deadline = (
        int(web3_arb.eth.get_block(PINNED_BLOCK)["timestamp"]) + DEADLINE_OFFSET
    )

    # Step 1: extract token_id from event
    sim1 = _build_ramses_open_sim(
        web3_arb, ctx, vault_address, deadline, int(499e6)
    )
    result1 = sim1.run()
    assert_all_success(result1)
    new_token_id, _ = _extract_ramses_new_position(result1.execute_logs)
    log.info("minted ramses token_id=%s", new_token_id)

    # Step 2: replay + multi-block month-shift + claim
    ramses_claim = RamsesClaimFuse(ARBITRUM_RAMSES_CLAIM_FUSE)

    sim2 = _build_ramses_open_sim(
        web3_arb, ctx, vault_address, deadline, int(499e6)
    )
    # Original test grants additional roles so claim_rewards can be called
    sim2.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)",
            Roles.CLAIM_REWARDS_ROLE,
            ANVIL_WALLET,
            0,
        ),
        from_=owner,
    )
    sim2.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)",
            Roles.TRANSFER_REWARDS_ROLE,
            ANVIL_WALLET,
            0,
        ),
        from_=owner,
    )

    # +1 month of time accumulation for reward emission
    sim2.next_block(time_shift_seconds=MONTH)

    sim2.observe(
        "ram_before_claim",
        ARBITRUM_RAM_TOKEN,
        "balanceOf(address)",
        (rewards.address,),
    )
    sim2.observe(
        "xram_before_claim",
        ARBITRUM_XRAM_TOKEN,
        "balanceOf(address)",
        (rewards.address,),
    )

    claim_action = ramses_claim.claim(
        token_ids=[new_token_id],
        token_rewards=[[ARBITRUM_RAM_TOKEN, ARBITRUM_XRAM_TOKEN]],
    )
    sim2.execute_on(
        target=rewards.address,
        signature="claimRewards((address,bytes)[])",
        actions=[claim_action],
    )

    sim2.observe(
        "ram_after_claim",
        ARBITRUM_RAM_TOKEN,
        "balanceOf(address)",
        (rewards.address,),
    )
    sim2.observe(
        "xram_after_claim",
        ARBITRUM_XRAM_TOKEN,
        "balanceOf(address)",
        (rewards.address,),
    )

    result2 = sim2.run()
    log.info("claim observations=%s", result2.observations)
    assert_all_success(result2)

    # Core invariant: at least one of the reward tokens accrued
    ram_diff = result2.get("ram_after_claim") - result2.get("ram_before_claim")
    xram_diff = result2.get("xram_after_claim") - result2.get("xram_before_claim")
    log.info("ram_diff=%s xram_diff=%s", ram_diff, xram_diff)
    assert ram_diff > 0 or xram_diff > 0, (
        "claim_rewards moved no RAM and no XRAM into the rewards manager"
    )

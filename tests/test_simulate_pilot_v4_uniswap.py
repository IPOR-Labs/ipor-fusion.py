"""PILOT v4 (Arbitrum) — Uniswap V3 swap + LP positions via `eth_simulateV1`.

Mirrors `test_execute_pilot_v4_uniswap.py`. The vault has 1000 USDC at the pinned
block and the original test exercises five flows: single-hop swap, multi-hop
swap, open new LP position, decrease+collect+close, and increase liquidity.

Tests that read `token_id` from emitted events (decrease/collect, increase) need
a two-step `eth_simulateV1`: first run mints the position and exposes the
token_id via `result.execute_logs`; second run replays the setup (state doesn't
persist across runs) plus the follow-up actions using the captured token_id.
"""

from __future__ import annotations

import logging
import time

from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3

from _simulate import assert_all_success
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
from ipor_fusion import (
    Web3Context,
    PlasmaVault,
    AccessManager,
    ERC20,
    Roles,
    VaultSimulator,
)
from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.fuses import (
    UniswapV3SwapFuse,
    UniswapV3NewPositionFuse,
    UniswapV3ModifyPositionFuse,
    UniswapV3CollectFuse,
    UniversalTokenSwapperFuse,
    UniswapV3Events,
)
from ipor_fusion.types import ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PINNED_BLOCK = 254084008  # mirrors anvil.reset_fork(...)
UNISWAP_V3_UNIVERSAL_ROUTER = Web3.to_checksum_address(
    "0x5E325eDA8064b456f4781070C0738d849c824258"
)
DEADLINE_OFFSET = 1000  # seconds past block.timestamp


def _encode_call(signature: str, *args) -> bytes:
    selector = function_signature_to_4byte_selector(signature)
    types = _parse_param_types(signature)
    return selector + encode(types, list(args)) if types else selector


def _setup_alpha(sim, access_manager, owner):
    """Standard pilot v4 setup: owner grants ALPHA_ROLE to ANVIL_WALLET."""
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.ALPHA_ROLE, ANVIL_WALLET, 0
        ),
        from_=owner,
    )


def _build_universal_swap(token_in, token_out, amount_in, min_out, path):
    """Build calldata for UniversalTokenSwapperFuse via Uniswap V3 universal router."""
    targets = [token_in, UNISWAP_V3_UNIVERSAL_ROUTER]
    transfer_data = _encode_call(
        "transfer(address,uint256)", UNISWAP_V3_UNIVERSAL_ROUTER, amount_in
    )
    inputs = [
        encode(
            ["address", "uint256", "uint256", "bytes", "bool"],
            [
                "0x0000000000000000000000000000000000000001",
                amount_in,
                min_out,
                path,
                False,
            ],
        )
    ]
    execute_data = function_signature_to_4byte_selector(
        "execute(bytes,bytes[])"
    ) + encode(
        ["bytes", "bytes[]"],
        [encode_packed(["bytes1"], [bytes.fromhex("00")]), inputs],
    )
    universal = UniversalTokenSwapperFuse(ARBITRUM_UNIVERSAL_SWAP_FUSE)
    return universal.swap(
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        targets=targets,
        data=[transfer_data, execute_data],
    )


def test_simulate_swap_when_one_hop_uniswap_v3(web3_arb):
    """USDC→USDT single-hop swap via Uniswap V3 universal router."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V4_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()

    path = encode_packed(
        ["address", "uint24", "address"], [ARBITRUM_USDC, 100, ARBITRUM_USDT]
    )
    swap_action = _build_universal_swap(
        ARBITRUM_USDC, ARBITRUM_USDT, int(100e6), int(99e6), path
    )

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_alpha(sim, access_manager, owner)
    sim.observe("usdc_before", ARBITRUM_USDC, "balanceOf(address)", (vault_address,))
    sim.observe("usdt_before", ARBITRUM_USDT, "balanceOf(address)", (vault_address,))
    sim.execute([swap_action])
    sim.observe("usdc_after", ARBITRUM_USDC, "balanceOf(address)", (vault_address,))
    sim.observe("usdt_after", ARBITRUM_USDT, "balanceOf(address)", (vault_address,))

    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)
    assert result.get("usdc_after") - result.get("usdc_before") == -int(100e6)
    usdt_change = result.get("usdt_after") - result.get("usdt_before")
    assert int(98e6) < usdt_change < int(100e6)


def test_simulate_swap_when_multiple_hop(web3_arb):
    """USDC→WETH→USDT multi-hop swap."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V4_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()

    path = encode_packed(
        ["address", "uint24", "address", "uint24", "address"],
        [ARBITRUM_USDC, 500, ARBITRUM_WETH, 3000, ARBITRUM_USDT],
    )
    swap_action = _build_universal_swap(
        ARBITRUM_USDC, ARBITRUM_USDT, int(100e6), int(99e6), path
    )

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_alpha(sim, access_manager, owner)
    sim.observe("usdc_before", ARBITRUM_USDC, "balanceOf(address)", (vault_address,))
    sim.observe("usdt_before", ARBITRUM_USDT, "balanceOf(address)", (vault_address,))
    sim.execute([swap_action])
    sim.observe("usdc_after", ARBITRUM_USDC, "balanceOf(address)", (vault_address,))
    sim.observe("usdt_after", ARBITRUM_USDT, "balanceOf(address)", (vault_address,))

    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)
    assert result.get("usdc_after") - result.get("usdc_before") == -int(100e6)
    usdt_change = result.get("usdt_after") - result.get("usdt_before")
    assert int(98e6) < usdt_change < int(100e6)


def test_simulate_open_new_position_uniswap_v3(web3_arb):
    """Swap USDC→USDT then mint a Uniswap V3 LP position."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V4_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()

    deadline = int(web3_arb.eth.get_block(PINNED_BLOCK)["timestamp"]) + DEADLINE_OFFSET

    uniswap_swap = UniswapV3SwapFuse(ARBITRUM_UNISWAP_V3_SWAP_FUSE)
    uniswap_new_pos = UniswapV3NewPositionFuse(ARBITRUM_V4_UNISWAP_V3_NEW_POSITION_FUSE)

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
            uniswap_new_pos.new_position(
                token0=ARBITRUM_USDC,
                token1=ARBITRUM_USDT,
                fee=100,
                tick_lower=-100,
                tick_upper=101,
                amount0_desired=int(499e6),
                amount1_desired=int(499e6),
                amount0_min=0,
                amount1_min=0,
                deadline=deadline,
            )
        ]
    )
    sim.observe(
        "usdc_after_position",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (vault_address,),
    )
    sim.observe(
        "usdt_after_position",
        ARBITRUM_USDT,
        "balanceOf(address)",
        (vault_address,),
    )

    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)
    usdc_change = result.get("usdc_after_position") - result.get("usdc_after_swap")
    usdt_change = result.get("usdt_after_position") - result.get("usdt_after_swap")
    assert usdc_change == -int(499e6)
    # Original test asserts == -489_152502 exactly; on Uniswap V3 the consumed
    # amount1 depends on pool state at the block which is deterministic on archive.
    assert usdt_change == -489_152502


# ─── Two-step pattern: pre-extract token_id, then replay with follow-up ───


def _extract_new_position_token_id(execute_logs):
    """Decode UniswapV3NewPositionFuse's emitted event from execute_logs.

    Mirrors `UniswapV3Events.extract_new_position_events` but operates on raw
    eth_simulateV1 log dicts (not web3.py Receipt objects).
    """
    target_topic = HexBytes(
        Web3.keccak(
            text=(
                "UniswapV3NewPositionFuseEnter(address,uint256,uint128,uint256,"
                "uint256,address,address,uint24,int24,int24)"
            )
        )
    )
    for log_dict in execute_logs:
        topics = log_dict.get("topics") or []
        if topics and HexBytes(topics[0]) == target_topic:
            data = HexBytes(log_dict["data"])
            from eth_abi import decode as abi_decode

            decoded = abi_decode(
                [
                    "address",  # version
                    "uint256",  # token_id
                    "uint128",  # liquidity
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
            return decoded[1], decoded[2]  # token_id, liquidity
    raise AssertionError("UniswapV3NewPositionFuseEnter event not found in logs")


def _build_open_new_position_sim(web3_arb, ctx, vault_address, deadline):
    """Common builder: setup + swap + new_position. Used by both two-step tests."""
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()

    uniswap_swap = UniswapV3SwapFuse(ARBITRUM_UNISWAP_V3_SWAP_FUSE)
    uniswap_new_pos = UniswapV3NewPositionFuse(ARBITRUM_V4_UNISWAP_V3_NEW_POSITION_FUSE)

    block_hex = hex(PINNED_BLOCK)
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
    sim.execute(
        [
            uniswap_new_pos.new_position(
                token0=ARBITRUM_USDC,
                token1=ARBITRUM_USDT,
                fee=100,
                tick_lower=-100,
                tick_upper=101,
                amount0_desired=int(499e6),
                amount1_desired=int(499e6),
                amount0_min=0,
                amount1_min=0,
                deadline=deadline,
            )
        ]
    )
    return sim


def test_simulate_collect_all_after_decrease_liquidity(web3_arb):
    """Open position → decrease liquidity → collect → close. Uses 2-step sim:
    first call mints the position to extract token_id from events; second call
    replays setup + adds decrease/collect/close.
    """
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V4_PLASMA_VAULT
    deadline = int(web3_arb.eth.get_block(PINNED_BLOCK)["timestamp"]) + DEADLINE_OFFSET

    # Step 1: extract token_id from new_position event logs
    sim1 = _build_open_new_position_sim(web3_arb, ctx, vault_address, deadline)
    result1 = sim1.run()
    assert_all_success(result1)
    new_token_id, liquidity = _extract_new_position_token_id(result1.execute_logs)
    log.info("minted token_id=%s liquidity=%s", new_token_id, liquidity)

    # Step 2: replay full flow + decrease + collect + close
    uniswap_modify = UniswapV3ModifyPositionFuse(
        ARBITRUM_UNISWAP_V3_MODIFY_POSITION_FUSE
    )
    uniswap_collect = UniswapV3CollectFuse(ARBITRUM_UNISWAP_V3_COLLECT_FUSE)
    uniswap_new_pos = UniswapV3NewPositionFuse(ARBITRUM_V4_UNISWAP_V3_NEW_POSITION_FUSE)

    sim2 = _build_open_new_position_sim(web3_arb, ctx, vault_address, deadline)
    sim2.execute(
        [
            uniswap_modify.decrease_liquidity(
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
    sim2.execute([uniswap_collect.collect([new_token_id])])
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
    sim2.execute([uniswap_new_pos.close_position([new_token_id])])

    result2 = sim2.run()
    log.info("step2 observations=%s", result2.observations)
    assert_all_success(result2)
    usdc_change = result2.get("usdc_after_collect") - result2.get("usdc_before_collect")
    usdt_change = result2.get("usdt_after_collect") - result2.get("usdt_before_collect")
    assert 498_000000 < usdc_change < 500_000000
    assert 489_000000 < usdt_change < 500_000000


def test_simulate_increase_liquidity(web3_arb):
    """Open position → increase liquidity. Pre-extract token_id then replay."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V4_PLASMA_VAULT
    deadline = int(web3_arb.eth.get_block(PINNED_BLOCK)["timestamp"]) + DEADLINE_OFFSET

    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()

    uniswap_swap = UniswapV3SwapFuse(ARBITRUM_UNISWAP_V3_SWAP_FUSE)
    uniswap_new_pos = UniswapV3NewPositionFuse(ARBITRUM_V4_UNISWAP_V3_NEW_POSITION_FUSE)
    uniswap_modify = UniswapV3ModifyPositionFuse(
        ARBITRUM_UNISWAP_V3_MODIFY_POSITION_FUSE
    )

    def _build_setup_sim():
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
                )
            ]
        )
        sim.execute(
            [
                uniswap_new_pos.new_position(
                    token0=ARBITRUM_USDC,
                    token1=ARBITRUM_USDT,
                    fee=100,
                    tick_lower=-100,
                    tick_upper=101,
                    amount0_desired=int(400e6),
                    amount1_desired=int(400e6),
                    amount0_min=0,
                    amount1_min=0,
                    deadline=deadline,
                )
            ]
        )
        return sim

    # Step 1: extract token_id
    sim1 = _build_setup_sim()
    result1 = sim1.run()
    assert_all_success(result1)
    new_token_id, _ = _extract_new_position_token_id(result1.execute_logs)
    log.info("minted token_id=%s", new_token_id)

    # Step 2: replay + increase
    sim2 = _build_setup_sim()
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
            uniswap_modify.increase_liquidity(
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
    assert usdc_change == -int(99e6)
    assert usdt_change == -int(97_046288)

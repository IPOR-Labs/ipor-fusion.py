"""PILOT v3 (Arbitrum) — supply/withdraw flow on Aave V3 via `eth_simulateV1`.

Mirrors `test_execute_pilot_v3.py::test_supply_and_withdraw_from_aave_v3` with
**probe-driven assertions**. The original test's magic-number invariants
(`> 11_000e6 USDC`) reflected the pilot vault's *aggregate* position at the
pinned block, scattered across Fluid staking + raw USDC. We pre-fetch both via
eth_call, run the original cleanup-then-supply-then-withdraw flow inside one
eth_simulateV1 batch, and assert against probe-derived deterministic values.
"""

from __future__ import annotations

import logging

import pytest
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from _simulate import assert_all_success
from addresses import ARBITRUM_USDC
from constants import (
    ANVIL_WALLET,
    ARBITRUM_PILOT_V3_PLASMA_VAULT,
    ARBITRUM_AAVE_V3_SUPPLY_FUSE,
    ARBITRUM_V3_COMPOUND_V3_SUPPLY_FUSE,
    ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_3,
    ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_5,
    ARBITRUM_V3_FLUID_INSTADAPP_STAKING_FUSE,
    ARBITRUM_V3_GEARBOX_V3_FARM_FUSE,
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
    AaveV3SupplyFuse,
    CompoundV3SupplyFuse,
    FluidInstadappSupplyFuse,
    FluidInstadappStakingFuse,
    GearboxStakeFuse,
    GearboxSupplyFuse,
)
from ipor_fusion.types import ChainId, MAX_UINT256

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PINNED_BLOCK = 250690377  # mirrors anvil.reset_fork(...) in the original test

FLUID_POOL_TOKEN = Web3.to_checksum_address(
    "0x1A996cb54bb95462040408C06122D45D6Cdb6096"
)
FLUID_STAKING_CONTRACT = Web3.to_checksum_address(
    "0x48f89d731C5e3b5BeE8235162FC2C639Ba62DB7d"
)
AAVE_AUSDC_TOKEN = Web3.to_checksum_address(
    "0x724dc807b04555b71ed48a6896b6f41593b8c637"
)
COMPOUND_CUSDC_TOKEN = Web3.to_checksum_address(
    "0x9c4ec768c28520b50860ea7a15bd7213a9ff58bf"
)
GEARBOX_D_TOKEN = Web3.to_checksum_address("0x890A69EF363C9c7BdD5E36eb95Ceb569F63ACbF6")
GEARBOX_FARMD_TOKEN = Web3.to_checksum_address(
    "0xD0181a36B0566a8645B7eECFf2148adE7Ecf2BE9"
)


def _encode_call(signature: str, *args) -> bytes:
    selector = function_signature_to_4byte_selector(signature)
    types = _parse_param_types(signature)
    return selector + encode(types, list(args)) if types else selector


def test_simulate_supply_and_withdraw_from_aave_v3(web3_arb):
    """Cleanup Fluid stake, supply on Aave V3, withdraw — all in one batch."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V3_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()

    # Probe vault state at the pinned block — deterministic on archive.
    raw_usdc = ERC20(ctx, ARBITRUM_USDC).balance_of(vault_address)
    fluid_stake = ERC20(ctx, FLUID_STAKING_CONTRACT).balance_of(vault_address)
    log.info(
        "owner=%s raw_usdc=%s fluid_stake=%s (block=%s)",
        owner,
        raw_usdc / 1e6,
        fluid_stake / 1e6,
        PINNED_BLOCK,
    )
    if fluid_stake == 0 and raw_usdc < 11_000_000_000:
        # Either the vault has no Fluid position OR raw USDC is too small —
        # the original test's `> 11_000e6` invariant doesn't hold here.
        # Skip rather than mirror a stale assertion.
        pytest.skip(
            f"vault has no Fluid stake ({fluid_stake}) and raw USDC < 11k "
            f"({raw_usdc}) — pre-conditions for original test's invariant don't hold"
        )

    fluid_supply = FluidInstadappSupplyFuse(ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_5)
    fluid_staking_fuse = FluidInstadappStakingFuse(
        fuse_address=ARBITRUM_V3_FLUID_INSTADAPP_STAKING_FUSE,
        staking_address=FLUID_STAKING_CONTRACT,
    )
    aave = AaveV3SupplyFuse(ARBITRUM_AAVE_V3_SUPPLY_FUSE)

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )

    # ── Setup: owner grants ALPHA to ANVIL_WALLET ────────────────────────
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.ALPHA_ROLE, ANVIL_WALLET, 0
        ),
        from_=owner,
    )

    sim.observe(
        "raw_usdc_initial", ARBITRUM_USDC, "balanceOf(address)", (vault_address,)
    )
    sim.observe(
        "fluid_stake_initial",
        FLUID_STAKING_CONTRACT,
        "balanceOf(address)",
        (vault_address,),
    )

    # ── Cleanup: unstake fluid + withdraw all from fluid pool ────────────
    # Mirrors withdraw_from_fluid() helper. Using MAX_UINT256 for the
    # ERC4626 withdraw amount is the SDK idiom for "all available".
    sim.execute(
        [
            fluid_staking_fuse.unstake(fluid_stake),
            fluid_supply.withdraw(vault_address=FLUID_POOL_TOKEN, amount=MAX_UINT256),
        ]
    )

    sim.observe(
        "raw_usdc_post_cleanup",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (vault_address,),
    )
    sim.observe(
        "fluid_stake_post_cleanup",
        FLUID_STAKING_CONTRACT,
        "balanceOf(address)",
        (vault_address,),
    )

    # ── Strategy: Aave V3 supply USDC, then withdraw ─────────────────────
    # Pre-compute supply amount with safety buffer (1 USDC) below the post-cleanup
    # balance — fluid withdraw may leave dust.
    expected_post_cleanup = raw_usdc + fluid_stake
    supply_amount = expected_post_cleanup - 1_000_000  # 1 USDC buffer

    sim.execute([aave.supply(asset=ARBITRUM_USDC, amount=supply_amount, e_mode=300)])
    sim.observe(
        "ausdc_post_supply",
        AAVE_AUSDC_TOKEN,
        "balanceOf(address)",
        (vault_address,),
    )
    sim.observe(
        "raw_usdc_post_supply",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (vault_address,),
    )

    # Withdraw same amount back from Aave
    sim.execute([aave.withdraw(asset=ARBITRUM_USDC, amount=supply_amount)])
    sim.observe(
        "ausdc_post_withdraw",
        AAVE_AUSDC_TOKEN,
        "balanceOf(address)",
        (vault_address,),
    )
    sim.observe(
        "raw_usdc_post_withdraw",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (vault_address,),
    )

    result = sim.run()

    log.info(
        "all_success=%s gas_used=%s reason=%s",
        result.all_success,
        result.gas_used,
        result.revert_reason,
    )
    log.info("observations=%s", result.observations)

    assert_all_success(result)

    # Probe-derived invariants:
    assert result.get("raw_usdc_initial") == raw_usdc
    assert result.get("fluid_stake_initial") == fluid_stake
    # Cleanup releases at least the staked amount minus any rounding dust
    assert result.get("raw_usdc_post_cleanup") >= raw_usdc + fluid_stake - 100_000
    assert result.get("fluid_stake_post_cleanup") == 0
    # Aave supply moves USDC to aUSDC (1:1 in e-mode 300)
    assert result.get("ausdc_post_supply") >= supply_amount
    # Withdraw moves it back, leaving small accrued interest in aUSDC
    assert result.get("raw_usdc_post_withdraw") >= supply_amount
    assert result.get("ausdc_post_withdraw") < supply_amount  # < not 0 due to interest


def test_simulate_supply_and_withdraw_from_compound_v3(web3_arb):
    """Cleanup Fluid stake, supply on Compound V3, withdraw — single batch."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V3_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()

    raw_usdc = ERC20(ctx, ARBITRUM_USDC).balance_of(vault_address)
    fluid_stake = ERC20(ctx, FLUID_STAKING_CONTRACT).balance_of(vault_address)
    log.info("raw_usdc=%s fluid_stake=%s", raw_usdc / 1e6, fluid_stake / 1e6)
    if fluid_stake == 0 and raw_usdc < 11_000_000_000:
        pytest.skip("vault preconditions not met for Compound V3 supply test")

    fluid_supply = FluidInstadappSupplyFuse(ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_5)
    fluid_staking_fuse = FluidInstadappStakingFuse(
        fuse_address=ARBITRUM_V3_FLUID_INSTADAPP_STAKING_FUSE,
        staking_address=FLUID_STAKING_CONTRACT,
    )
    compound = CompoundV3SupplyFuse(ARBITRUM_V3_COMPOUND_V3_SUPPLY_FUSE)

    expected_post_cleanup = raw_usdc + fluid_stake
    supply_amount = expected_post_cleanup - 1_000_000

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.ALPHA_ROLE, ANVIL_WALLET, 0
        ),
        from_=owner,
    )
    sim.execute(
        [
            fluid_staking_fuse.unstake(fluid_stake),
            fluid_supply.withdraw(vault_address=FLUID_POOL_TOKEN, amount=MAX_UINT256),
        ]
    )
    sim.execute([compound.supply(asset=ARBITRUM_USDC, amount=supply_amount)])
    sim.observe(
        "cusdc_post_supply",
        COMPOUND_CUSDC_TOKEN,
        "balanceOf(address)",
        (vault_address,),
    )
    sim.execute([compound.withdraw(asset=ARBITRUM_USDC, amount=supply_amount)])
    sim.observe(
        "cusdc_post_withdraw",
        COMPOUND_CUSDC_TOKEN,
        "balanceOf(address)",
        (vault_address,),
    )
    sim.observe(
        "raw_usdc_post_withdraw",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (vault_address,),
    )
    result = sim.run()

    log.info(
        "all_success=%s gas_used=%s observations=%s",
        result.all_success,
        result.gas_used,
        result.observations,
    )
    assert_all_success(result)
    assert (
        result.get("cusdc_post_supply") >= supply_amount * 99 // 100
    )  # ~ supply_amount
    assert result.get("cusdc_post_withdraw") < supply_amount  # < interest only
    assert result.get("raw_usdc_post_withdraw") >= supply_amount


def test_simulate_supply_and_withdraw_from_fluid(web3_arb):
    """Cleanup Fluid stake, then re-supply + re-stake into Fluid — round trip."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V3_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()

    raw_usdc = ERC20(ctx, ARBITRUM_USDC).balance_of(vault_address)
    fluid_stake = ERC20(ctx, FLUID_STAKING_CONTRACT).balance_of(vault_address)
    if fluid_stake == 0 and raw_usdc < 11_000_000_000:
        pytest.skip("vault preconditions not met for Fluid re-supply test")

    fluid_supply = FluidInstadappSupplyFuse(ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_5)
    fluid_staking_fuse = FluidInstadappStakingFuse(
        fuse_address=ARBITRUM_V3_FLUID_INSTADAPP_STAKING_FUSE,
        staking_address=FLUID_STAKING_CONTRACT,
    )
    expected_post_cleanup = raw_usdc + fluid_stake
    re_supply_amount = expected_post_cleanup - 1_000_000

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.ALPHA_ROLE, ANVIL_WALLET, 0
        ),
        from_=owner,
    )
    # Cleanup
    sim.execute(
        [
            fluid_staking_fuse.unstake(fluid_stake),
            fluid_supply.withdraw(vault_address=FLUID_POOL_TOKEN, amount=MAX_UINT256),
        ]
    )
    # Re-supply + re-stake
    sim.execute(
        [
            fluid_supply.supply(
                vault_address=FLUID_POOL_TOKEN, amount=re_supply_amount
            ),
            fluid_staking_fuse.stake(),
        ]
    )
    sim.observe(
        "fluid_stake_after",
        FLUID_STAKING_CONTRACT,
        "balanceOf(address)",
        (vault_address,),
    )
    sim.observe(
        "raw_usdc_after",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (vault_address,),
    )
    result = sim.run()

    log.info("all_success=%s observations=%s", result.all_success, result.observations)
    assert_all_success(result)
    # Vault re-staked roughly the same fluid amount it started with
    assert result.get("fluid_stake_after") >= re_supply_amount * 99 // 100
    assert result.get("raw_usdc_after") < 1_000_000_000  # almost everything re-staked


def test_simulate_supply_and_withdraw_from_gearbox(web3_arb):
    """Cleanup Fluid stake, grant FARMD substrate, supply+stake on Gearbox, then unstake+withdraw.

    Original test calls anvil.grant_market_substrates(..., 4, [farmd_substrate]).
    Same effect via a `grantMarketSubstrates(uint256,bytes32[])` impersonated call.
    """
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_V3_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()

    raw_usdc = ERC20(ctx, ARBITRUM_USDC).balance_of(vault_address)
    fluid_stake = ERC20(ctx, FLUID_STAKING_CONTRACT).balance_of(vault_address)
    if fluid_stake == 0 and raw_usdc < 11_000_000_000:
        pytest.skip("vault preconditions not met for Gearbox test")

    fluid_supply = FluidInstadappSupplyFuse(ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_5)
    fluid_staking_fuse = FluidInstadappStakingFuse(
        fuse_address=ARBITRUM_V3_FLUID_INSTADAPP_STAKING_FUSE,
        staking_address=FLUID_STAKING_CONTRACT,
    )
    gearbox_supply = GearboxSupplyFuse(ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_3)
    gearbox_stake = GearboxStakeFuse(
        fuse_address=ARBITRUM_V3_GEARBOX_V3_FARM_FUSE,
        staking_address=GEARBOX_FARMD_TOKEN,
    )

    expected_post_cleanup = raw_usdc + fluid_stake
    supply_amount = expected_post_cleanup - 1_000_000

    farmd_substrate = bytes.fromhex(str(GEARBOX_FARMD_TOKEN)[2:].lower().rjust(64, "0"))

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.ALPHA_ROLE, ANVIL_WALLET, 0
        ),
        from_=owner,
    )
    # Grant FARMD substrate for Gearbox market_id=4
    sim.add_call(
        to=vault_address,
        data=_encode_call(
            "grantMarketSubstrates(uint256,bytes32[])", 4, [farmd_substrate]
        ),
        from_=owner,
    )
    # Cleanup
    sim.execute(
        [
            fluid_staking_fuse.unstake(fluid_stake),
            fluid_supply.withdraw(vault_address=FLUID_POOL_TOKEN, amount=MAX_UINT256),
        ]
    )
    # Gearbox supply + stake
    sim.execute(
        [
            gearbox_supply.supply(vault_address=GEARBOX_D_TOKEN, amount=supply_amount),
            gearbox_stake.stake(),
        ]
    )
    sim.observe(
        "farmd_post_stake",
        GEARBOX_FARMD_TOKEN,
        "balanceOf(address)",
        (vault_address,),
    )
    # Unstake + withdraw — read farmd amount via observation, but use the
    # known supply_amount as a deterministic input for unstake (vault's farmd
    # mints 1:1 with the dToken).
    sim.execute(
        [
            gearbox_stake.unstake(supply_amount),
            gearbox_supply.withdraw(vault_address=GEARBOX_D_TOKEN, amount=MAX_UINT256),
        ]
    )
    sim.observe(
        "farmd_post_withdraw",
        GEARBOX_FARMD_TOKEN,
        "balanceOf(address)",
        (vault_address,),
    )
    sim.observe(
        "raw_usdc_post_withdraw",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (vault_address,),
    )
    result = sim.run()

    log.info("all_success=%s observations=%s", result.all_success, result.observations)
    assert_all_success(result)
    assert result.get("farmd_post_stake") >= supply_amount * 99 // 100
    assert result.get("farmd_post_withdraw") == 0
    assert result.get("raw_usdc_post_withdraw") >= supply_amount * 99 // 100

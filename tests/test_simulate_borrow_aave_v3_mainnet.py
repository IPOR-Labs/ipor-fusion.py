"""Aave V3 supply/borrow/repay/withdraw on Ethereum mainnet via `eth_simulateV1`.

Mirrors `test_borrow_aave_v3_mainnet.py::test_should_borrow_aave_v3`. The whole
flow (role grant + market substrates + 4 fuse executes + 5 reads) goes in one
RPC payload. State threads through every step inside a single simulated block.
"""

from __future__ import annotations

import logging

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from _simulate import assert_all_success
from addresses import ETHEREUM_WBTC, ETHEREUM_WETH
from constants import (
    ANVIL_WALLET,
    ETHEREUM_AAVE_V3_SUPPLY_FUSE,
    ETHEREUM_AAVE_V3_BORROW_FUSE,
)
from ipor_fusion import (
    Web3Context,
    PlasmaVault,
    AccessManager,
    ERC20,
    Roles,
    IporFusionMarkets,
    VaultSimulator,
)
from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.fuses import AaveV3SupplyFuse, AaveV3BorrowFuse, ERC4626SupplyFuse
from ipor_fusion.types import ChainId, Period

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

VAULT_ADDRESS = Web3.to_checksum_address("0x1fdf5dc3F915Cb40E0AD5690DE51E3cB464d1BAD")
ATOMIST = Web3.to_checksum_address("0x46B48240f61C831B85fCf4c198C98028Ab8EE68d")
WBTC_HOLDER = Web3.to_checksum_address("0xE940ae8cF59fE2709BBc572CBAD2633fB45Abf46")
PINNED_BLOCK = 22616438  # mirrors anvil.reset_fork(...) in test_borrow_aave_v3_mainnet.py


def _encode_call(signature: str, *args) -> bytes:
    selector = function_signature_to_4byte_selector(signature)
    types = _parse_param_types(signature)
    return selector + encode(types, list(args)) if types else selector


def _address_substrate(addr: str) -> bytes:
    """Pad an EVM address into a 32-byte substrate value."""
    return bytes.fromhex(addr.removeprefix("0x").lower().rjust(64, "0"))


def test_simulate_borrow_aave_v3(web3_eth):
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_eth, chain_id=ChainId(web3_eth.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    plasma_vault = PlasmaVault(ctx, VAULT_ADDRESS)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())

    # Original test's exact amounts on the pinned block — wbtc_holder has WBTC
    # there, so we deposit instead of needing stateOverrides.stateDiff.
    wbtc_collateral_amount = 10**8  # 1 WBTC
    weth_borrow_amount = 20 * 10**18  # 20 WETH (mirrors original)
    withdraw_amount = (wbtc_collateral_amount * 99999) // 100000  # 99.999%

    aave_supply = AaveV3SupplyFuse(ETHEREUM_AAVE_V3_SUPPLY_FUSE)
    aave_borrow = AaveV3BorrowFuse(ETHEREUM_AAVE_V3_BORROW_FUSE)

    supply_action = aave_supply.supply(
        asset=ETHEREUM_WBTC, amount=wbtc_collateral_amount, e_mode=1
    )
    borrow_action = aave_borrow.borrow(asset=ETHEREUM_WETH, amount=weth_borrow_amount)
    repay_action = aave_borrow.repay(asset=ETHEREUM_WETH, amount=weth_borrow_amount)
    withdraw_action = aave_supply.withdraw(asset=ETHEREUM_WBTC, amount=withdraw_amount)

    log.info(
        "collateral=%.4f weth_borrow=%.4f withdraw=%.4f",
        wbtc_collateral_amount / 1e8,
        weth_borrow_amount / 1e18,
        withdraw_amount / 1e8,
    )

    sim = VaultSimulator(
        web3=web3_eth, vault=VAULT_ADDRESS, alpha=ANVIL_WALLET, block=block_hex
    )

    # Setup: atomist grants ALPHA + WHITELIST + required market substrates.
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.ALPHA_ROLE, ANVIL_WALLET, 0
        ),
        from_=ATOMIST,
    )
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)",
            Roles.WHITELIST_ROLE,
            WBTC_HOLDER,
            0,
        ),
        from_=ATOMIST,
    )
    sim.add_call(
        to=VAULT_ADDRESS,
        data=_encode_call(
            "grantMarketSubstrates(uint256,bytes32[])",
            IporFusionMarkets.AAVE_V3,
            [
                _address_substrate(ETHEREUM_WBTC),
                _address_substrate(ETHEREUM_WETH),
            ],
        ),
        from_=ATOMIST,
    )
    # wbtc_holder approves and deposits 1 WBTC into the vault
    sim.add_call(
        to=ETHEREUM_WBTC,
        data=_encode_call(
            "approve(address,uint256)", VAULT_ADDRESS, wbtc_collateral_amount
        ),
        from_=WBTC_HOLDER,
    )
    sim.add_call(
        to=VAULT_ADDRESS,
        data=_encode_call(
            "deposit(uint256,address)", wbtc_collateral_amount, WBTC_HOLDER
        ),
        from_=WBTC_HOLDER,
    )

    sim.observe("wbtc_initial", ETHEREUM_WBTC, "balanceOf(address)", (VAULT_ADDRESS,))

    # 1. Supply WBTC as collateral
    sim.execute([supply_action])
    sim.observe(
        "wbtc_after_supply", ETHEREUM_WBTC, "balanceOf(address)", (VAULT_ADDRESS,)
    )

    # 2. Borrow WETH against the collateral
    sim.execute([borrow_action])
    sim.observe(
        "weth_after_borrow", ETHEREUM_WETH, "balanceOf(address)", (VAULT_ADDRESS,)
    )

    # 3. Repay the WETH loan
    sim.execute([repay_action])
    sim.observe(
        "weth_after_repay", ETHEREUM_WETH, "balanceOf(address)", (VAULT_ADDRESS,)
    )

    # 4. Withdraw most of the WBTC collateral
    sim.execute([withdraw_action])
    sim.observe(
        "wbtc_after_withdraw",
        ETHEREUM_WBTC,
        "balanceOf(address)",
        (VAULT_ADDRESS,),
    )

    result = sim.run()

    log.info("success=%s gas_used=%s", result.all_success, result.gas_used)
    log.info("revert_reason=%s", result.revert_reason)
    log.info("observations=%s", result.observations)

    assert_all_success(result)

    # Invariants of the supply→borrow→repay→withdraw cycle:
    assert result.get("wbtc_initial") == wbtc_collateral_amount
    assert result.get("wbtc_after_supply") == 0
    assert result.get("weth_after_borrow") == weth_borrow_amount
    assert result.get("weth_after_repay") == 0
    assert result.get("wbtc_after_withdraw") == withdraw_amount


# ─────────────────────────────────────────────────────────────────────────
# Variant 2: deposit → Aave supply → borrow → ERC4626 supply → ERC4626 withdraw
# Multi-block (move_time +60s before ERC4626 withdraw)
# ─────────────────────────────────────────────────────────────────────────

ERC4626_FUSE_ADDRESS = Web3.to_checksum_address(
    "0x970b4f5522685D4826eceb0377B3DdBF12836dFd"
)
WETH_VAULT_ADDRESS = Web3.to_checksum_address(
    "0x9824dCdac89F208Bf8b5Cb5C4Dc41F04a0878607"
)
WETH_VAULT_ATOMIST = Web3.to_checksum_address(
    "0xf2C6a2225BE9829eD77263b032E3D92C52aE6694"
)
PINNED_BLOCK_DEPOSIT = 22687555  # mirrors anvil.reset_fork(...) in the original test


def test_simulate_deposit_to_plasma_vault(web3_eth):
    """Cross-vault flow: WBTC vault supplies borrowed WETH into a separate WETH vault.

    The original test wires up two vaults: a main vault (WBTC) borrows WETH on
    Aave, then deposits that WETH into a second ERC-4626 vault via `ERC4626SupplyFuse`.
    The withdraw step requires `move_time(+60s)` to satisfy the WETH vault's
    deposit/withdraw delay — modelled here as `next_block(time_shift_seconds=60)`.
    """
    block_hex = hex(PINNED_BLOCK_DEPOSIT)
    ctx = Web3Context(web3=web3_eth, chain_id=ChainId(web3_eth.eth.chain_id))
    ctx.default_block = PINNED_BLOCK_DEPOSIT

    plasma_vault = PlasmaVault(ctx, VAULT_ADDRESS)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    weth_vault = PlasmaVault(ctx, WETH_VAULT_ADDRESS)
    weth_access = AccessManager(ctx, weth_vault.get_access_manager_address())

    # Pre-fetched read: original test caps the WETH vault's supply at cap/4 so
    # this deposit doesn't blow past the existing limit.
    cap = weth_vault.get_total_supply_cap()
    new_cap = cap // 4
    log.info("weth_vault total_supply_cap=%s -> new=%s", cap, new_cap)

    wbtc_collateral_amount = 10**8  # 1 WBTC
    weth_borrow_amount = 20 * 10**18  # 20 WETH

    aave_supply = AaveV3SupplyFuse(ETHEREUM_AAVE_V3_SUPPLY_FUSE)
    aave_borrow = AaveV3BorrowFuse(ETHEREUM_AAVE_V3_BORROW_FUSE)
    erc4626 = ERC4626SupplyFuse(ERC4626_FUSE_ADDRESS)

    sim = VaultSimulator(
        web3=web3_eth, vault=VAULT_ADDRESS, alpha=ANVIL_WALLET, block=block_hex
    )

    # ── Block 0: full setup + deposit + 3 executes (Aave supply, borrow, 4626 supply)
    # main vault: atomist grants ALPHA + WHITELIST + Aave/4626 substrates
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.ALPHA_ROLE, ANVIL_WALLET, 0
        ),
        from_=ATOMIST,
    )
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)",
            Roles.WHITELIST_ROLE,
            WBTC_HOLDER,
            0,
        ),
        from_=ATOMIST,
    )
    sim.add_call(
        to=VAULT_ADDRESS,
        data=_encode_call(
            "grantMarketSubstrates(uint256,bytes32[])",
            IporFusionMarkets.AAVE_V3,
            [
                _address_substrate(ETHEREUM_WBTC),
                _address_substrate(ETHEREUM_WETH),
            ],
        ),
        from_=ATOMIST,
    )
    sim.add_call(
        to=VAULT_ADDRESS,
        data=_encode_call(
            "grantMarketSubstrates(uint256,bytes32[])",
            IporFusionMarkets.ERC4626_0013,
            [_address_substrate(WETH_VAULT_ADDRESS)],
        ),
        from_=ATOMIST,
    )

    # WETH vault: its atomist whitelists the main vault and lifts supply cap
    sim.add_call(
        to=weth_access.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)",
            Roles.WHITELIST_ROLE,
            VAULT_ADDRESS,
            0,
        ),
        from_=WETH_VAULT_ATOMIST,
    )
    sim.add_call(
        to=WETH_VAULT_ADDRESS,
        data=_encode_call("setTotalSupplyCap(uint256)", new_cap),
        from_=WETH_VAULT_ATOMIST,
    )

    # wbtc_holder approves and deposits 1 WBTC
    sim.add_call(
        to=ETHEREUM_WBTC,
        data=_encode_call(
            "approve(address,uint256)", VAULT_ADDRESS, wbtc_collateral_amount
        ),
        from_=WBTC_HOLDER,
    )
    sim.add_call(
        to=VAULT_ADDRESS,
        data=_encode_call(
            "deposit(uint256,address)", wbtc_collateral_amount, WBTC_HOLDER
        ),
        from_=WBTC_HOLDER,
    )

    sim.observe(
        "wbtc_post_deposit",
        ETHEREUM_WBTC,
        "balanceOf(address)",
        (VAULT_ADDRESS,),
    )

    # alpha executes Aave supply, borrow, ERC4626 supply
    sim.execute(
        [aave_supply.supply(asset=ETHEREUM_WBTC, amount=wbtc_collateral_amount, e_mode=1)]
    )
    sim.observe(
        "wbtc_post_aave_supply",
        ETHEREUM_WBTC,
        "balanceOf(address)",
        (VAULT_ADDRESS,),
    )

    sim.execute([aave_borrow.borrow(asset=ETHEREUM_WETH, amount=weth_borrow_amount)])
    sim.observe(
        "weth_post_borrow",
        ETHEREUM_WETH,
        "balanceOf(address)",
        (VAULT_ADDRESS,),
    )

    sim.execute(
        [erc4626.supply(vault_address=WETH_VAULT_ADDRESS, amount=weth_borrow_amount)]
    )
    sim.observe(
        "weth_post_erc4626_supply",
        ETHEREUM_WETH,
        "balanceOf(address)",
        (VAULT_ADDRESS,),
    )

    # ── Block 1: +60 seconds (mirrors anvil.move_time(60) before withdraw)
    sim.next_block(time_shift_seconds=Period.MINUTE)

    sim.execute(
        [erc4626.withdraw(vault_address=WETH_VAULT_ADDRESS, amount=weth_borrow_amount)]
    )
    sim.observe(
        "weth_post_erc4626_withdraw",
        ETHEREUM_WETH,
        "balanceOf(address)",
        (VAULT_ADDRESS,),
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

    # Mirror original test's invariants exactly
    assert result.get("wbtc_post_deposit") == wbtc_collateral_amount  # 1e8
    assert result.get("wbtc_post_aave_supply") == 0
    assert result.get("weth_post_borrow") == weth_borrow_amount  # 20e18
    assert result.get("weth_post_erc4626_supply") == 0
    assert result.get("weth_post_erc4626_withdraw") > 0

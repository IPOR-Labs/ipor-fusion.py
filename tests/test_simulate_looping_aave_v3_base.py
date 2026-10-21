"""Aave V3 leveraged looping (10x) on Base — driven by `eth_simulateV1`.

Mirrors `test_looping_aave_v3_base.py::test_supply_borrow_in_flash_loan`. Single
flash-loan-wrapped batch with Aave V3 supply + borrow + Aerodrome swap goes
through one eth_simulateV1 payload pinned to the original test's fork block.
"""

from __future__ import annotations

import logging

from _simulate import assert_all_success
from addresses import (
    BASE_AAVE_V3_A_WSTETH,
    BASE_AAVE_V3_VARIABLE_DEBT_WETH,
    BASE_WETH,
    BASE_WSTETH,
)
from constants import (
    ANVIL_WALLET,
    BASE_AAVE_V3_BORROW_FUSE,
    BASE_AAVE_V3_SUPPLY_FUSE,
    BASE_MORPHO_FLASH_LOAN_FUSE,
    BASE_UNIVERSAL_SWAP_FUSE,
)
from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    PlasmaVault,
    PriceOracleMiddleware,
    Roles,
    VaultSimulator,
    Web3Context,
)
from ipor_fusion.fuses import (
    AaveV3BorrowFuse,
    AaveV3SupplyFuse,
    MorphoFlashLoanFuse,
    UniversalTokenSwapperFuse,
)
from ipor_fusion.types import Amount, ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

AERODROME_ROUTER_ADDRESS = Web3.to_checksum_address(
    "0xBE6D8F0D05cC4bE24d5167a3eF062215bE6D18a5"
)
AERODROME_EXECUTOR = Web3.to_checksum_address(
    "0x591435c065fCE9713c8B112FCBf5af98b8975cb3"
)

ATOMIST = Web3.to_checksum_address("0xF6a9bd8F6DC537675D499Ac1CA14f2c55d8b5569")
VAULT_ADDRESS = Web3.to_checksum_address("0xc4c00d8b323f37527eeda27c87412378be9f68ec")
WSTETH_HOLDER = Web3.to_checksum_address("0xf0bb20865277aBd641a307eCe5Ee04E79073416C")

INITIAL_DEPOSIT = int(1e18)
LEVERAGE = 10
PINNED_BLOCK = 30431901  # mirrors anvil.reset_fork(...) in the original test


def _aerodrome_path(token_in: ChecksumAddress, token_out: ChecksumAddress) -> bytes:
    if (token_in == BASE_WETH and token_out == BASE_WSTETH) or (
        token_in == BASE_WSTETH and token_out == BASE_WETH
    ):
        return encode_packed(["address", "uint24", "address"], [token_in, 1, token_out])
    raise ValueError(f"Unsupported path: {token_in} -> {token_out}")


def _aerodrome_swap(
    ctx, universal, token_in, token_out, amount_in, min_amount_out, deadline
):
    targets = [token_in, AERODROME_ROUTER_ADDRESS]
    approve_data = (
        ERC20(token_in).approve(AERODROME_ROUTER_ADDRESS, Amount(amount_in)).data
    )
    path = _aerodrome_path(token_in, token_out)
    swap_data = function_signature_to_4byte_selector(
        "exactInput((bytes,address,uint256,uint256,uint256))"
    ) + encode(
        ["(bytes,address,uint256,uint256,uint256)"],
        [[path, AERODROME_EXECUTOR, deadline, amount_in, min_amount_out]],
    )
    return universal.swap(
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        targets=targets,
        data=[approve_data, swap_data],
    )


def test_simulate_supply_borrow_in_flash_loan(web3_base):
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(
        web3=web3_base, chain_id=ChainId(web3_base.eth.chain_id)
    ).at_block(PINNED_BLOCK)

    plasma_vault = PlasmaVault(VAULT_ADDRESS)
    access_manager = AccessManager(plasma_vault.get_access_manager_address().call(ctx))
    oracle = PriceOracleMiddleware(
        plasma_vault.get_price_oracle_middleware_address().call(ctx)
    )
    wsteth = ERC20(BASE_WSTETH)
    weth = ERC20(BASE_WETH)
    awsteth = ERC20(BASE_AAVE_V3_A_WSTETH)
    dweth = ERC20(BASE_AAVE_V3_VARIABLE_DEBT_WETH)

    pre_balance_int = wsteth.balance_of(VAULT_ADDRESS).call(ctx)
    wsteth_price = oracle.get_asset_price(BASE_WSTETH).call(ctx).readable()
    weth_price = oracle.get_asset_price(BASE_WETH).call(ctx).readable()
    deadline = int(web3_base.eth.get_block(PINNED_BLOCK)["timestamp"]) + 1000

    # Strategy maths — vault holdings + 1 WStETH deposit, 10x leverage, 90% LTV
    post_balance = pre_balance_int + INITIAL_DEPOSIT
    wsteth_collateral_amount = post_balance * LEVERAGE
    flash_loan_amount = wsteth_collateral_amount - post_balance
    ltv = 1 - 1 / LEVERAGE
    weth_borrow_amount = int(wsteth_collateral_amount * ltv * wsteth_price / weth_price)

    log.info(
        "pre=%.4f deposit=%.4f collateral=%.4f flash=%.4f borrow=%.4f",
        pre_balance_int / 1e18,
        INITIAL_DEPOSIT / 1e18,
        wsteth_collateral_amount / 1e18,
        flash_loan_amount / 1e18,
        weth_borrow_amount / 1e18,
    )

    # Build the leveraged loop as a single FuseAction tree.
    aave_supply = AaveV3SupplyFuse(BASE_AAVE_V3_SUPPLY_FUSE)
    aave_borrow = AaveV3BorrowFuse(BASE_AAVE_V3_BORROW_FUSE)
    morpho_flash = MorphoFlashLoanFuse(BASE_MORPHO_FLASH_LOAN_FUSE)
    universal = UniversalTokenSwapperFuse(BASE_UNIVERSAL_SWAP_FUSE)

    supply_action = aave_supply.supply(
        asset=BASE_WSTETH, amount=wsteth_collateral_amount, e_mode=1
    )
    borrow_action = aave_borrow.borrow(asset=BASE_WETH, amount=weth_borrow_amount)
    swap_action = _aerodrome_swap(
        ctx=ctx,
        universal=universal,
        token_in=BASE_WETH,
        token_out=BASE_WSTETH,
        amount_in=weth_borrow_amount,
        min_amount_out=0,
        deadline=deadline,
    )
    flash_loan = morpho_flash.flash_loan(
        asset=BASE_WSTETH,
        amount=flash_loan_amount,
        actions=[supply_action, borrow_action, swap_action],
    )

    # One eth_simulateV1 payload — setup + leveraged loop + observations.
    sim = VaultSimulator(
        web3=web3_base, vault=VAULT_ADDRESS, alpha=ANVIL_WALLET, block=block_hex
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0),
        from_=ATOMIST,
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.WHITELIST_ROLE, WSTETH_HOLDER, 0),
        from_=ATOMIST,
    )
    sim.add_call(
        call=wsteth.approve(VAULT_ADDRESS, INITIAL_DEPOSIT),
        from_=WSTETH_HOLDER,
    )
    sim.add_call(
        call=plasma_vault.deposit(INITIAL_DEPOSIT, WSTETH_HOLDER),
        from_=WSTETH_HOLDER,
    )

    wsteth_pre_loop = sim.observe(wsteth.balance_of(VAULT_ADDRESS))

    sim.execute([flash_loan])

    sim.observe(wsteth.balance_of(VAULT_ADDRESS))
    weth_post_loop = sim.observe(weth.balance_of(VAULT_ADDRESS))
    awsteth_post_loop = sim.observe(awsteth.balance_of(VAULT_ADDRESS))
    dweth_post_loop = sim.observe(dweth.balance_of(VAULT_ADDRESS))

    result = sim.run()

    log.info("success=%s gas_used=%s", result.all_success, result.gas_used)
    log.info("revert_reason=%s", result.revert_reason)

    assert_all_success(result)

    # Invariants of a successful 10x Aave-V3 leverage loop:
    # - pre-loop wsteth = vault_pre + deposit (deterministic at the pinned block)
    # - WETH dust ~0 (full borrow swapped back to repay flash loan)
    # - aWStETH (collateral receipt) ~ wsteth_collateral_amount, dWETH (debt) ~ weth_borrow_amount
    assert wsteth_pre_loop.value == post_balance
    assert weth_post_loop.value < int(0.01e18)
    awsteth_after = awsteth_post_loop.value
    dweth_after = dweth_post_loop.value
    # Aave aTokens accrue ratio-style; values match collateral/borrow amounts within
    # rebasing precision (~1 wei per unit).
    assert abs(awsteth_after - wsteth_collateral_amount) <= 10
    assert abs(dweth_after - weth_borrow_amount) <= 10

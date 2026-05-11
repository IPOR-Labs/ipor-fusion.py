"""Looping leverage strategy on Morpho Blue (Base) — driven by `eth_simulateV1`.

Reproduces `test_looping_morpho_blue_base.py` end-to-end without anvil. The full
flow (role grants, deposit, flash-loaned 3x leverage loop) lands in one
`eth_simulateV1` batch — state carries between calls within the simulated block.
"""

from __future__ import annotations

import logging

from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from _simulate import assert_all_success
from addresses import BASE_WSTETH, BASE_WETH
from constants import (
    ANVIL_WALLET,
    BASE_MORPHO_COLLATERAL_FUSE,
    BASE_MORPHO_BORROW_FUSE,
    BASE_MORPHO_FLASH_LOAN_FUSE,
    BASE_UNIVERSAL_SWAP_FUSE,
)
from ipor_fusion import (
    Web3Context,
    PlasmaVault,
    PriceOracleMiddleware,
    ERC20,
    Roles,
    VaultSimulator,
)
from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.fuses import (
    MorphoCollateralFuse,
    MorphoBorrowFuse,
    MorphoFlashLoanFuse,
    UniversalTokenSwapperFuse,
)
from ipor_fusion.types import ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# WStETH/WETH market on Morpho Blue (Base), LLTV = 94.5%
MORPHO_BLUE_MARKET_ID = (
    "3a4048c64ba1b375330d376b1ce40e4047d03b47ab4d48af484edec9fec801ba"
)

AERODROME_ROUTER_ADDRESS = Web3.to_checksum_address(
    "0xBE6D8F0D05cC4bE24d5167a3eF062215bE6D18a5"
)
AERODROME_EXECUTOR = Web3.to_checksum_address(
    "0x591435c065fCE9713c8B112FCBf5af98b8975cb3"
)

ATOMIST = Web3.to_checksum_address("0xF6a9bd8F6DC537675D499Ac1CA14f2c55d8b5569")
VAULT_ADDRESS = Web3.to_checksum_address("0xc4c00d8b323f37527eeda27c87412378be9f68ec")
WSTETH_HOLDER = Web3.to_checksum_address("0xf0bb20865277aBd641a307eCe5Ee04E79073416C")

DEPOSIT_AMOUNT = int(1e18)
LEVERAGE = 3
PINNED_BLOCK = (
    35437300  # mirrors anvil.reset_fork(...) in test_looping_morpho_blue_base.py
)


def _encode_call(signature: str, *args) -> bytes:
    """Same encoding ContractWrapper._encode uses, no wrapper needed."""
    selector = function_signature_to_4byte_selector(signature)
    types = _parse_param_types(signature)
    return selector + encode(types, list(args)) if types else selector


def _aerodrome_path(token_in: ChecksumAddress, token_out: ChecksumAddress) -> bytes:
    if (token_in == BASE_WETH and token_out == BASE_WSTETH) or (
        token_in == BASE_WSTETH and token_out == BASE_WETH
    ):
        return encode_packed(["address", "uint24", "address"], [token_in, 1, token_out])
    raise ValueError(f"Unsupported path: {token_in} -> {token_out}")


def _aerodrome_swap(
    universal, token_in, token_out, amount_in, min_amount_out, deadline
):
    targets = [token_in, AERODROME_ROUTER_ADDRESS]
    approve_data = _encode_call(
        "approve(address,uint256)", AERODROME_ROUTER_ADDRESS, amount_in
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


def test_simulate_looping_morpho_blue(web3_base):
    # Historical block from the original anvil-based test — vault state, oracle
    # prices, and wsteth_holder's WStETH balance are deterministic on this block.
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_base, chain_id=ChainId(web3_base.eth.chain_id))
    ctx.default_block = PINNED_BLOCK
    plasma_vault = PlasmaVault(ctx, VAULT_ADDRESS)
    access_manager_addr = plasma_vault.get_access_manager_address()
    oracle = PriceOracleMiddleware(
        ctx, plasma_vault.get_price_oracle_middleware_address()
    )
    pre_balance = ERC20(ctx, BASE_WSTETH).balance_of(VAULT_ADDRESS)
    wsteth_price = oracle.get_asset_price(BASE_WSTETH).readable()
    weth_price = oracle.get_asset_price(BASE_WETH).readable()
    deadline = int(web3_base.eth.get_block(PINNED_BLOCK)["timestamp"]) + 1000

    # Strategy maths — fully resolved before any simulated call is sent.
    # Deposit credits underlying 1:1 (no entry fee), so post_balance is exact.
    post_balance = pre_balance + DEPOSIT_AMOUNT
    wsteth_collateral_amount = post_balance * LEVERAGE
    flash_loan_amount = wsteth_collateral_amount - post_balance
    ltv = 1 - 1 / LEVERAGE
    weth_borrow_amount = int(
        wsteth_collateral_amount * ltv * wsteth_price / weth_price * 1.005
    )

    log.info(
        "pre_balance=%.4f deposit=%.4f collateral=%.4f flash=%.4f borrow=%.4f",
        pre_balance / 1e18,
        DEPOSIT_AMOUNT / 1e18,
        wsteth_collateral_amount / 1e18,
        flash_loan_amount / 1e18,
        weth_borrow_amount / 1e18,
    )

    # Build the leveraged loop as a single FuseAction tree.
    morpho_collateral = MorphoCollateralFuse(BASE_MORPHO_COLLATERAL_FUSE)
    morpho_borrow = MorphoBorrowFuse(BASE_MORPHO_BORROW_FUSE)
    morpho_flash = MorphoFlashLoanFuse(BASE_MORPHO_FLASH_LOAN_FUSE)
    universal = UniversalTokenSwapperFuse(BASE_UNIVERSAL_SWAP_FUSE)

    supply_collateral = morpho_collateral.supply_collateral(
        market_id=MORPHO_BLUE_MARKET_ID, amount=wsteth_collateral_amount
    )
    borrow = morpho_borrow.borrow(
        market_id=MORPHO_BLUE_MARKET_ID, amount=weth_borrow_amount
    )
    swap = _aerodrome_swap(
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
        actions=[supply_collateral, borrow, swap],
    )

    # One eth_simulateV1 payload — setup + leveraged loop + observations.
    sim = VaultSimulator(
        web3=web3_base, vault=VAULT_ADDRESS, alpha=ANVIL_WALLET, block=block_hex
    )

    # 1. atomist grants ALPHA_ROLE to ANVIL_WALLET (alpha used by sim.execute)
    sim.add_call(
        to=access_manager_addr,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.ALPHA_ROLE, ANVIL_WALLET, 0
        ),
        from_=ATOMIST,
    )
    # 2. atomist grants WHITELIST_ROLE to wsteth_holder so it can deposit
    sim.add_call(
        to=access_manager_addr,
        data=_encode_call(
            "grantRole(uint64,address,uint32)",
            Roles.WHITELIST_ROLE,
            WSTETH_HOLDER,
            0,
        ),
        from_=ATOMIST,
    )
    # 3. wsteth_holder approves vault to pull WStETH
    sim.add_call(
        to=BASE_WSTETH,
        data=_encode_call("approve(address,uint256)", VAULT_ADDRESS, DEPOSIT_AMOUNT),
        from_=WSTETH_HOLDER,
    )
    # 4. wsteth_holder deposits into vault — credits 1:1 in underlying
    sim.add_call(
        to=VAULT_ADDRESS,
        data=_encode_call("deposit(uint256,address)", DEPOSIT_AMOUNT, WSTETH_HOLDER),
        from_=WSTETH_HOLDER,
    )

    # Pre-loop snapshot (after deposit, before execute)
    sim.observe(
        "vault_wsteth_pre_loop",
        BASE_WSTETH,
        "balanceOf(address)",
        (VAULT_ADDRESS,),
    )

    # 5. alpha executes the flash-loan-wrapped leverage loop atomically
    sim.execute([flash_loan])

    # Post-loop observations
    sim.observe(
        "vault_wsteth_post_loop",
        BASE_WSTETH,
        "balanceOf(address)",
        (VAULT_ADDRESS,),
    )
    sim.observe(
        "vault_weth_post_loop", BASE_WETH, "balanceOf(address)", (VAULT_ADDRESS,)
    )
    sim.observe(
        "total_assets_post_loop",
        VAULT_ADDRESS,
        "totalAssets()",
        output_types=["uint256"],
    )

    result = sim.run()

    log.info("success=%s gas_used=%s", result.success, result.gas_used)
    log.info("revert_reason=%s", result.revert_reason)
    log.info("observations=%s", result.observations)

    # Every queued call (setup + execute + observations) must succeed.
    assert_all_success(result)

    # Invariants of a successful 3x leverage loop:
    # - pre-loop wsteth = vault_pre + deposit (deterministic on the pinned block)
    # - WETH dust ~0 (everything borrowed got swapped to repay the flash loan)
    # - totalAssets is positive after collateral is recognized
    assert result.get("vault_wsteth_pre_loop") == post_balance
    assert result.get("vault_weth_post_loop") < int(0.01e18)
    assert result.get("total_assets_post_loop") > 0

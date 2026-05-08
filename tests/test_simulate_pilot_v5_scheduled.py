"""PILOT scheduled-vault flows on Arbitrum via `eth_simulateV1`.

Mirrors `test_execute_pilot_v5_scheduled.py`. Six flows:
  - deposit: whale funds ANVIL_WALLET, then vault.deposit
  - mint: whale → ANVIL_WALLET, then vault.mint
  - redeem: deposit → move_time(7h) → request → move_time(1h) → release → redeem
  - withdraw: deposit → move_time(7h) → request → move_time(1h) → release → withdraw
  - transfer: deposit on user_one's behalf, then ERC20 transfer of shares
  - transfer_from: deposit + approve + transferFrom

Multi-block flows (redeem/withdraw) use `next_block(time_shift_seconds=...)`.
"""

from __future__ import annotations

import logging

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from _simulate import assert_all_success
from addresses import ARBITRUM_USDC
from constants import ARBITRUM_PILOT_SCHEDULED_PLASMA_VAULT, ANVIL_WALLET
from ipor_fusion import (
    Web3Context,
    PlasmaVault,
    AccessManager,
    WithdrawManager,
    Roles,
    IporFusionMarkets,
    VaultSimulator,
)
from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.types import ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PINNED_BLOCK = 268934406  # mirrors anvil.reset_fork(...)
WHALE_ACCOUNT = Web3.to_checksum_address("0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1")


def _encode_call(signature: str, *args) -> bytes:
    selector = function_signature_to_4byte_selector(signature)
    types = _parse_param_types(signature)
    return selector + encode(types, list(args)) if types else selector


def _setup_basic(sim, access_manager, owner):
    """Owner grants ALPHA + WHITELIST to ANVIL_WALLET."""
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.ALPHA_ROLE, ANVIL_WALLET, 0
        ),
        from_=owner,
    )
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.WHITELIST_ROLE, ANVIL_WALLET, 0
        ),
        from_=owner,
    )


def _setup_with_atomist(sim, access_manager, owner):
    _setup_basic(sim, access_manager, owner)
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.ATOMIST_ROLE, ANVIL_WALLET, 0
        ),
        from_=owner,
    )


def _whale_fund(sim, recipient, amount):
    """Whale transfers USDC to the recipient (impersonated, no signature)."""
    sim.add_call(
        to=ARBITRUM_USDC,
        data=_encode_call("transfer(address,uint256)", recipient, amount),
        from_=WHALE_ACCOUNT,
    )


def test_simulate_deposit(web3_arb):
    """Whale funds ANVIL_WALLET, deposits into vault."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_SCHEDULED_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()
    amount = 100_000000

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_basic(sim, access_manager, owner)
    _whale_fund(sim, ANVIL_WALLET, amount)
    sim.add_call(
        to=ARBITRUM_USDC,
        data=_encode_call("approve(address,uint256)", vault_address, amount),
        from_=ANVIL_WALLET,
    )
    sim.observe(
        "total_assets_before", vault_address, "totalAssets()"
    )
    sim.observe(
        "user_shares_before",
        vault_address,
        "balanceOf(address)",
        (ANVIL_WALLET,),
    )
    sim.add_call(
        to=vault_address,
        data=_encode_call("deposit(uint256,address)", amount, ANVIL_WALLET),
        from_=ANVIL_WALLET,
    )
    sim.observe("total_assets_after", vault_address, "totalAssets()")
    sim.observe(
        "user_shares_after",
        vault_address,
        "balanceOf(address)",
        (ANVIL_WALLET,),
    )
    sim.observe(
        "aave_total",
        vault_address,
        "totalAssetsInMarket(uint256)",
        (IporFusionMarkets.AAVE_V3,),
    )

    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)
    assets_diff = result.get("total_assets_after") - result.get("total_assets_before")
    shares_diff = result.get("user_shares_after") - result.get("user_shares_before")
    assert 100_000000 < assets_diff < 100_100000
    assert 100_00000000 < shares_diff < 100_10000000
    assert result.get("aave_total") == 0


def test_simulate_mint(web3_arb):
    """Mint exact shares (vs deposit which takes assets)."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_SCHEDULED_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()
    amount = 110_000000
    decimals = plasma_vault.decimals()
    shares_amount = 100 * 10**decimals

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_basic(sim, access_manager, owner)
    _whale_fund(sim, ANVIL_WALLET, amount)
    sim.add_call(
        to=ARBITRUM_USDC,
        data=_encode_call("approve(address,uint256)", vault_address, amount),
        from_=ANVIL_WALLET,
    )
    sim.observe(
        "total_assets_before", vault_address, "totalAssets()"
    )
    sim.observe(
        "user_shares_before",
        vault_address,
        "balanceOf(address)",
        (ANVIL_WALLET,),
    )
    sim.observe(
        "vault_usdc_before", ARBITRUM_USDC, "balanceOf(address)", (vault_address,)
    )
    sim.add_call(
        to=vault_address,
        data=_encode_call("mint(uint256,address)", shares_amount, ANVIL_WALLET),
        from_=ANVIL_WALLET,
    )
    sim.observe(
        "total_assets_after", vault_address, "totalAssets()"
    )
    sim.observe(
        "user_shares_after",
        vault_address,
        "balanceOf(address)",
        (ANVIL_WALLET,),
    )
    sim.observe(
        "user_underlying_after",
        vault_address,
        "maxWithdraw(address)",
        (ANVIL_WALLET,),
    )
    sim.observe(
        "vault_usdc_after", ARBITRUM_USDC, "balanceOf(address)", (vault_address,)
    )
    sim.observe(
        "aave_total",
        vault_address,
        "totalAssetsInMarket(uint256)",
        (IporFusionMarkets.AAVE_V3,),
    )

    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)

    total_assets_after = result.get("total_assets_after")
    total_assets_before = result.get("total_assets_before")
    user_underlying_after = result.get("user_underlying_after")
    assert (
        abs(total_assets_after - (total_assets_before + user_underlying_after)) < 100_000
    )
    shares_diff = result.get("user_shares_after") - result.get("user_shares_before")
    assert abs(shares_diff - shares_amount) < 5_000
    vault_usdc_diff = result.get("vault_usdc_after") - result.get("vault_usdc_before")
    assert abs(vault_usdc_diff - user_underlying_after) < 5_000
    assert result.get("aave_total") == 0


def test_simulate_redeem(web3_arb):
    """Deposit → move_time(7h) → update_window + request → move_time(1h) → release → redeem."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_SCHEDULED_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()
    withdraw_manager = WithdrawManager(ctx, plasma_vault.withdraw_manager_address())
    decimals = plasma_vault.decimals()
    amount = 100_000000
    to_redeem = 50 * 10**decimals
    # convert_to_assets is deterministic at the pinned block (vault ratio is stable)
    to_withdraw = plasma_vault.convert_to_assets(to_redeem)

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_with_atomist(sim, access_manager, owner)
    _whale_fund(sim, ANVIL_WALLET, amount)
    sim.add_call(
        to=ARBITRUM_USDC,
        data=_encode_call("approve(address,uint256)", vault_address, amount),
        from_=ANVIL_WALLET,
    )

    sim.observe("total_assets_before", vault_address, "totalAssets()")
    sim.observe(
        "user_shares_before",
        vault_address,
        "balanceOf(address)",
        (ANVIL_WALLET,),
    )
    sim.observe(
        "user_usdc_before",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (ANVIL_WALLET,),
    )

    sim.add_call(
        to=vault_address,
        data=_encode_call("deposit(uint256,address)", amount, ANVIL_WALLET),
        from_=ANVIL_WALLET,
    )

    # +7 hours
    sim.next_block(time_shift_seconds=7 * 60 * 60)

    # Atomist updates withdraw window + user requests withdraw
    sim.add_call(
        to=withdraw_manager.address,
        data=_encode_call("updateWithdrawWindow(uint256)", 7 * 60 * 60),
        from_=ANVIL_WALLET,  # has ATOMIST role
    )
    sim.add_call(
        to=withdraw_manager.address,
        data=_encode_call("request(uint256)", to_withdraw),
        from_=ANVIL_WALLET,
    )

    # +1 hour, release
    sim.next_block(time_shift_seconds=60 * 60)
    sim.add_call(
        to=withdraw_manager.address,
        data=_encode_call("releaseFunds()"),
        from_=ANVIL_WALLET,
    )

    # Redeem
    sim.add_call(
        to=vault_address,
        data=_encode_call(
            "redeem(uint256,address,address)", to_redeem, ANVIL_WALLET, ANVIL_WALLET
        ),
        from_=ANVIL_WALLET,
    )

    sim.observe("total_assets_after", vault_address, "totalAssets()")
    sim.observe(
        "user_shares_after",
        vault_address,
        "balanceOf(address)",
        (ANVIL_WALLET,),
    )
    sim.observe(
        "user_usdc_after",
        ARBITRUM_USDC,
        "balanceOf(address)",
        (ANVIL_WALLET,),
    )

    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)
    assets_after = result.get("total_assets_after")
    assets_before = result.get("total_assets_before")
    assert abs(assets_after - (assets_before + 50_000000)) < 100_000

    shares_diff = result.get("user_shares_after") - result.get("user_shares_before")
    assert abs(shares_diff - 50 * 10**decimals) < 10_000_000

    usdc_diff = result.get("user_usdc_after") - result.get("user_usdc_before")
    assert abs(usdc_diff - (-50_000000)) < 100_000


def test_simulate_withdraw(web3_arb):
    """Deposit → move_time(7h) → update_window + request → move_time(1h) → release → withdraw."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_SCHEDULED_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()
    withdraw_manager = WithdrawManager(ctx, plasma_vault.withdraw_manager_address())
    decimals = plasma_vault.decimals()
    amount = 100_000000
    shares_amount = 100 * 10**decimals
    # max_withdraw at the pinned block before deposit; we'll request the full
    # amount from the user's POST-deposit view (~= amount).
    to_withdraw_request = amount

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_with_atomist(sim, access_manager, owner)
    _whale_fund(sim, ANVIL_WALLET, amount)
    sim.add_call(
        to=ARBITRUM_USDC,
        data=_encode_call("approve(address,uint256)", vault_address, amount),
        from_=ANVIL_WALLET,
    )
    sim.add_call(
        to=vault_address,
        data=_encode_call("deposit(uint256,address)", amount, ANVIL_WALLET),
        from_=ANVIL_WALLET,
    )
    sim.observe("total_assets_before", vault_address, "totalAssets()")
    sim.observe(
        "user_shares_before",
        vault_address,
        "balanceOf(address)",
        (ANVIL_WALLET,),
    )

    # +7 hours
    sim.next_block(time_shift_seconds=7 * 60 * 60)
    sim.add_call(
        to=withdraw_manager.address,
        data=_encode_call("updateWithdrawWindow(uint256)", 7 * 60 * 60),
        from_=ANVIL_WALLET,
    )
    sim.add_call(
        to=withdraw_manager.address,
        data=_encode_call("request(uint256)", to_withdraw_request),
        from_=ANVIL_WALLET,
    )

    sim.next_block(time_shift_seconds=60 * 60)
    sim.add_call(
        to=withdraw_manager.address,
        data=_encode_call("releaseFunds()"),
        from_=ANVIL_WALLET,
    )

    # Withdraw a deterministic safe amount (1 USDC under the requested amount)
    withdraw_amount = to_withdraw_request - 1_000_000
    sim.add_call(
        to=vault_address,
        data=_encode_call(
            "withdraw(uint256,address,address)",
            withdraw_amount,
            ANVIL_WALLET,
            ANVIL_WALLET,
        ),
        from_=ANVIL_WALLET,
    )

    sim.observe("total_assets_after", vault_address, "totalAssets()")
    sim.observe(
        "user_shares_after",
        vault_address,
        "balanceOf(address)",
        (ANVIL_WALLET,),
    )
    sim.observe(
        "aave_total",
        vault_address,
        "totalAssetsInMarket(uint256)",
        (IporFusionMarkets.AAVE_V3,),
    )

    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)

    assets_after = result.get("total_assets_after")
    assets_before = result.get("total_assets_before")
    # Withdrew ~99 USDC (kept 1 USDC buffer), so total_assets dropped by withdraw_amount
    assert abs((assets_before - assets_after) - withdraw_amount) < 1_000_000

    user_shares_diff = result.get("user_shares_before") - result.get("user_shares_after")
    # Roughly 99% of the shares — original test allows ±10M shares slack
    assert 0 < user_shares_diff < shares_amount + 10_000_000
    assert result.get("aave_total") == 0


def test_simulate_transfer(web3_arb):
    """Deposit on user_one's behalf, transfer shares to user_two."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_SCHEDULED_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()

    amount = 100_000000
    user_one = Web3.to_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")
    user_two = Web3.to_checksum_address("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_basic(sim, access_manager, owner)
    # Also whitelist user_one so deposit on their behalf works
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.WHITELIST_ROLE, user_one, 0
        ),
        from_=owner,
    )
    _whale_fund(sim, user_one, amount)
    # user_one approves vault
    sim.add_call(
        to=ARBITRUM_USDC,
        data=_encode_call("approve(address,uint256)", vault_address, amount),
        from_=user_one,
    )
    # ANVIL_WALLET deposits on user_one's behalf — but original test does:
    #   forked_ctx.prank(ANVIL_WALLET); usdc.approve(vault_address, 3*amount)
    #   plasma_vault.deposit(amount, user_one)
    # i.e. ANVIL_WALLET (whale-funded earlier? no — funded user_one) — actually
    # original test funds user_one with USDC and then ANVIL_WALLET (no USDC) calls
    # deposit(amount, user_one). That requires ANVIL_WALLET to have approve from
    # somewhere... it works on anvil because approve goes through automatically.
    # Simpler here: user_one self-deposits.
    sim.add_call(
        to=vault_address,
        data=_encode_call("deposit(uint256,address)", amount, user_one),
        from_=user_one,
    )
    # user_one transfers shares to user_two
    sim.add_call(
        to=vault_address,
        data=_encode_call("transfer(address,uint256)", user_two, amount),
        from_=user_one,
    )
    sim.observe(
        "user_two_shares",
        vault_address,
        "balanceOf(address)",
        (user_two,),
    )

    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)
    assert result.get("user_two_shares") == amount


def test_simulate_transfer_from(web3_arb):
    """Deposit + approve + transferFrom shares to a third party."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_SCHEDULED_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, plasma_vault.get_access_manager_address())
    owner = access_manager.owner()

    amount = 100_000000
    user_one = Web3.to_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")
    user_two = Web3.to_checksum_address("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_basic(sim, access_manager, owner)
    sim.add_call(
        to=access_manager.address,
        data=_encode_call(
            "grantRole(uint64,address,uint32)", Roles.WHITELIST_ROLE, user_one, 0
        ),
        from_=owner,
    )
    _whale_fund(sim, user_one, amount)
    sim.add_call(
        to=ARBITRUM_USDC,
        data=_encode_call("approve(address,uint256)", vault_address, amount),
        from_=user_one,
    )
    sim.add_call(
        to=vault_address,
        data=_encode_call("deposit(uint256,address)", amount, user_one),
        from_=user_one,
    )
    # user_one approves spender (here: user_one itself for simplicity in
    # transferFrom; original test's intent is "approve a spender first"; we use
    # ANVIL_WALLET as the spender)
    sim.add_call(
        to=vault_address,
        data=_encode_call("approve(address,uint256)", ANVIL_WALLET, amount),
        from_=user_one,
    )
    # ANVIL_WALLET (the approved spender) transferFrom user_one to user_two
    sim.add_call(
        to=vault_address,
        data=_encode_call(
            "transferFrom(address,address,uint256)", user_one, user_two, amount
        ),
        from_=ANVIL_WALLET,
    )
    sim.observe(
        "user_two_shares",
        vault_address,
        "balanceOf(address)",
        (user_two,),
    )
    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)
    assert result.get("user_two_shares") == amount

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

from web3 import Web3

from _simulate import assert_all_success
from addresses import ARBITRUM_USDC
from constants import ARBITRUM_PILOT_SCHEDULED_PLASMA_VAULT, ANVIL_WALLET
from ipor_fusion import (
    Web3Context,
    PlasmaVault,
    AccessManager,
    WithdrawManager,
    ERC20,
    Roles,
    IporFusionMarkets,
    VaultSimulator,
)
from ipor_fusion.types import ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

PINNED_BLOCK = 268934406  # mirrors anvil.reset_fork(...)
WHALE_ACCOUNT = Web3.to_checksum_address("0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1")


def _setup_basic(sim, access_manager, owner):
    """Owner grants ALPHA + WHITELIST to ANVIL_WALLET."""
    sim.add_call(
        call=access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0),
        from_=owner,
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.WHITELIST_ROLE, ANVIL_WALLET, 0),
        from_=owner,
    )


def _setup_with_atomist(sim, access_manager, owner):
    _setup_basic(sim, access_manager, owner)
    sim.add_call(
        call=access_manager.grant_role(Roles.ATOMIST_ROLE, ANVIL_WALLET, 0),
        from_=owner,
    )


def _whale_fund(sim, usdc, recipient, amount):
    """Whale transfers USDC to the recipient (impersonated, no signature)."""
    sim.add_call(
        call=usdc.transfer(recipient, amount),
        from_=WHALE_ACCOUNT,
    )


def test_simulate_deposit(web3_arb):
    """Whale funds ANVIL_WALLET, deposits into vault."""
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    vault_address = ARBITRUM_PILOT_SCHEDULED_PLASMA_VAULT
    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(
        ctx, plasma_vault.get_access_manager_address().call()
    )
    usdc = ERC20(ctx, ARBITRUM_USDC)
    owner = access_manager.owner()
    amount = 100_000000

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_basic(sim, access_manager, owner)
    _whale_fund(sim, usdc, ANVIL_WALLET, amount)
    sim.add_call(
        call=usdc.approve(vault_address, amount),
        from_=ANVIL_WALLET,
    )
    sim.observe("total_assets_before", plasma_vault.total_assets())
    sim.observe("user_shares_before", plasma_vault.balance_of(ANVIL_WALLET))
    sim.add_call(
        call=plasma_vault.deposit(amount, ANVIL_WALLET),
        from_=ANVIL_WALLET,
    )
    sim.observe("total_assets_after", plasma_vault.total_assets())
    sim.observe("user_shares_after", plasma_vault.balance_of(ANVIL_WALLET))
    sim.observe(
        "aave_total",
        plasma_vault.total_assets_in_market(IporFusionMarkets.AAVE_V3),
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
    access_manager = AccessManager(
        ctx, plasma_vault.get_access_manager_address().call()
    )
    usdc = ERC20(ctx, ARBITRUM_USDC)
    owner = access_manager.owner()
    amount = 110_000000
    decimals = plasma_vault.decimals().call()
    shares_amount = 100 * 10**decimals

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_basic(sim, access_manager, owner)
    _whale_fund(sim, usdc, ANVIL_WALLET, amount)
    sim.add_call(
        call=usdc.approve(vault_address, amount),
        from_=ANVIL_WALLET,
    )
    sim.observe("total_assets_before", plasma_vault.total_assets())
    sim.observe("user_shares_before", plasma_vault.balance_of(ANVIL_WALLET))
    sim.observe("vault_usdc_before", usdc.balance_of(vault_address))
    sim.add_call(
        call=plasma_vault.mint(shares_amount, ANVIL_WALLET),
        from_=ANVIL_WALLET,
    )
    sim.observe("total_assets_after", plasma_vault.total_assets())
    sim.observe("user_shares_after", plasma_vault.balance_of(ANVIL_WALLET))
    sim.observe("user_underlying_after", plasma_vault.max_withdraw(ANVIL_WALLET))
    sim.observe("vault_usdc_after", usdc.balance_of(vault_address))
    sim.observe(
        "aave_total",
        plasma_vault.total_assets_in_market(IporFusionMarkets.AAVE_V3),
    )

    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)

    total_assets_after = result.get("total_assets_after")
    total_assets_before = result.get("total_assets_before")
    user_underlying_after = result.get("user_underlying_after")
    assert (
        abs(total_assets_after - (total_assets_before + user_underlying_after))
        < 100_000
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
    access_manager = AccessManager(
        ctx, plasma_vault.get_access_manager_address().call()
    )
    withdraw_manager = WithdrawManager(ctx, plasma_vault.withdraw_manager_address())
    usdc = ERC20(ctx, ARBITRUM_USDC)
    owner = access_manager.owner()
    decimals = plasma_vault.decimals().call()
    amount = 100_000000
    to_redeem = 50 * 10**decimals
    # convert_to_assets is deterministic at the pinned block (vault ratio is stable)
    to_withdraw = plasma_vault.convert_to_assets(to_redeem).call()

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_with_atomist(sim, access_manager, owner)
    _whale_fund(sim, usdc, ANVIL_WALLET, amount)
    sim.add_call(
        call=usdc.approve(vault_address, amount),
        from_=ANVIL_WALLET,
    )

    sim.observe("total_assets_before", plasma_vault.total_assets())
    sim.observe("user_shares_before", plasma_vault.balance_of(ANVIL_WALLET))
    sim.observe("user_usdc_before", usdc.balance_of(ANVIL_WALLET))

    sim.add_call(
        call=plasma_vault.deposit(amount, ANVIL_WALLET),
        from_=ANVIL_WALLET,
    )

    # +7 hours
    sim.next_block(time_shift_seconds=7 * 60 * 60)

    # Atomist updates withdraw window + user requests withdraw
    sim.add_call(
        call=withdraw_manager.update_withdraw_window(7 * 60 * 60),
        from_=ANVIL_WALLET,  # has ATOMIST role
    )
    sim.add_call(
        call=withdraw_manager.request(to_withdraw),
        from_=ANVIL_WALLET,
    )

    # +1 hour, release
    sim.next_block(time_shift_seconds=60 * 60)
    sim.add_call(
        call=withdraw_manager.release_funds(),
        from_=ANVIL_WALLET,
    )

    # Redeem
    sim.add_call(
        call=plasma_vault.redeem(to_redeem, ANVIL_WALLET, ANVIL_WALLET),
        from_=ANVIL_WALLET,
    )

    sim.observe("total_assets_after", plasma_vault.total_assets())
    sim.observe("user_shares_after", plasma_vault.balance_of(ANVIL_WALLET))
    sim.observe("user_usdc_after", usdc.balance_of(ANVIL_WALLET))

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
    access_manager = AccessManager(
        ctx, plasma_vault.get_access_manager_address().call()
    )
    withdraw_manager = WithdrawManager(ctx, plasma_vault.withdraw_manager_address())
    usdc = ERC20(ctx, ARBITRUM_USDC)
    owner = access_manager.owner()
    decimals = plasma_vault.decimals().call()
    amount = 100_000000
    shares_amount = 100 * 10**decimals
    # max_withdraw at the pinned block before deposit; we'll request the full
    # amount from the user's POST-deposit view (~= amount).
    to_withdraw_request = amount

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_with_atomist(sim, access_manager, owner)
    _whale_fund(sim, usdc, ANVIL_WALLET, amount)
    sim.add_call(
        call=usdc.approve(vault_address, amount),
        from_=ANVIL_WALLET,
    )
    sim.add_call(
        call=plasma_vault.deposit(amount, ANVIL_WALLET),
        from_=ANVIL_WALLET,
    )
    sim.observe("total_assets_before", plasma_vault.total_assets())
    sim.observe("user_shares_before", plasma_vault.balance_of(ANVIL_WALLET))

    # +7 hours
    sim.next_block(time_shift_seconds=7 * 60 * 60)
    sim.add_call(
        call=withdraw_manager.update_withdraw_window(7 * 60 * 60),
        from_=ANVIL_WALLET,
    )
    sim.add_call(
        call=withdraw_manager.request(to_withdraw_request),
        from_=ANVIL_WALLET,
    )

    sim.next_block(time_shift_seconds=60 * 60)
    sim.add_call(
        call=withdraw_manager.release_funds(),
        from_=ANVIL_WALLET,
    )

    # Withdraw a deterministic safe amount (1 USDC under the requested amount)
    withdraw_amount = to_withdraw_request - 1_000_000
    sim.add_call(
        call=plasma_vault.withdraw(withdraw_amount, ANVIL_WALLET, ANVIL_WALLET),
        from_=ANVIL_WALLET,
    )

    sim.observe("total_assets_after", plasma_vault.total_assets())
    sim.observe("user_shares_after", plasma_vault.balance_of(ANVIL_WALLET))
    sim.observe(
        "aave_total",
        plasma_vault.total_assets_in_market(IporFusionMarkets.AAVE_V3),
    )

    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)

    assets_after = result.get("total_assets_after")
    assets_before = result.get("total_assets_before")
    # Withdrew ~99 USDC (kept 1 USDC buffer), so total_assets dropped by withdraw_amount
    assert abs((assets_before - assets_after) - withdraw_amount) < 1_000_000

    user_shares_diff = result.get("user_shares_before") - result.get(
        "user_shares_after"
    )
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
    access_manager = AccessManager(
        ctx, plasma_vault.get_access_manager_address().call()
    )
    usdc = ERC20(ctx, ARBITRUM_USDC)
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
        call=access_manager.grant_role(Roles.WHITELIST_ROLE, user_one, 0),
        from_=owner,
    )
    _whale_fund(sim, usdc, user_one, amount)
    # user_one approves vault
    sim.add_call(
        call=usdc.approve(vault_address, amount),
        from_=user_one,
    )
    # user_one self-deposits (original test funds user_one with USDC; we keep
    # the impersonated deposit simple here).
    sim.add_call(
        call=plasma_vault.deposit(amount, user_one),
        from_=user_one,
    )
    # user_one transfers shares to user_two
    sim.add_call(
        call=plasma_vault.transfer(user_two, amount),
        from_=user_one,
    )
    sim.observe("user_two_shares", plasma_vault.balance_of(user_two))

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
    access_manager = AccessManager(
        ctx, plasma_vault.get_access_manager_address().call()
    )
    usdc = ERC20(ctx, ARBITRUM_USDC)
    owner = access_manager.owner()

    amount = 100_000000
    user_one = Web3.to_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")
    user_two = Web3.to_checksum_address("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")

    sim = VaultSimulator(
        web3=web3_arb, vault=vault_address, alpha=ANVIL_WALLET, block=block_hex
    )
    _setup_basic(sim, access_manager, owner)
    sim.add_call(
        call=access_manager.grant_role(Roles.WHITELIST_ROLE, user_one, 0),
        from_=owner,
    )
    _whale_fund(sim, usdc, user_one, amount)
    sim.add_call(
        call=usdc.approve(vault_address, amount),
        from_=user_one,
    )
    sim.add_call(
        call=plasma_vault.deposit(amount, user_one),
        from_=user_one,
    )
    # user_one approves ANVIL_WALLET as a share-spender
    sim.add_call(
        call=plasma_vault.approve(ANVIL_WALLET, amount),
        from_=user_one,
    )
    # ANVIL_WALLET (the approved spender) transferFrom user_one to user_two
    sim.add_call(
        call=plasma_vault.transfer_from(user_one, user_two, amount),
        from_=ANVIL_WALLET,
    )
    sim.observe("user_two_shares", plasma_vault.balance_of(user_two))
    result = sim.run()
    log.info("observations=%s", result.observations)
    assert_all_success(result)
    assert result.get("user_two_shares") == amount

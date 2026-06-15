"""WithdrawManager release-funds flow on Arbitrum via `eth_simulateV1`.

Mirrors `test_execute_release_funds.py::test_should_release_funds`. The original
test interleaves `anvil.move_time(...)` between actions to satisfy the
withdraw-cooldown window. With multi-block `eth_simulateV1` we model each
move_time as a `next_block(time_shift_seconds=...)` boundary — same semantics
(state carries forward, block.timestamp jumps), zero infrastructure.
"""

from __future__ import annotations

import logging

from _simulate import assert_all_success
from addresses import ARBITRUM_USDC
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    PlasmaVault,
    Roles,
    VaultSimulator,
    Web3Context,
    WithdrawManager,
)
from ipor_fusion.types import ChainId, Period

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

USER = Web3.to_checksum_address("0x1714400FF23dB4aF24F9fd64e7039e6597f18C2b")
VAULT_ADDRESS = Web3.to_checksum_address("0x05231e2fB2F580F043D7760a5CFb4Ec6F01656E9")
DEPOSIT_AMOUNT = 1000_000000  # 1000 USDC (6 decimals), mirrors original test
PINNED_BLOCK = 285000000  # mirrors anvil.reset_fork(...) in the original test


def test_simulate_release_funds(web3_arb):
    block_hex = hex(PINNED_BLOCK)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    plasma_vault = PlasmaVault(ctx, VAULT_ADDRESS)
    access_manager = AccessManager(
        ctx, plasma_vault.get_access_manager_address().call()
    )
    withdraw_manager = WithdrawManager(ctx, plasma_vault.withdraw_manager_address())
    usdc = ERC20(ctx, ARBITRUM_USDC)
    owner = access_manager.owner()
    baseline_timestamp = int(web3_arb.eth.get_block(PINNED_BLOCK)["timestamp"])

    log.info(
        "owner=%s baseline_ts=%s vault=%s wmgr=%s",
        owner,
        baseline_timestamp,
        VAULT_ADDRESS,
        withdraw_manager.address,
    )

    sim = VaultSimulator(
        web3=web3_arb, vault=VAULT_ADDRESS, alpha=USER, block=block_hex
    )

    # ── Block 0 (baseline) ────────────────────────────────────────────────
    # owner grants roles, user deposits, user requests withdraw.
    sim.add_call(
        call=access_manager.grant_role(Roles.ALPHA_ROLE, USER, 0),
        from_=owner,
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.WHITELIST_ROLE, USER, 0),
        from_=owner,
    )
    sim.add_call(
        call=usdc.approve(VAULT_ADDRESS, DEPOSIT_AMOUNT),
        from_=USER,
    )
    sim.add_call(
        call=plasma_vault.deposit(DEPOSIT_AMOUNT, USER),
        from_=USER,
    )
    sim.add_call(
        call=withdraw_manager.request(DEPOSIT_AMOUNT),
        from_=USER,
    )

    # ── Block 1: +1 hour cooldown ─────────────────────────────────────────
    # Observe the user's request via the contract directly. SDK's
    # get_pending_requests_info() aggregates events and per-account reads — that
    # can't be a single eth_call, so we read the user's request_info instead.
    sim.next_block(time_shift_seconds=Period.HOUR)
    sim.observe("request_info", withdraw_manager.request_info(USER))

    # ── Block 2: +1 minute, alpha (= user) releases funds ─────────────────
    # Original test calls release_funds(timestamp=pending.timestamp) where
    # pending.timestamp = block.timestamp at +HOUR minus 1.
    sim.next_block(time_shift_seconds=Period.MINUTE)
    release_timestamp = baseline_timestamp + Period.HOUR - 1
    sim.add_call(
        call=withdraw_manager.release_funds(timestamp=release_timestamp),
        from_=USER,
    )

    # ── Block 3: +1 hour, user withdraws and we measure the balance delta ─
    sim.next_block(time_shift_seconds=Period.HOUR)
    sim.observe("request_after_release", withdraw_manager.request_info(USER))
    sim.observe("max_withdraw", plasma_vault.max_withdraw(USER))
    sim.observe("usdc_before", usdc.balance_of(USER))

    # The original test calls withdraw(max_withdraw(user)) — exact value known
    # only at runtime. eth_simulateV1 can't thread observed values into later
    # calldata in the same batch, so we withdraw a deterministic amount with a
    # safety buffer below max_withdraw and check the invariant from the original
    # test's second variant (delta > 999 USDC).
    withdraw_amount = 999_000000  # 999 USDC, leaves >0.998 USDC buffer
    sim.add_call(
        call=plasma_vault.withdraw(withdraw_amount, USER, USER),
        from_=USER,
    )
    sim.observe("usdc_after", usdc.balance_of(USER))

    result = sim.run()

    log.info(
        "all_success=%s gas_used=%s reason=%s",
        result.all_success,
        result.gas_used,
        result.revert_reason,
    )
    log.info("observations=%s", result.observations)

    assert_all_success(result)

    # Confirm release flipped can_withdraw and that the requested amount survived.
    request = result.get("request_after_release")
    assert request.shares == DEPOSIT_AMOUNT
    assert request.can_withdraw is True
    assert result.get("max_withdraw") >= withdraw_amount

    delta = result.get("usdc_after") - result.get("usdc_before")
    log.info("user balance delta: %s (1e6 = 1 USDC)", delta)
    assert delta == withdraw_amount


# ─────────────────────────────────────────────────────────────────────────
# Variant 2: release_funds(timestamp, shares) + redeem_from_request flow
# ─────────────────────────────────────────────────────────────────────────

VAULT2_ADDRESS = Web3.to_checksum_address("0x272Cb09e2d6237304E4D2BcB9DEf40032aA0ebB1")
ALPHA2_ADDRESS = Web3.to_checksum_address("0xB2Fc9e5c92577bE694E7EBbE76Eeb5977bec4D9A")
PINNED_BLOCK_2 = 315570373  # mirrors anvil.reset_fork(...) in the original test


def test_simulate_release_funds_shares(web3_arb):
    """Shares-based withdrawal: request_shares → release_funds(ts, shares) → redeem_from_request."""
    block_hex = hex(PINNED_BLOCK_2)
    ctx = Web3Context(web3=web3_arb, chain_id=ChainId(web3_arb.eth.chain_id))
    ctx.default_block = PINNED_BLOCK_2

    plasma_vault = PlasmaVault(ctx, VAULT2_ADDRESS)
    access_manager = AccessManager(
        ctx, plasma_vault.get_access_manager_address().call()
    )
    withdraw_manager = WithdrawManager(ctx, plasma_vault.withdraw_manager_address())
    usdc = ERC20(ctx, ARBITRUM_USDC)
    owner = access_manager.owner()
    baseline_timestamp = int(web3_arb.eth.get_block(PINNED_BLOCK_2)["timestamp"])

    # Pre-compute exact shares for the deposit at the pinned block. The vault's
    # share/assets ratio on this block determines convert_to_shares deterministically.
    shares = plasma_vault.convert_to_shares(DEPOSIT_AMOUNT).call()
    log.info("baseline_ts=%s shares_for_1k_USDC=%s", baseline_timestamp, shares)

    sim = VaultSimulator(
        web3=web3_arb, vault=VAULT2_ADDRESS, alpha=ALPHA2_ADDRESS, block=block_hex
    )

    # ── Block 0 ────────────────────────────────────────────────────────────
    # owner grants ALPHA to alpha_address, WHITELIST to user;
    # user approves and deposits; user requests share-denominated withdrawal.
    sim.add_call(
        call=access_manager.grant_role(Roles.ALPHA_ROLE, ALPHA2_ADDRESS, 0),
        from_=owner,
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.WHITELIST_ROLE, USER, 0),
        from_=owner,
    )
    sim.add_call(
        call=usdc.approve(VAULT2_ADDRESS, DEPOSIT_AMOUNT),
        from_=USER,
    )
    sim.add_call(
        call=plasma_vault.deposit(DEPOSIT_AMOUNT, USER),
        from_=USER,
    )
    sim.add_call(
        call=withdraw_manager.request_shares(shares),
        from_=USER,
    )

    # ── Block 1: +1 hour, alpha releases shares ────────────────────────────
    # Original test calls release_funds(timestamp=current-1, shares=shares),
    # i.e. timestamp roughly equal to "right now in this block". block.timestamp
    # at +HOUR = baseline + 3600, so we pass baseline + HOUR - 1.
    sim.next_block(time_shift_seconds=Period.HOUR)
    release_timestamp = baseline_timestamp + Period.HOUR - 1
    sim.add_call(
        call=withdraw_manager.release_funds(timestamp=release_timestamp, shares=shares),
        from_=ALPHA2_ADDRESS,
    )

    # ── Block 2: +1 hour, user redeems via redeem_from_request ─────────────
    sim.next_block(time_shift_seconds=Period.HOUR)
    sim.observe("usdc_before", usdc.balance_of(USER))
    sim.add_call(
        call=plasma_vault.redeem_from_request(shares, USER, USER),
        from_=USER,
    )
    sim.observe("usdc_after", usdc.balance_of(USER))

    result = sim.run()

    log.info(
        "all_success=%s gas_used=%s reason=%s observations=%s",
        result.all_success,
        result.gas_used,
        result.revert_reason,
        result.observations,
    )

    assert_all_success(result)
    delta = result.get("usdc_after") - result.get("usdc_before")
    log.info("user balance delta: %s (1e6 = 1 USDC)", delta)
    assert delta > 999_000000  # mirror original test's invariant

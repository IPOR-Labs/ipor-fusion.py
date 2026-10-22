"""Zero-address withdraw manager on-chain regression test (read-only BASE state).

The live BASE PlasmaVault ``0x45aa96f0b3188D47a1DaFdbefCE1db6B37f58216``
("IPOR USDC Lending Optimizer Base") was deployed without a withdraw manager:
its only ``WithdrawManagerChanged`` event (block 22140976) carries
``address(0)``. The buggy SDK reported that zero address as a real manager, the
fetcher then queried ``getWithdrawWindow()`` on an address with no code, and the
empty ``eth_call`` data failed ABI decoding (``InsufficientDataBytes``) — taking
the whole ``vault info`` fetch down.

Drives the real ``PlasmaVault`` event replay and ``fetch_vault_data`` against
live chain data at a pinned block; assertions are on typed values. Only provider
problems skip (missing, unreachable or non-archive ``BASE_PROVIDER_URL``) — the
code under test runs unguarded, so a regression fails instead of skipping.
"""

from __future__ import annotations

from _live import archive_context
from web3 import Web3

from ipor_fusion import PlasmaVault
from ipor_fusion.vault_data import fetch_vault_data

VAULT = Web3.to_checksum_address("0x45aa96f0b3188D47a1DaFdbefCE1db6B37f58216")
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"  # BASE USDC (underlying)
CHAIN_ID = 8453
BLOCK = 50788912


def test_vault_without_withdraw_manager_fetches_on_real_base_vault():
    ctx = archive_context("BASE_PROVIDER_URL", BLOCK, VAULT)
    pv = PlasmaVault(VAULT)

    withdraw_manager = pv.read_withdraw_manager_address(ctx)
    data = fetch_vault_data(ctx, pv.address, block=BLOCK)

    # Sanity: confirm we are reading the expected vault at this block.
    assert data.vault_name == "IPOR USDC Lending Optimizer Base"
    assert data.asset.lower() == USDC
    assert data.asset_decimals == 6

    # The fix: address(0) from WithdrawManagerChanged means "no manager".
    assert withdraw_manager is None
    assert data.withdraw_manager is None
    assert data.withdraw_manager_data is None

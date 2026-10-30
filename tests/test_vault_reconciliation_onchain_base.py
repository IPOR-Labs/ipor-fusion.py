"""totalAssets formulas on live BASE vaults (read-only, pinned block).

PlasmaVault contracts before ipor-fusion #475 (Feb 2026) return gross assets
minus the unrealized management fee; later ones return gross, and both
generations add the RewardsClaimManager's vested balance. At block 52945233
Harvest WETH vault Base runs the netting contract (its gross delta equals the
unrealized fee) and Base ETH Lending Optimizer the gross one with a vested
rewards balance. Both must reconcile to a zero delta.

Skipped unless an archive BASE RPC is configured via ``BASE_PROVIDER_URL``.
"""

from __future__ import annotations

from _live import archive_context
from web3 import Web3

from ipor_fusion import validate_vault
from ipor_fusion.vault_data import fetch_vault_data

BLOCK = 52945233
NETTING_VAULT = Web3.to_checksum_address("0x7872893e528fe2c0829e405960db5b742112aa97")
GROSS_VAULT = Web3.to_checksum_address("0x17d0f109ee895bad0b68aa104aa72bd0b003ad8e")


def _fetch(vault):
    ctx = archive_context("BASE_PROVIDER_URL", BLOCK, vault)
    data = fetch_vault_data(ctx, vault, block=BLOCK, lending=False, metadata=False)
    return data, validate_vault(ctx, vault, block=BLOCK)


def test_a_vault_netting_the_management_fee_reconciles():
    data, validation = _fetch(NETTING_VAULT)
    recon = validation.reconciliation
    fee = data.unrealized_management_fee
    assert fee is not None and fee > 0
    assert recon.management_fee_netted == fee
    assert data.stored_markets_total
    assert recon.implied_market_total == data.stored_markets_total
    assert recon.delta == 0
    assert "market_storage_drift" not in {f.check for f in validation.findings}


def test_a_gross_vault_with_vested_rewards_reconciles():
    data, validation = _fetch(GROSS_VAULT)
    recon = validation.reconciliation
    assert data.unrealized_management_fee
    assert data.rewards_manager_balance
    assert recon.management_fee_netted == 0
    assert recon.rewards_manager_balance == data.rewards_manager_balance
    assert recon.delta == 0

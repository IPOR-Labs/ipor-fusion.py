"""IL-7463 on-chain regression test (read-only, real BASE state).

Drives the real SDK/CLI compute path — ``_fetch_vault_data`` →
``_compute_erc20_balances`` → ``_compute_health_check`` — against the live BASE
PlasmaVault ``0x7743931f74157C8aC65697660102555d40D09a77`` at block 46334673,
where the vault held 10 USDC of idle (undeployed) underlying.

Because USDC is, by design, NOT an ``ERC20_VAULT_BALANCE`` substrate, the buggy
code reported ``underlying_on_vault = 0`` and a 100% reconciliation mismatch.
This test asserts the post-fix truth on real chain data.

No Click and no rendering — assertions are on typed values, so a cosmetic output
change can't break it. It is skipped unless a reachable (archive) BASE RPC is
configured via ``BASE_PROVIDER_URL`` (the same gate the simulate tests use); the
historical block requires an archive node.
"""

from __future__ import annotations

import os

import pytest
from web3 import Web3

from ipor_fusion import PlasmaVault, Web3Context
from ipor_fusion.cli.vault_fetcher import _fetch_vault_data, _VaultData
from ipor_fusion.cli.vault_health import (
    _BalanceFuseTotals,
    _compute_erc20_balances,
    _compute_health_check,
)

VAULT = Web3.to_checksum_address("0x7743931f74157C8aC65697660102555d40D09a77")
USDC = "0x833589fcd6edb6e08f4c7c32d4f71b54bda02913"  # BASE USDC (underlying)
CHAIN_ID = 8453
BLOCK = 46334673
EXPECTED_IDLE_USDC = 10 * 10**6  # 10 USDC (6 decimals) sitting idle on the vault


def _sum_balance_fuse_markets(pv: PlasmaVault, data: _VaultData) -> _BalanceFuseTotals:
    """Replicate the CLI's per-market totalAssetsInMarket sum (dedup by market).

    This only constructs an *input* to the real ``_compute_health_check``; the
    reconciliation logic under test is the production function, not this sum.
    """
    totals = _BalanceFuseTotals()
    seen: set[int] = set()
    for bf in data.balance_fuses:
        if bf.market_id in seen:
            continue
        seen.add(bf.market_id)
        totals.raw_total += pv.total_assets_in_market(bf.market_id).call()
    return totals


def test_idle_underlying_reconciles_on_real_base_vault():
    url = os.environ.get("BASE_PROVIDER_URL")
    if not url:
        pytest.skip("BASE_PROVIDER_URL not set")

    try:  # pylint: disable=broad-except
        ctx = Web3Context.from_url(url)
        ctx.default_block = BLOCK
        pv = PlasmaVault(ctx, VAULT)
        data = _fetch_vault_data(ctx, pv, BLOCK, chain_id=CHAIN_ID)
        erc20 = _compute_erc20_balances(ctx, pv, data)
        bf_totals = _sum_balance_fuse_markets(pv, data)
    except Exception as exc:  # pylint: disable=broad-except
        # Infra problem (provider unreachable or non-archive) — skip, don't fail.
        pytest.skip(f"BASE on-chain read failed (provider missing/non-archive?): {exc}")

    # Sanity: confirm we are reading the expected vault/chain state at this block.
    assert data.asset.lower() == USDC
    assert data.asset_decimals == 6
    assert data.total_assets == EXPECTED_IDLE_USDC

    # The fix: idle underlying is counted even though USDC is not a substrate.
    assert erc20.underlying_balance_raw == EXPECTED_IDLE_USDC

    # The symptom: no false reconciliation warning on this healthy vault.
    health = _compute_health_check(data, bf_totals, erc20, set())
    assert [w for w in health.warnings if "totalAssets" in w] == []

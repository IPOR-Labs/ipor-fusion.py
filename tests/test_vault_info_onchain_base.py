"""Idle-underlying on-chain regression test (read-only, real BASE state).

Drives the public ``validate_vault`` (fetch, ERC20 holdings, reconciliation,
findings) against the live BASE
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

from collections import Counter
from unittest.mock import patch

from _live import archive_context, provider_url
from web3 import Web3
from web3.providers.rpc import HTTPProvider

from ipor_fusion import Web3Context, validate_vault
from ipor_fusion.vault_data import fetch_vault_data

VAULT = Web3.to_checksum_address("0x7743931f74157C8aC65697660102555d40D09a77")
CHAIN_ID = 8453
BLOCK = 46334673
EXPECTED_IDLE_USDC = 10 * 10**6  # 10 USDC (6 decimals) sitting idle on the vault


def test_idle_underlying_reconciles_on_real_base_vault():
    ctx = archive_context("BASE_PROVIDER_URL", BLOCK)
    validation = validate_vault(ctx, VAULT, block=BLOCK)

    # Sanity: confirm we are reading the expected vault/chain state at this block.
    recon = validation.reconciliation
    assert (validation.chain_id, validation.block_number) == (CHAIN_ID, BLOCK)
    assert recon.on_chain_total_assets == EXPECTED_IDLE_USDC

    # The fix: idle underlying is counted even though USDC is not a substrate.
    assert recon.underlying_on_vault == EXPECTED_IDLE_USDC

    # The symptom: no false reconciliation warning on this healthy vault.
    assert [w for w in validation.warnings if "totalAssets" in w] == []


# Batched reads keep the fetch at a handful of round trips per pipeline. The
# per-call fan-out this replaced issued ~50 eth_calls here, each wrapped in two
# eth_chainId lookups by web3's validation middleware.
MAX_ETH_CALLS = 20


def test_fetch_stays_within_round_trip_budget():
    archive_context("BASE_PROVIDER_URL", BLOCK)  # skips without an archive provider
    url = provider_url("BASE_PROVIDER_URL")

    methods: Counter[str] = Counter()
    make_request = HTTPProvider.make_request

    def counting(provider, method, params):
        methods[method] += 1
        return make_request(provider, method, params)

    # web3 binds make_request when the provider is built, so the context
    # must be created under the patch for any request to be counted.
    with patch.object(HTTPProvider, "make_request", counting):
        ctx = Web3Context.from_url(url)
        methods.clear()
        data = fetch_vault_data(ctx, VAULT, block=BLOCK)

    assert data.total_assets == EXPECTED_IDLE_USDC
    assert methods["eth_call"] > 0, "requests were not counted"
    assert methods["eth_chainId"] == 0, methods
    assert methods["eth_call"] <= MAX_ETH_CALLS, methods

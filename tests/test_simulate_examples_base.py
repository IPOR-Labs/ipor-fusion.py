"""RPC-gated end-to-end check that the examples/ simulation paths still run.

Auto-marked `sdk` by conftest; skips cleanly when BASE_PROVIDER_URL is unset or
the provider lacks eth_simulateV1. The offline builders are covered separately
by test_examples_vaults.py.
"""

from __future__ import annotations


def test_simple_aave_v3_supply_base_simulation(web3_base, load_example):
    mod = load_example("simple_aave_v3_supply_base.py")
    result = mod.run_simulation(web3_base)
    # run_simulation already asserts all_success and every outcome; here just
    # confirm the batch actually executed on-chain work.
    assert result.gas_used > 0

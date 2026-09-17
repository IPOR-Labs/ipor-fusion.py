"""RPC-gated end-to-end check that the examples/ simulation paths still run.

Auto-marked `sdk` by conftest; skips cleanly when BASE_PROVIDER_URL is unset or
the provider lacks eth_simulateV1. The offline builders are covered separately
by test_examples_vaults.py.
"""

from __future__ import annotations

from eth_abi.abi import decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3


def test_simple_aave_v3_supply_base_simulation(web3_base, load_example):
    mod = load_example("simple_aave_v3_supply_base.py")
    result = mod.run_simulation(web3_base)
    # run_simulation already asserts all_success and every outcome; here just
    # confirm the batch actually executed on-chain work.
    assert result.gas_used > 0


def test_advanced_euler_v2_credit_market_base_simulation(web3_base, load_example):
    mod = load_example("advanced_euler_v2_credit_market_base.py")

    # Provenance for the two eVaults (not in the IPOR registry): assert each
    # eVault's underlying is what the example claims. This closes the provenance
    # loop the offline test cannot -- it needs a chain read.
    asset_selector = function_signature_to_4byte_selector("asset()")

    def evault_asset(evault: str) -> str:
        raw = web3_base.eth.call(
            {"to": evault, "data": asset_selector}, block_identifier=mod.PINNED_BLOCK
        )
        (addr,) = decode(["address"], raw)
        return Web3.to_checksum_address(addr)

    assert evault_asset(mod.EVAULT_CBETH) == mod.BASE_CBETH
    assert evault_asset(mod.EVAULT_WETH) == mod.BASE_WETH

    result = mod.run_simulation(web3_base)
    # run_simulation already asserts all_success and every outcome; here just
    # confirm the batch actually executed on-chain work.
    assert result.gas_used > 0


def test_external_state_margin_leg_base_simulation(web3_base, load_example):
    mod = load_example("external_state_margin_leg_base.py")
    result = mod.run_simulation(web3_base)
    assert result.gas_used > 0
    # This example ends on a deliberate revert, so `result.all_success` is False
    # on a successful run and the sibling checks above do not apply. run_simulation
    # already enforces what follows and raises if it does not hold, so these lines
    # cannot fail on their own; they are here so that weakening the example's own
    # guard -- letting a second call fail, or accepting any revert reason --
    # still fails CI.
    assert [c.label for c in result.failed_calls] == ["demo_confirm"]
    revert_data = bytes(result.failed_calls[0].return_data)
    assert revert_data[:4] == mod.PROPOSAL_HASH_MISMATCH_SELECTOR

"""RPC-gated proof for the crosschain CCIP lifecycle example (Arbitrum hub,
HyperEVM spoke). General PR CI has no HyperEVM secret, so this runs locally
and wherever ``HYPEREVM_PROVIDER_URL`` is set."""

from __future__ import annotations

from ipor_fusion import discover_deployment
from ipor_fusion.crosschain.transport import CrosschainTransportKind


def test_crosschain_ccip_usdc_arbitrum_hyperevm_simulation(
    web3_arb, web3_hyperevm, load_example
):
    mod = load_example("crosschain_ccip_usdc_arbitrum_hyperevm.py")

    # The addresses the example hard-codes are what the hub vault's grants say.
    hub_ctx = mod._pinned_context(
        web3_arb, mod.ARBITRUM_CHAIN_ID, mod.ARBITRUM_PINNED_BLOCK
    )
    deployment = discover_deployment(hub_ctx, mod.HUB_VAULT, mod.CROSSCHAIN_MARKET)
    ccip = deployment.executor(CrosschainTransportKind.CHAINLINK_CCIP)
    assert ccip.address == mod.EXECUTOR
    assert (ccip.balance_proposer, ccip.balance_approver) == (
        mod.BALANCE_PROPOSER,
        mod.BALANCE_APPROVER,
    )
    assert deployment.remote_vaults[mod.HYPEREVM_CHAIN_ID] == (mod.SPOKE_VAULT,)

    numbers = mod.run_simulation(web3_arb, web3_hyperevm)

    assert numbers["sent"] == mod.DEPOSIT_AMOUNT
    assert 0 < numbers["credited"] <= numbers["sent"]
    assert numbers["spoke_shares"] > 0
    assert 0 < numbers["remote_idle"] <= numbers["credited"]
    assert numbers["attested_initial"] == numbers["credited"]
    assert numbers["attested_redeemed"] == numbers["remote_idle"]
    assert numbers["attested_residue"] == 0
    assert numbers["settled_after_return"] == 0
    assert 0 < numbers["idle"] <= numbers["remote_idle"]
    assert numbers["round_trip_cost"] == numbers["sent"] - numbers["idle"]
    assert (
        numbers["vault_usdc_after"]
        == numbers["vault_usdc_after_deposit"] - numbers["round_trip_cost"]
    )

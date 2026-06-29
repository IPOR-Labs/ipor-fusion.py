"""EulerSwap v2 LP-pool registry register/unregister on BASE via `eth_simulateV1`.

Mirrors the Solidity `EulerV2SwapRegistryForkTest.testShouldRegisterAndUnregisterPoolInLiveRegistry`:
deploy a pool (shared `queue_setup`), then drive the new `EulerV2SwapRegistryFuse`
through real `PlasmaVault.execute(...)` calls as alpha:

  1. register   — `EulerV2SwapRegistryFuse.register(...)` registers the pool in the
     public registry (zero validity bond).
  2. decommission — `EulerV2SwapDeployFuse.decommission(...)` removes the pool's EVC
     account operator. The live registry reverts `unregisterPool` while the operator
     is still installed, so this must come first.
  3. unregister — `EulerV2SwapRegistryFuse.unregister(...)` clears the registration.

Assertions read the registry's `poolByEulerAccount(eulerAccount)` straight from the
live EulerSwap registry on Base.
"""

from __future__ import annotations

import logging

from _euler_v2 import (
    BASE_FUSION_FACTORY,
    EULER_SWAP_DEPLOY_FUSE,
    EULER_SWAP_REGISTRY_FUSE,
    OWNER,
    SUB_ACCOUNT,
    clone_args,
    queue_setup,
    registry_pool_by_euler_account,
)
from _simulate import assert_all_success
from constants import ANVIL_WALLET

from ipor_fusion import VaultSimulator, Web3Context
from ipor_fusion.core.fusion_factory import FusionFactory
from ipor_fusion.fuses import EulerV2SwapDeployFuse, EulerV2SwapRegistryFuse
from ipor_fusion.types import ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

ZERO_ADDRESS_LOWER = "0x0000000000000000000000000000000000000000"


def test_simulate_euler_v2_swap_registry_base(web3_base):
    # Pin the latest block for the run so every read/sim call sees one consistent state.
    block = web3_base.eth.block_number
    ctx = Web3Context(web3=web3_base, chain_id=ChainId(8453), signer=OWNER)
    ctx.default_block = block
    factory = FusionFactory(ctx, BASE_FUSION_FACTORY)

    # Predict the deterministic clone addresses.
    preview = factory.clone(**clone_args()).call()
    vault_address = preview.plasma_vault
    access_manager_address = preview.access_manager

    sim = VaultSimulator(
        web3=web3_base, vault=vault_address, alpha=ANVIL_WALLET, block=hex(block)
    )
    plan = queue_setup(
        sim, ctx, factory, vault_address, access_manager_address, web3_base, block
    )
    deploy_fuse = EulerV2SwapDeployFuse(EULER_SWAP_DEPLOY_FUSE)
    registry_fuse = EulerV2SwapRegistryFuse(EULER_SWAP_REGISTRY_FUSE)

    # Deploy the pool (supply collateral + deploy).
    sim.execute(plan.supply_actions + [plan.deploy_action])
    sim.observe(
        "registered_before",
        registry_pool_by_euler_account(ctx, plan.euler_account),
    )

    # Register the pool in the public registry.
    sim.execute(
        [registry_fuse.register(pool=plan.predicted_pool, sub_account=SUB_ACCOUNT)]
    )
    sim.observe(
        "registered_after",
        registry_pool_by_euler_account(ctx, plan.euler_account),
    )

    # Remove the EVC operator first (the registry blocks unregister otherwise), then unregister.
    sim.execute(
        [deploy_fuse.decommission(pool=plan.predicted_pool, sub_account=SUB_ACCOUNT)]
    )
    sim.execute(
        [registry_fuse.unregister(pool=plan.predicted_pool, sub_account=SUB_ACCOUNT)]
    )
    sim.observe(
        "registered_after_unregister",
        registry_pool_by_euler_account(ctx, plan.euler_account),
    )

    result = sim.run()
    log.info(
        "all_success=%s gas=%s reason=%s obs=%s",
        result.all_success,
        result.gas_used,
        result.revert_reason,
        result.observations,
    )
    assert_all_success(result)

    # No registration before; pool registered after `register`; cleared after `unregister`.
    assert result.get("registered_before").lower() == ZERO_ADDRESS_LOWER
    assert result.get("registered_after").lower() == plan.predicted_pool.lower()
    assert result.get("registered_after_unregister").lower() == ZERO_ADDRESS_LOWER

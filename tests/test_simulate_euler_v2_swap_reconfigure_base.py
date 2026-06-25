"""EulerSwap v2 LP-pool reconfigure on BASE via `eth_simulateV1`.

Mirrors the Solidity `EulerV2SwapForkTest.testShouldReconfigurePoolAndReflectNewDynamicParams`:
deploy a pool (shared `queue_setup`), then drive the new
`EulerV2SwapReconfigureFuse` through a real `PlasmaVault.execute(...)` as alpha
and assert the pool's on-chain `getDynamicParams()` reflects the new fees and
equilibrium reserves.
"""

from __future__ import annotations

import logging

from _euler_v2 import (
    BASE_FUSION_FACTORY,
    EULER_SWAP_RECONFIGURE_FUSE,
    OWNER,
    SUB_ACCOUNT,
    clone_args,
    dynamic_params,
    initial_state,
    pool_dynamic_params,
    queue_setup,
)
from _simulate import assert_all_success
from constants import ANVIL_WALLET
from ipor_fusion import Web3Context, VaultSimulator
from ipor_fusion.core.fusion_factory import FusionFactory
from ipor_fusion.fuses import EulerV2SwapReconfigureFuse
from ipor_fusion.types import ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# DynamicParams tuple indices (see IEulerV2Swap.DynamicParams field order).
_EQUILIBRIUM_RESERVE0 = 0
_FEE0 = 8
_FEE1 = 9

_OLD_FEE = 3 * 10**15  # 0.3% — queue_setup's deploy default
_NEW_FEE = 5 * 10**15  # 0.5%
_NEW_EQUILIBRIUM_RESERVE = 6 * 10**18


def test_simulate_euler_v2_swap_reconfigure_base(web3_base):
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

    # Deploy the pool first (supply collateral + deploy).
    sim.execute(plan.supply_actions + [plan.deploy_action])
    sim.observe("dynamic_before", pool_dynamic_params(ctx, plan.predicted_pool))

    # Reconfigure: bump both fees to 0.5% and raise the equilibrium reserves.
    reconfigure_fuse = EulerV2SwapReconfigureFuse(EULER_SWAP_RECONFIGURE_FUSE)
    reconfigure_action = reconfigure_fuse.reconfigure(
        pool=plan.predicted_pool,
        sub_account=SUB_ACCOUNT,
        dynamic_params=dynamic_params(
            fee=_NEW_FEE, equilibrium_reserve=_NEW_EQUILIBRIUM_RESERVE
        ),
        initial_state=initial_state(reserve=_NEW_EQUILIBRIUM_RESERVE),
    )
    sim.execute([reconfigure_action])
    sim.observe("dynamic_after", pool_dynamic_params(ctx, plan.predicted_pool))

    result = sim.run()
    log.info(
        "all_success=%s gas=%s reason=%s obs=%s",
        result.all_success,
        result.gas_used,
        result.revert_reason,
        result.observations,
    )
    assert_all_success(result)

    before = result.get("dynamic_before")
    after = result.get("dynamic_after")
    # The deploy set fee 0.3%; reconfigure must move it to 0.5% on both sides and
    # apply the new equilibrium reserve — read straight from the live pool.
    assert before[_FEE0] == _OLD_FEE
    assert after[_FEE0] == _NEW_FEE
    assert after[_FEE1] == _NEW_FEE
    assert after[_EQUILIBRIUM_RESERVE0] == _NEW_EQUILIBRIUM_RESERVE

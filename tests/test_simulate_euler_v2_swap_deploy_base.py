"""EulerSwap v2 LP-pool deploy/decommission on BASE via `eth_simulateV1`.

Mirrors the Solidity `EulerV2SwapForkTest` (deploy + exit paths). The flow:
clone a fresh PlasmaVault (WETH underlying), wire the EULER_V2 market (supply +
deploy fuses, balance fuse, eVault substrates), then drive the new
`EulerV2SwapDeployFuse` through a real `PlasmaVault.execute(...)` as alpha:

  1. deploy  — `EulerV2SwapDeployFuse.deploy(...)` installs the pool as the EVC
     account operator and CREATE2-deploys it via the EulerSwap factory.
  2. exit    — `EulerV2SwapDeployFuse.decommission(...)` removes that operator.

`enter` requires a `predicted_pool` equal to `factory.computePoolAddress(...)`
whose low 14 address bits == 0x28A8 (the Uniswap-v4 hook-flag constraint baked
into EulerSwap v2). That salt is mined off-chain (see `_euler_v2.mine_salt`).
"""

from __future__ import annotations

import logging

from _euler_v2 import (
    BASE_FUSION_FACTORY,
    EULER_SWAP_DEPLOY_FUSE,
    OWNER,
    SUB_ACCOUNT,
    evc_is_operator_authorized,
    factory_deployed_pools,
    clone_args,
    queue_setup,
)
from _simulate import assert_all_success
from constants import ANVIL_WALLET
from ipor_fusion import Web3Context, VaultSimulator
from ipor_fusion.core.fusion_factory import FusionFactory
from ipor_fusion.fuses import EulerV2SwapDeployFuse
from ipor_fusion.types import ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)


def test_simulate_euler_v2_swap_deploy_base(web3_base):
    # Pin the latest block for the run so every read/sim call sees one consistent
    # state (whale balances, factory + eVault state).
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
    exit_action = deploy_fuse.decommission(
        pool=plan.predicted_pool, sub_account=SUB_ACCOUNT
    )

    # Deploy the pool (supply collateral then deploy, alpha-driven execute).
    sim.observe(
        "operator_before",
        evc_is_operator_authorized(ctx, plan.euler_account, plan.predicted_pool),
    )
    sim.execute(plan.supply_actions + [plan.deploy_action])
    sim.observe(
        "operator_after_deploy",
        evc_is_operator_authorized(ctx, plan.euler_account, plan.predicted_pool),
    )
    sim.observe("deployed_pools", factory_deployed_pools(ctx, plan.predicted_pool))

    # Decommission the pool (remove operator).
    sim.execute([exit_action])
    sim.observe(
        "operator_after_exit",
        evc_is_operator_authorized(ctx, plan.euler_account, plan.predicted_pool),
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

    # enter: pool deployed by the factory and installed as the EVC account operator.
    assert result.get("operator_before") is False
    assert result.get("operator_after_deploy") is True
    assert result.get("deployed_pools") is True
    # exit: operator authorization removed (pool decommissioned).
    assert result.get("operator_after_exit") is False

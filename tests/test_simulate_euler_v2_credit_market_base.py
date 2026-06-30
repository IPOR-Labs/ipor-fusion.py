"""EulerV2 collateral / controller / borrow fuses on BASE via `eth_simulateV1`.

Mirrors the Solidity `EulerV2CreditMarket.t.sol` credit-market lifecycle, driving
the three dedicated fuses through real `PlasmaVault.execute(...)` calls as alpha:

  1. supply cbETH collateral (shared `queue_setup`)
  2. EulerV2CollateralFuse.enable_collateral(ecbETH)
  3. EulerV2ControllerFuse.enable_controller(eWETH)
  4. EulerV2BorrowFuse.borrow(eWETH)         → debt > 0
  5. EulerV2BorrowFuse.repay(eWETH, all)     → debt == 0
  6. EulerV2ControllerFuse.disable_controller(eWETH)
  7. EulerV2CollateralFuse.disable_collateral(ecbETH)

Debt is read straight from the live eWETH eVault's `debtOf(eulerAccount)`.
"""

from __future__ import annotations

import logging

from _euler_v2 import (
    BASE_FUSION_FACTORY,
    EULER_BORROW_FUSE,
    EULER_COLLATERAL_FUSE,
    EULER_CONTROLLER_FUSE,
    EVAULT_CBETH,
    EVAULT_WETH,
    OWNER,
    SUB_ACCOUNT,
    clone_args,
    evault_debt_of,
    queue_setup,
)
from _simulate import assert_all_success
from constants import ANVIL_WALLET

from ipor_fusion import VaultSimulator, Web3Context
from ipor_fusion.core.fusion_factory import FusionFactory
from ipor_fusion.fuses import (
    EulerV2BorrowFuse,
    EulerV2CollateralFuse,
    EulerV2ControllerFuse,
)
from ipor_fusion.types import MAX_UINT256, Amount, ChainId

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

BORROW_AMOUNT = Amount(1 * 10**18)  # 1 WETH against ~10 cbETH collateral (92% LTV)


def test_simulate_euler_v2_credit_market_base(web3_base):
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

    collateral_fuse = EulerV2CollateralFuse(EULER_COLLATERAL_FUSE)
    controller_fuse = EulerV2ControllerFuse(EULER_CONTROLLER_FUSE)
    borrow_fuse = EulerV2BorrowFuse(EULER_BORROW_FUSE)

    # Supply cbETH collateral only (plan.supply_actions[0]); WETH is borrowed below.
    sim.execute([plan.supply_actions[0]])

    # Enable cbETH as collateral and eWETH as the borrow controller.
    sim.execute(
        [
            collateral_fuse.enable_collateral(
                euler_vault=EVAULT_CBETH, sub_account=SUB_ACCOUNT
            ),
            controller_fuse.enable_controller(
                euler_vault=EVAULT_WETH, sub_account=SUB_ACCOUNT
            ),
        ]
    )

    # Borrow WETH against the cbETH collateral.
    sim.execute(
        [
            borrow_fuse.borrow(
                euler_vault=EVAULT_WETH,
                asset_amount=BORROW_AMOUNT,
                sub_account=SUB_ACCOUNT,
            )
        ]
    )
    sim.observe(
        "debt_after_borrow", evault_debt_of(ctx, EVAULT_WETH, plan.euler_account)
    )

    # Repay the full debt.
    sim.execute(
        [
            borrow_fuse.repay(
                euler_vault=EVAULT_WETH,
                max_asset_amount=MAX_UINT256,
                sub_account=SUB_ACCOUNT,
            )
        ]
    )
    sim.observe(
        "debt_after_repay", evault_debt_of(ctx, EVAULT_WETH, plan.euler_account)
    )

    # Tear down the credit position (controller first, then collateral).
    sim.execute(
        [
            controller_fuse.disable_controller(
                euler_vault=EVAULT_WETH, sub_account=SUB_ACCOUNT
            ),
            collateral_fuse.disable_collateral(
                euler_vault=EVAULT_CBETH, sub_account=SUB_ACCOUNT
            ),
        ]
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

    # borrow opened debt; repay cleared it.
    assert result.get("debt_after_borrow") >= BORROW_AMOUNT
    assert result.get("debt_after_repay") == 0

"""EulerV2BatchFuse on BASE via `eth_simulateV1`.

Mirrors the spirit of the Solidity `EulerV2Batch.testShouldPassExampleFromEulerDocs`
(a multi-op EVC batch executed atomically through `PlasmaVault.execute(...)`), using
a self-repaying flash-loan batch on the live Base eWETH eVault:

    enableController → borrow → repay(all) → disableController

EVC liquidity checks are deferred to the end of the batch, so the borrow nets to
zero debt before the check runs — no collateral or external funding required. The
docs example's `onEulerFlashLoan` callback item is omitted (it needs a deployable
no-op fuse that does not exist on a fork); every other supported op is exercised.
"""

from __future__ import annotations

import logging

from _euler_v2 import (
    BASE_FUSION_FACTORY,
    EULER_BALANCE_FUSE,
    EULER_BATCH_FUSE,
    EULER_MARKET,
    EULER_V2_EVC,
    EVAULT_WETH,
    OWNER,
    clone_args,
    euler_account,
    euler_substrate,
)
from _simulate import assert_all_success
from addresses import BASE_WETH
from constants import ANVIL_WALLET
from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion import (
    ERC20,
    AccessManager,
    PlasmaVault,
    Roles,
    VaultSimulator,
    Web3Context,
)
from ipor_fusion.core.fusion_factory import FusionFactory
from ipor_fusion.fuses import EulerV2BatchFuse, EulerV2BatchItem
from ipor_fusion.types import MAX_UINT256, Amount, ChainId, Period

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

# Sub-account 0x00 → eulerAccount == the vault itself.
BATCH_SUB_ACCOUNT = 0x00
BORROW_AMOUNT = Amount(1 * 10**18)  # 1 WETH, flash-borrowed and repaid in-batch


def _enable_controller(account: ChecksumAddress, vault: ChecksumAddress) -> bytes:
    return function_signature_to_4byte_selector(
        "enableController(address,address)"
    ) + encode(["address", "address"], [account, vault])


def _borrow(amount: int, receiver: ChecksumAddress) -> bytes:
    return function_signature_to_4byte_selector("borrow(uint256,address)") + encode(
        ["uint256", "address"], [amount, receiver]
    )


def _repay(amount: int, receiver: ChecksumAddress) -> bytes:
    return function_signature_to_4byte_selector("repay(uint256,address)") + encode(
        ["uint256", "address"], [amount, receiver]
    )


def _disable_controller() -> bytes:
    return function_signature_to_4byte_selector("disableController()")


def test_simulate_euler_v2_batch_base(web3_base):
    # Pin the latest block for the run so every read/sim call sees one consistent state.
    block = web3_base.eth.block_number
    ctx = Web3Context(web3=web3_base, chain_id=ChainId(8453), signer=OWNER)
    ctx.default_block = block
    factory = FusionFactory(ctx, BASE_FUSION_FACTORY)

    # Predict the deterministic clone addresses.
    preview = factory.clone(**clone_args()).call()
    vault_address = preview.plasma_vault
    access_manager_address = preview.access_manager
    account = euler_account(vault_address, BATCH_SUB_ACCOUNT)  # == vault_address

    plasma_vault = PlasmaVault(ctx, vault_address)
    access_manager = AccessManager(ctx, access_manager_address)
    batch_fuse = EulerV2BatchFuse(EULER_BATCH_FUSE)
    weth = ERC20(ctx, BASE_WETH)

    # Self-repaying flash-loan batch on the eWETH eVault.
    batch_action = batch_fuse.batch(
        items=[
            EulerV2BatchItem(
                target_contract=EULER_V2_EVC,
                on_behalf_of_account=BATCH_SUB_ACCOUNT,
                data=_enable_controller(account, EVAULT_WETH),
            ),
            EulerV2BatchItem(
                target_contract=EVAULT_WETH,
                on_behalf_of_account=BATCH_SUB_ACCOUNT,
                data=_borrow(BORROW_AMOUNT, account),
            ),
            EulerV2BatchItem(
                target_contract=EVAULT_WETH,
                on_behalf_of_account=BATCH_SUB_ACCOUNT,
                data=_repay(MAX_UINT256, account),
            ),
            EulerV2BatchItem(
                target_contract=EVAULT_WETH,
                on_behalf_of_account=BATCH_SUB_ACCOUNT,
                data=_disable_controller(),
            ),
        ],
        assets_for_approvals=[BASE_WETH],
        euler_vaults_for_approvals=[EVAULT_WETH],
    )

    sim = VaultSimulator(
        web3=web3_base, vault=vault_address, alpha=ANVIL_WALLET, block=hex(block)
    )

    # Create the vault FIRST so the clone index matches the preview.
    sim.add_call(call=factory.clone(**clone_args()), from_=OWNER, label="clone")

    no_delay = Period(0)
    sim.add_call(
        call=access_manager.grant_role(Roles.ATOMIST_ROLE, OWNER, no_delay), from_=OWNER
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.FUSE_MANAGER_ROLE, OWNER, no_delay),
        from_=OWNER,
    )
    sim.add_call(
        call=access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, no_delay),
        from_=OWNER,
    )

    # Wire the EULER_V2 market: batch fuse, balance fuse, eWETH substrate (sub-account 0).
    sim.add_call(call=plasma_vault.add_fuses([EULER_BATCH_FUSE]), from_=OWNER)
    sim.add_call(
        call=plasma_vault.add_balance_fuse(EULER_MARKET, EULER_BALANCE_FUSE),
        from_=OWNER,
    )
    sim.add_call(
        call=plasma_vault.grant_market_substrates(
            EULER_MARKET,
            [
                euler_substrate(
                    euler_vault=EVAULT_WETH,
                    is_collateral=True,
                    can_borrow=True,
                    sub_account=BATCH_SUB_ACCOUNT,
                )
            ],
        ),
        from_=OWNER,
    )

    sim.observe("weth_before", weth.balance_of(vault_address))
    sim.execute([batch_action])
    sim.observe("weth_after", weth.balance_of(vault_address))

    result = sim.run()
    log.info(
        "all_success=%s gas=%s reason=%s obs=%s",
        result.all_success,
        result.gas_used,
        result.revert_reason,
        result.observations,
    )
    assert_all_success(result)

    # The flash-borrowed WETH is repaid within the batch — the vault nets to zero.
    assert result.get("weth_before") == 0
    assert result.get("weth_after") == 0

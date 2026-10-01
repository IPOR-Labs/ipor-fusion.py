"""On Base: what `create_executor` and `sync_substrates` do, not how they encode.

An attestation-only vault -- custodians and a balance account, no asset and no
target -- creates its executor with `create_executor`, so the custodians it
granted beforehand can propose and confirm at once. The executor's cache then
lags the vault both ways until `sync_substrates` runs: a custodian granted
later is refused, and a custodian revoked later is still accepted.

Addresses, actors and the pinned block come from the Base external-state
example, which simulates the full enter/attest/exit lifecycle on the same
deployment; this test covers the path that never enters.
"""

from __future__ import annotations

from eth_abi.abi import decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.utils.address import get_create_address

from ipor_fusion import (
    AccessManager,
    ExternalStateExecutor,
    ExternalStateSubstrates,
    PlasmaVault,
    Roles,
    VaultSimulator,
    Web3Context,
)
from ipor_fusion.core import FusionFactory
from ipor_fusion.fuses import ExternalStateOperationFuse
from ipor_fusion.types import Amount, Period

CUSTODIAN_C = Web3.to_checksum_address("0x9965507D1a55bcC2695C58ba16FB37d819B0A4dc")
# Any address outside the vault's roles and substrates: the sync is permissionless.
BYSTANDER = Web3.to_checksum_address("0x976EA74026E726554dB657fA54763abd0C3a0aa9")

ATTESTED_VALUE = Amount(250_000_000)
LATER_VALUE = Amount(260_000_000)
REVOKED_VALUE = Amount(270_000_000)
# The whole batch is one block; pinning its time makes the proposal hash
# computable before the batch runs.
BLOCK_TIME_SHIFT = Period.MINUTE

UNAUTHORIZED_CUSTODIAN_SELECTOR = function_signature_to_4byte_selector(
    "ExternalStateExecutorUnauthorizedCustodian(address)"
)


def _substrates(mod, *custodians: str) -> list[bytes]:
    """The attestation-only grant: one balance account, the custodians and the
    two mandatory guards. No ASSET or TARGET, so no `enter` could run -- the
    setup `create_executor` exists for."""
    return [
        ExternalStateSubstrates.balance_account(mod.VENUE_BALANCE_ACCOUNT),
        *(ExternalStateSubstrates.custodian(c) for c in custodians),
        ExternalStateSubstrates.staleness_max(mod.STALENESS_MAX),
        ExternalStateSubstrates.big_change_bps(mod.BIG_CHANGE_BPS),
    ]


def _assert_unauthorized_custodian(revert_data: bytes, custodian: str) -> None:
    assert revert_data[:4] == UNAUTHORIZED_CUSTODIAN_SELECTOR
    (caller,) = decode(["address"], revert_data[4:])
    assert Web3.to_checksum_address(caller) == custodian


def test_create_executor_caches_grants_and_sync_refreshes_custodians(
    web3_base, load_example
):
    """The executor address is predicted, not discovered: the create is the
    first contract the fresh vault deploys, and a new contract starts at nonce 1
    (EIP-161), so the executor is CREATE(vault, 1). That spares the discovery
    pass the example needs -- its first executor-creating call comes after
    setup it has to replay -- and the prediction is checked against the vault's
    `ExternalStateExecutorDeployed` log before anything else: a call to an
    address with no code succeeds, so a wrong prediction would otherwise surface
    only as confusing failures further down.
    """
    mod = load_example("external_state_margin_leg_base.py")
    ctx = Web3Context(web3=web3_base, chain_id=mod.BASE_CHAIN_ID, signer=mod.OWNER)
    ctx.default_block = mod.PINNED_BLOCK

    factory = FusionFactory(ctx, mod.BASE_FUSION_FACTORY)
    preview = factory.clone(**mod.clone_args()).call()
    vault = PlasmaVault(ctx, preview.plasma_vault)
    access_manager = AccessManager(ctx, preview.access_manager)
    executor_address = Web3.to_checksum_address(
        get_create_address(preview.plasma_vault, 1)
    )
    executor = ExternalStateExecutor(ctx, executor_address)

    block_time = (
        int(web3_base.eth.get_block(mod.PINNED_BLOCK)["timestamp"]) + BLOCK_TIME_SHIFT
    )
    first_proposal_hash = ExternalStateExecutor.proposal_hash(
        executor=executor_address,
        chain_id=mod.BASE_CHAIN_ID,
        balance_account=mod.VENUE_BALANCE_ACCOUNT,
        value=ATTESTED_VALUE,
        proposer=mod.CUSTODIAN_A,
        proposed_at=block_time,
        nonce=1,
    )

    sim = VaultSimulator(
        web3=web3_base,
        vault=preview.plasma_vault,
        alpha=mod.ALPHA,
        block=mod.PINNED_BLOCK,
    )
    sim.with_block_time_shift(BLOCK_TIME_SHIFT)
    sim.add_call(call=factory.clone(**mod.clone_args()), from_=mod.OWNER)
    for role, account in (
        (Roles.ATOMIST_ROLE, mod.OWNER),
        (Roles.FUSE_MANAGER_ROLE, mod.OWNER),
        (Roles.ALPHA_ROLE, mod.ALPHA),
    ):
        sim.add_call(
            call=access_manager.grant_role(role, account, Period(0)), from_=mod.OWNER
        )
    sim.add_call(
        call=vault.add_fuses([mod.BASE_EXTERNAL_STATE_OPERATION_FUSE]), from_=mod.OWNER
    )
    sim.add_call(
        call=vault.add_balance_fuse(
            mod.EXTERNAL_STATE_MARKET, mod.BASE_EXTERNAL_STATE_BALANCE_FUSE
        ),
        from_=mod.OWNER,
    )
    sim.add_call(
        call=vault.grant_market_substrates(
            mod.EXTERNAL_STATE_MARKET,
            _substrates(mod, mod.CUSTODIAN_A, mod.CUSTODIAN_B),
        ),
        from_=mod.OWNER,
    )

    sim.execute(
        [
            ExternalStateOperationFuse(
                mod.BASE_EXTERNAL_STATE_OPERATION_FUSE
            ).create_executor()
        ]
    )

    # Granted before the create, so already in the executor's cache.
    sim.add_call(
        call=executor.propose_balance(mod.VENUE_BALANCE_ACCOUNT, ATTESTED_VALUE),
        from_=mod.CUSTODIAN_A,
        label="propose_a",
    )
    sim.add_call(
        call=executor.confirm_balance(mod.VENUE_BALANCE_ACCOUNT, first_proposal_hash),
        from_=mod.CUSTODIAN_B,
        label="confirm_b",
    )
    sim.observe("confirmed", executor.balances(mod.VENUE_BALANCE_ACCOUNT))

    # grantMarketSubstrates replaces the whole set, so C joins A and B.
    sim.add_call(
        call=vault.grant_market_substrates(
            mod.EXTERNAL_STATE_MARKET,
            _substrates(mod, mod.CUSTODIAN_A, mod.CUSTODIAN_B, CUSTODIAN_C),
        ),
        from_=mod.OWNER,
    )
    sim.add_call(
        call=executor.propose_balance(mod.VENUE_BALANCE_ACCOUNT, LATER_VALUE),
        from_=CUSTODIAN_C,
        label="propose_c_before_sync",
    )
    sim.add_call(call=executor.sync_substrates(), from_=BYSTANDER, label="sync")
    sim.add_call(
        call=executor.propose_balance(mod.VENUE_BALANCE_ACCOUNT, LATER_VALUE),
        from_=CUSTODIAN_C,
        label="propose_c_after_sync",
    )
    sim.observe("pending_from_c", executor.pending_proposals(mod.VENUE_BALANCE_ACCOUNT))

    # Revoking B: the executor keeps trusting its cache until the next sync.
    sim.add_call(
        call=vault.grant_market_substrates(
            mod.EXTERNAL_STATE_MARKET,
            _substrates(mod, mod.CUSTODIAN_A, CUSTODIAN_C),
        ),
        from_=mod.OWNER,
    )
    sim.add_call(
        call=executor.propose_balance(mod.VENUE_BALANCE_ACCOUNT, REVOKED_VALUE),
        from_=mod.CUSTODIAN_B,
        label="propose_b_before_sync",
    )
    sim.observe("pending_from_b", executor.pending_proposals(mod.VENUE_BALANCE_ACCOUNT))
    sim.add_call(call=executor.sync_substrates(), from_=BYSTANDER, label="resync")
    sim.add_call(
        call=executor.propose_balance(mod.VENUE_BALANCE_ACCOUNT, REVOKED_VALUE),
        from_=mod.CUSTODIAN_B,
        label="propose_b_after_sync",
    )

    result = sim.run()

    assert (
        mod._executor_from_logs(result.execute_logs, preview.plasma_vault)
        == executor_address
    )
    failed = {c.label: bytes(c.return_data) for c in result.failed_calls}
    assert list(failed) == ["propose_c_before_sync", "propose_b_after_sync"]
    _assert_unauthorized_custodian(failed["propose_c_before_sync"], CUSTODIAN_C)
    _assert_unauthorized_custodian(failed["propose_b_after_sync"], mod.CUSTODIAN_B)

    assert result.get("confirmed") == ATTESTED_VALUE
    for label, proposer, value in (
        ("pending_from_c", CUSTODIAN_C, LATER_VALUE),
        ("pending_from_b", mod.CUSTODIAN_B, REVOKED_VALUE),
    ):
        pending = result.get(label)
        assert pending is not None
        assert (pending.proposer, pending.value) == (proposer, value)

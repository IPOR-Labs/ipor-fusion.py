"""CCIP application recovery with real contracts and simulated delivery."""

from __future__ import annotations

from dataclasses import replace

from _crosschain import assert_relay_success
from _crosschain_lifecycle import (
    Run,
    assert_reverted,
    attest,
    call,
    claim,
    redeem_and_recall,
    supply,
)
from web3 import Web3

from ipor_fusion import AccessManager, CcipCommandType, CcipLane, Command, Roles
from ipor_fusion.crosschain.messages import (
    EMPTY_COMMAND,
    CcipMsgType,
    CommandStatus,
    decode_ccip_envelope,
)
from ipor_fusion.fuses.base import FuseAction

UNPRIVILEGED_FLUSHER = Web3.to_checksum_address(
    "0x000000000000000000000000000000000000F105"
)


def _lane(run: Run) -> CcipLane:
    assert isinstance(run.lane, CcipLane)
    return run.lane


def _snapshot(run: Run, tag: str, command_id: bytes = bytes(32)) -> dict:
    lane = _lane(run)
    reads = {
        "active": lane.executor.active_command(run.spoke_chain_id),
        "status": lane.executor.command_status(command_id),
        "sequence": lane.executor.next_command_sequence(run.spoke_chain_id),
        "epoch": lane.executor.command_config_epoch(run.spoke_chain_id),
        "settled": lane.settled_remote_balance(),
        "idle": lane.idle_ledger(),
        "return": lane.executor.active_return(run.spoke_chain_id),
        "version": lane.remote_state_version(),
        "pending_outbound": lane.pending_transfer_count(),
    }
    remote_reads = {
        "remote_status": lane.dispatcher.command_status(command_id),
        "remote_sequence": lane.dispatcher.next_command_sequence(),
        "remote_epoch": lane.dispatcher.command_config_epoch(),
        "observation": lane.dispatcher.observation(),
        "shares": run.remote_vault.balance_of(lane.executor_address),
        "access": run.remote_vault.get_access_manager_address(),
        "head": lane.dispatcher.response_queue_head(),
        "tail": lane.dispatcher.response_queue_tail(),
    }
    for key, read in reads.items():
        run.hub.observe(f"{tag}_{key}", read)
    for key, read in remote_reads.items():
        run.spoke_sim.observe(f"{tag}_{key}", read)
    results = run.relay()
    return {
        **{key: results[run.hub_chain_id].get(f"{tag}_{key}") for key in reads},
        **{
            key: results[run.spoke_chain_id].get(f"{tag}_{key}") for key in remote_reads
        },
    }


def _recovery_action(run: Run, command_id: bytes, *, cancel: bool) -> FuseAction:
    lane = _lane(run)
    send = lane.default_command_send()
    return lane.command_fuse.enter(
        CcipCommandType.CANCEL_COMMAND if cancel else CcipCommandType.RETRY_COMMAND,
        executor=lane.executor_address,
        chain_id=run.spoke_chain_id,
        command=replace(EMPTY_COMMAND, command_id=command_id),
        max_fee=send.max_fee,
        gas_limit=send.gas_limit,
        fee_token=send.fee_token,
    )


def _recover_command(run: Run, command_id: bytes, *, cancel: bool) -> None:
    lane = _lane(run)
    run.hub.execute([_recovery_action(run, command_id, cancel=cancel)])
    if cancel:
        run.hub.add_call(
            run.vault.execute([_recovery_action(run, command_id, cancel=False)]),
            from_=run.owner,
            label="retry_during_cancel",
        )
        run.expected_failures.add("retry_during_cancel")
    else:
        run.hub.observe("retry_pending", lane.executor.command_status(command_id))
    results = run.relay()
    if cancel:
        assert_reverted(
            call(results, run.hub_chain_id, "retry_during_cancel"),
            "CancelInFlight(uint256,bytes32)",
        )
    else:
        assert results[run.hub_chain_id].get("retry_pending") == CommandStatus.PENDING


def _assert_failed_unchanged(before: dict, after: dict, command_id: bytes) -> None:
    assert after["active"] == command_id
    assert after["status"] == after["remote_status"] == CommandStatus.FAILED
    for key in (
        "sequence",
        "remote_sequence",
        "epoch",
        "remote_epoch",
        "shares",
        "settled",
        "version",
    ):
        assert after[key] == before[key], key
    assert after["observation"] == before["observation"]


def run_failed_deposit_recovery(run: Run, *, cancel: bool) -> None:
    """Recover a role failure by retry, or an impossible minimum by cancel."""
    lane = _lane(run)
    credited = supply(run)
    attest(run, tag="initial", observation_label="observation_after_settle")
    before = _snapshot(run, "before_failure")
    delivered_before = len(run.csim.delivered)
    deposit = lane.send_command(
        Command.deposit(
            run.remote_vault.address, credited, min_shares=2**255 if cancel else 0
        )
    )
    run.hub.execute([deposit])
    run.hub.observe(
        "failed_command_id", lane.executor.active_command(run.spoke_chain_id)
    )
    results = run.relay()
    command_id = results[run.hub_chain_id].get("failed_command_id")
    assert command_id != bytes(32)
    assert [
        decode_ccip_envelope(m.payload)[0]
        for m in run.csim.delivered[delivered_before:]
    ] == [
        CcipMsgType.COMMAND,
        CcipMsgType.COMMAND_NACK,
    ]
    failed = _snapshot(run, "failed", command_id)
    _assert_failed_unchanged(before, failed, command_id)

    run.hub.add_call(
        run.vault.execute([deposit]), from_=run.owner, label="blocked_next_command"
    )
    run.expected_failures.add("blocked_next_command")
    results = run.relay()
    assert_reverted(
        call(results, run.hub_chain_id, "blocked_next_command"),
        "ActiveCommandExists(uint256,bytes32)",
    )

    _recover_command(run, command_id, cancel=False)
    repeated = _snapshot(run, "failed_again", command_id)
    _assert_failed_unchanged(before, repeated, command_id)

    run.advance(3600)
    run.spoke_sim.observe("observation_parked", lane.observation())
    run.relay()
    attest(run, tag="parked", observation_label="observation_parked")

    run.advance(1)
    if not cancel:
        run.spoke_sim.add_call(
            AccessManager.encoder(failed["access"]).grant_role(
                Roles.WHITELIST_ROLE, lane.executor_address, 0
            ),
            from_=run.owner,
            label="repair_dispatcher_whitelist",
        )
    _recover_command(run, command_id, cancel=cancel)
    recovered = _snapshot(run, "recovered", command_id)
    status = CommandStatus.CANCELLED if cancel else CommandStatus.SUCCEEDED
    assert recovered["status"] == recovered["remote_status"] == status
    assert recovered["active"] == bytes(32)
    assert (
        recovered["sequence"] == recovered["remote_sequence"] == before["sequence"] + 1
    )
    assert recovered["epoch"] == recovered["remote_epoch"] == before["epoch"]
    assert recovered["version"] == before["version"] + 1
    assert (
        recovered["observation"].state_version
        == before["observation"].state_version + 1
    )
    run.advance(3600)
    run.spoke_sim.observe("observation_recovered", lane.observation())
    run.relay()
    attest(
        run,
        tag="recovered",
        observation_label="observation_recovered",
        old_version=before["version"],
    )
    if cancel:
        assert recovered["shares"] == 0
        assert recovered["observation"].tracked_idle == credited
        run.hub.execute([lane.recall(amount=credited, min_return=credited)])
        run.relay()
        returned = _snapshot(run, "returned", command_id)
        assert returned["idle"] == credited
        assert returned["settled"] == 0
        assert returned["return"] == bytes(32)
        assert returned["observation"].accounted_balance == 0
        claim(run, credited)
    else:
        assert recovered["shares"] > 0
        assert recovered["observation"].tracked_idle == 0
        idle = redeem_and_recall(
            run, recovered["shares"], credited=credited, settled=credited
        )
        claim(run, idle)


def run_unfunded_return_recovery(run: Run) -> None:
    """A return remains accounted while HYPE fees are missing; flush it once."""
    lane = _lane(run)
    credited = supply(run)
    attest(run, tag="initial", observation_label="observation_after_settle")
    run.advance(1)
    run.csim.fund_native(run.spoke_chain_id, lane.executor_address, 0)
    delivered_before = len(run.csim.delivered)
    run.hub.execute([lane.recall(amount=credited, min_return=credited)])
    run.relay()
    queued = _snapshot(run, "queued_return")
    assert len(run.csim.delivered) == delivered_before + 1
    assert queued["head"] != 0 and queued["head"] == queued["tail"]
    assert queued["observation"].tracked_idle == 0
    assert queued["observation"].queued_token_amount == credited
    assert queued["observation"].accounted_balance == credited
    assert queued["settled"] == credited and queued["idle"] == 0
    assert queued["return"] != bytes(32)
    assert queued["pending_outbound"] == 0

    run.hub.observe("nav_queued", lane.get_balance())
    run.hub.observe("transfer_staleness", lane.executor.transfer_staleness_max())
    results = run.relay()
    assert results[run.hub_chain_id].get("nav_queued") == credited
    run.advance(results[run.hub_chain_id].get("transfer_staleness") + 1)
    run.hub.observe("nav_stale_return", lane.get_balance())
    run.expected_failures.add("nav_stale_return")
    results = run.relay()
    assert_reverted(
        call(results, run.hub_chain_id, "nav_stale_return"),
        "TransferStale(uint256,uint256)",
    )

    # Funding belongs in a later block: rewriting the failure block would erase
    # the queued state when eth_simulateV1 replays the entire history.
    run.advance(1)
    run.csim.fund_native(run.spoke_chain_id, lane.executor_address, 10**18)
    run.spoke_sim.add_call(
        lane.dispatcher.flush_response(),
        from_=UNPRIVILEGED_FLUSHER,
        label="flush_return",
    )
    run.relay()
    returned = _snapshot(run, "flushed_return")
    assert len(run.csim.delivered) == delivered_before + 2
    assert returned["head"] == 0
    assert returned["observation"].queued_token_amount == 0
    assert returned["observation"].accounted_balance == 0
    assert returned["settled"] == 0 and returned["idle"] == credited
    assert returned["return"] == bytes(32)
    assert returned["pending_outbound"] == 0
    claim(run, credited)
    messages_after_claim = len(run.csim.delivered)
    run.relay()
    assert len(run.csim.delivered) == messages_after_claim
    run.spoke_sim.add_call(
        lane.dispatcher.flush_response(),
        from_=UNPRIVILEGED_FLUSHER,
        label="flush_empty_queue",
    )
    results = run.csim.relay()
    assert_relay_success(
        results, expected_failures=run.expected_failures | {"flush_empty_queue"}
    )
    assert_reverted(
        call(results, run.spoke_chain_id, "flush_empty_queue"), "NoQueuedResponse()"
    )

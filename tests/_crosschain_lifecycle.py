"""Full crosschain lifecycle between a hub vault and one spoke through the
transport-agnostic ``CrosschainLane`` and the ``CrosschainSimulator`` relay
(``eth_simulateV1``, no bridge: the CCIP Router is impersonated on delivery).
The CCIP rehearsal builds the ``Run`` from a factory pair compiled from the
released contracts.

1. ``lane.supply``: USDC leaves the vault, the transport credits the
   dispatcher a couple of minutes later, the settlement receipt fills the
   executor's settled bucket;
2. proposer/approver attest the settled balance (a settled bucket fails NAV
   closed until they do) and the hub vault refreshes the crosschain market;
3. ``lane.send_command`` DEPOSIT: the dispatcher deposits into the spoke
   PlasmaVault and ACKs;
4. more than ``STALENESS_MAX`` later NAV fails closed again: a proposal at the
   pre-DEPOSIT state version is refused, a fresh observation re-attests;
5. REDEEM in a later spoke block (past the 1 s redemption delay), then
   ``lane.recall``: the return leg lands in the executor's idle ledger;
6. the post-recall residue: the dispatcher observes zero while the settled
   bucket keeps the spoke vault's rounding, which the relative bound refuses
   to re-mark;
7. ``lane.claim`` pulls the idle ledger back into the vault.

Both chains move on one clock (``Run.advance``), pinned a couple of minutes
apart like the real chains, so every attestation gate (freshness, expiry,
approval cadence, state version) is crossed the way a keeper crosses it.
Every ``relay()`` re-runs both chains from their pinned blocks, so the phases
build one growing call list per chain; reads are queued only after the relay
that appended the deliveries they observe, and a deliberate revert stays in
the list, tolerated by label from then on.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from typing import TypeVar

from _crosschain import (
    assert_relay_success,
)
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3.types import Timestamp

from ipor_fusion import (
    ERC20,
    PlasmaVault,
    SimulationObservation,
    VaultSimulator,
)
from ipor_fusion.core.simulation import SimulatedCallResult, SimulationResult
from ipor_fusion.crosschain import (
    BalanceObservation,
    Command,
    CrosschainLane,
    CrosschainSimulator,
    CrosschainTransportKind,
    LaneObservation,
)
from ipor_fusion.types import Amount, ChainId, MarketId

log = logging.getLogger(__name__)

T = TypeVar("T")

SUPPLY_AMOUNT = {
    CrosschainTransportKind.STARGATE_LAYERZERO: 1_000_000,  # 1 USDC
    CrosschainTransportKind.CHAINLINK_CCIP: 100_000,  # 0.1 USDC, the canary size
}
NATIVE_BUDGET = 10**18  # executor and dispatcher pay bridge fees themselves

# Simulated schedule. The freshness window itself (``staleness_max``, 1 h on
# the POC executors) is read from the lane; MIN_UPDATE_INTERVAL is 60 s and a
# proposal needs a 15 min approval window.
BRIDGE_LATENCY = 2 * 60
APPROVAL_DELAY = 5 * 60
PROPOSAL_TTL = 30 * 60
PAST_STALENESS = 10 * 60

#: ``getBalance()`` failing closed on a stale settled bucket, per transport.
NAV_STALE_ERROR = {
    CrosschainTransportKind.STARGATE_LAYERZERO: "NavSettledStale(uint256)",
    CrosschainTransportKind.CHAINLINK_CCIP: "ObservationStale(uint256)",
}
PROPOSAL_VERSION_MISMATCH = "ProposalVersionMismatch(uint64,uint64)"
BIG_CHANGE_EXCEEDED = "BigChangeExceeded(uint256,uint256,uint256)"


@dataclass
class Run:
    """One lifecycle run: the lane under test, both chains' simulators, the
    hub-side handles the phases read through and each chain's modeled clock."""

    hub_chain_id: ChainId
    spoke_chain_id: ChainId
    spoke_name: str
    hub_vault_address: ChecksumAddress
    hub_asset_address: ChecksumAddress
    market_id: MarketId
    owner: ChecksumAddress
    balance_proposer: ChecksumAddress
    balance_approver: ChecksumAddress
    transport_kind: CrosschainTransportKind
    lane: CrosschainLane
    csim: CrosschainSimulator
    hub: VaultSimulator
    spoke_sim: VaultSimulator
    vault: PlasmaVault
    remote_vault: PlasmaVault
    hub_asset: ERC20
    amount: int
    hub_start: int
    staleness_max: int
    recall_min_return_bps: int = 9_800
    #: CCIP accounting of the executor generation under test. The deployed
    #: pilot debits a recall by the amount received and cannot re-mark a
    #: settled residue to zero; the source generation (recall-debit and
    #: dust-to-zero fixes) debits the sent amount and attests dust at or below the bound
    #: to zero.
    ccip_debits_sent_amount: bool = False
    attestation_zero_dust_sd: int = 0
    #: The executor's ``MIN_UPDATE_INTERVAL``: every attestation after the
    #: first waits it out (0: no wait).
    min_update_interval: int = 0
    relays: int = 0
    #: Labels of calls that revert on purpose; they stay in the replayed list.
    expected_failures: set[str] = field(default_factory=set)
    #: Reads one phase queues and a later phase compares against.
    vault_usdc_before: SimulationObservation[Amount] | None = None
    outbound_after_send: SimulationObservation[Amount] | None = None
    settled_after_settle: SimulationObservation[Amount] | None = None
    observation_after_settle: SimulationObservation[LaneObservation] | None = None
    idle_ledger: SimulationObservation[Amount] | None = None
    settled_after_return: SimulationObservation[Amount] | None = None
    observation_after_return: SimulationObservation[LaneObservation] | None = None

    @property
    def hub_now(self) -> int:
        return self.hub.current_time

    @property
    def spoke_now(self) -> int:
        return self.spoke_sim.current_time

    @property
    def spoke_block(self) -> int:
        return self.spoke_sim.current_block_number

    def advance(self, seconds: int) -> None:
        """Queue a new block on both chains with the same time shift."""
        self.hub.next_block(time_shift_seconds=seconds)
        self.spoke_sim.next_block(time_shift_seconds=seconds)

    def relay(self) -> dict[ChainId, SimulationResult]:
        """Relay, then require every call to succeed except the expected
        reverts, which must still be reverting."""
        results = self.csim.relay()
        self.relays += 1
        assert_relay_success(results, expected_failures=self.expected_failures)
        for label in self.expected_failures:
            assert not call(results, self.hub_chain_id, label).success, label
        log.debug(
            "%s/%s relay #%d: %d hub calls, %d spoke calls, %d messages delivered",
            self.transport_kind.name.lower(),
            self.spoke_name,
            self.relays,
            len(results[self.hub_chain_id].calls),
            len(results[self.spoke_chain_id].calls),
            len(self.csim.delivered),
        )
        return results

    def log(self, step: str, **values: object) -> None:
        """One line per step: lane, simulated time since the pinned hub block,
        then the numbers the step produced."""
        fields = " ".join(f"{key}={value}" for key, value in values.items())
        log.info(
            "%s/%s +%ds %s: %s",
            self.transport_kind.name.lower(),
            self.spoke_name,
            self.hub_now - self.hub_start,
            step,
            fields,
        )


def required(observation: SimulationObservation[T] | None) -> SimulationObservation[T]:
    """A read an earlier phase queued on the run."""
    assert observation is not None, "the phase that queues this read has not run"
    return observation


def call(results, chain_id: ChainId, label: str) -> SimulatedCallResult:
    return next(c for c in results[chain_id].calls if c.label == label)


def assert_reverted(call: SimulatedCallResult, error_signature: str) -> None:
    assert not call.success, f"{call.label} did not revert"
    expected = function_signature_to_4byte_selector(error_signature)
    assert bytes(call.return_data[:4]) == expected, (
        call.label,
        bytes(call.return_data[:4]).hex(),
        call.error,
    )


def supply(run: Run) -> int:
    """Bridge to the spoke; returns the amount credited to the dispatcher."""
    lane = run.lane
    delivered_before = len(run.csim.delivered)
    run.vault_usdc_before = run.csim.observe(
        run.hub_chain_id, run.hub_asset.balance_of(run.hub_vault_address)
    )
    run.hub.execute(
        [
            lane.supply(
                asset=run.hub_asset_address,
                amount=run.amount,
                min_received=(run.amount * 99 // 100)
                if lane.enforces_min_received
                else 0,
            )
        ]
    )
    run.outbound_after_send = outbound_after_send = run.csim.observe(
        run.hub_chain_id, lane.outbound_in_flight()
    )
    # The taxi and its settlement receipt land a couple of minutes after the send.
    run.advance(BRIDGE_LATENCY)
    run.relay()

    transfer, settled_receipt = run.csim.delivered[delivered_before:]
    credited = transfer.token_amount
    run.log(
        "supply sent",
        amount=run.amount,
        credited=credited,
        outbound_in_flight=outbound_after_send.value,
        messages=len(run.csim.delivered),
    )
    assert (
        transfer.dst_chain_id == run.spoke_chain_id
        and settled_receipt.dst_chain_id == run.hub_chain_id
    )
    assert 0 < credited <= run.amount
    if lane.enforces_min_received:
        assert credited >= run.amount * 99 // 100
    outbound = outbound_after_send.value
    if run.transport_kind == CrosschainTransportKind.CHAINLINK_CCIP:
        assert outbound == run.amount
    else:
        assert outbound == credited

    # a keeper observes after the receipt is visible: one block later, so the
    # observation strictly follows the executor's value-moving receipt
    run.advance(1)
    run.settled_after_settle = settled_after_settle = run.csim.observe(
        run.hub_chain_id, lane.settled_remote_balance()
    )
    outbound_after_settle = run.csim.observe(
        run.hub_chain_id, lane.outbound_in_flight()
    )
    remote_version = run.csim.observe(run.hub_chain_id, lane.remote_state_version())
    run.observation_after_settle = run.csim.observe(
        run.spoke_chain_id, lane.observation()
    )
    run.relay()
    assert settled_after_settle.value == credited
    assert outbound_after_settle.value == 0
    observation = run.observation_after_settle.value
    assert observation.tracked_idle == credited
    assert observation.accounted_balance == credited
    assert remote_version.value == observation.state_version
    run.log(
        "supply settled",
        settled=settled_after_settle.value,
        outbound_in_flight=outbound_after_settle.value,
        remote_idle=observation.tracked_idle,
        state_version=observation.state_version,
    )
    return credited


def _observation(
    run: Run, observation: LaneObservation, *, state_version: int
) -> BalanceObservation:
    """The proposer's view: the dispatcher snapshot at the spoke's current
    block, valid for ``PROPOSAL_TTL`` on the hub."""
    proposal = run.lane.attestation(
        observation,
        remote_block=run.spoke_block,
        remote_timestamp=run.spoke_now,
        expiry=run.hub_now + PROPOSAL_TTL,
        # the run keeps the spoke clock ahead of the hub's, so the executor's
        # own ordering check is what the rehearsal exercises
        receipt_at=Timestamp(0),
    )
    return replace(proposal, state_version=state_version)


def attest(
    run: Run,
    *,
    tag: str,
    observed: SimulationObservation[LaneObservation],
    hub_idle: int = 0,
    old_version: int | None = None,
    approve_error: str | None = None,
) -> int:
    """Two-key attestation of the dispatcher snapshot ``observed`` read,
    approved in a later block, then a market NAV refresh; returns the attested
    value. ``old_version`` also sends a proposal at that stale state version
    first, which must revert. ``approve_error`` names the custom error the
    approval must revert with instead of landing."""
    lane = run.lane
    observation = observed.value
    if old_version is not None:
        run.hub.add_call(
            lane.propose_balance(
                _observation(run, observation, state_version=old_version)
            ),
            from_=run.balance_proposer,
            label="propose_old_version",
        )
        run.expected_failures.add("propose_old_version")
    run.hub.add_call(
        lane.propose_balance(
            _observation(run, observation, state_version=observation.state_version)
        ),
        from_=run.balance_proposer,
        label=f"propose_{tag}",
    )
    results = run.relay()
    if old_version is not None:
        assert_reverted(
            call(results, run.hub_chain_id, "propose_old_version"),
            PROPOSAL_VERSION_MISMATCH,
        )
    proposal_id = lane.proposal_id_from_logs(
        call(results, run.hub_chain_id, f"propose_{tag}").logs
    )
    proposed: dict[str, object] = {
        "proposal_id": proposal_id,
        "settled_balance": observation.accounted_balance,
        "state_version": observation.state_version,
        "remote_timestamp": run.spoke_now,
        "expiry": run.hub_now + PROPOSAL_TTL,
    }
    if old_version is not None:
        proposed["old_version_refused"] = old_version
    run.log(f"attest {tag} proposed", **proposed)

    # The approver acts in a later block: expiry and cadence are checked there.
    run.advance(APPROVAL_DELAY)
    run.hub.add_call(
        lane.approve_balance(proposal_id),
        from_=run.balance_approver,
        label=f"approve_{tag}",
    )
    if approve_error is not None:
        run.expected_failures.add(f"approve_{tag}")
        results = run.relay()
        assert_reverted(
            call(results, run.hub_chain_id, f"approve_{tag}"), approve_error
        )
        run.log(f"attest {tag} refused", proposal_id=proposal_id, error=approve_error)
        return observation.accounted_balance

    executor_balance = run.csim.observe(
        run.hub_chain_id, lane.get_balance(), label=f"executor_balance_{tag}"
    )
    run.hub.add_call(
        run.vault.update_markets_balances([run.market_id]),
        from_=run.owner,
        label=f"update_balances_{tag}",
    )
    market_total_read = run.csim.observe(
        run.hub_chain_id,
        run.vault.total_assets_in_market(run.market_id),
        label=f"market_total_{tag}",
    )
    run.relay()
    attested = observation.accounted_balance
    expected = attested + hub_idle
    assert executor_balance.value == expected
    market_total = market_total_read.value
    assert abs(market_total - expected) <= expected // 50, (market_total, expected)
    run.log(
        f"attest {tag} approved",
        proposal_id=proposal_id,
        attested=attested,
        executor_balance=executor_balance.value,
        market_total=market_total,
    )
    return attested


def _deposit(run: Run, credited: int) -> int:
    """DEPOSIT the dispatcher's idle into the spoke vault; returns the shares."""
    lane = run.lane
    before = required(run.observation_after_settle).value
    run.hub.execute(
        [lane.send_command(Command.deposit(run.remote_vault.address, credited))]
    )
    run.relay()
    observation_after_deposit = run.csim.observe(run.spoke_chain_id, lane.observation())
    remote_shares = run.csim.observe(
        run.spoke_chain_id,
        run.remote_vault.balance_of(lane.executor_address),
    )
    active_after_ack = run.csim.observe(run.hub_chain_id, lane.has_active_command())
    run.relay()
    after = observation_after_deposit.value
    shares = remote_shares.value
    assert shares > 0
    assert after.tracked_idle == 0
    assert abs(after.accounted_balance - credited) <= credited // 1000
    # value-neutral for the version: the ACK carries the current one
    assert after.state_version == before.state_version
    assert active_after_ack.value is False
    run.log(
        "deposit acked",
        shares=shares,
        accounted_balance=after.accounted_balance,
        state_version=after.state_version,
        active_command=active_after_ack.value,
    )
    return shares


def _renew_after_gap(run: Run, credited: int) -> int:
    """Past ``STALENESS_MAX`` the settled bucket closes NAV; a keeper re-attests
    from a fresh observation at the state version the DEPOSIT left unchanged. Returns the
    newly attested settled value."""
    lane = run.lane
    before = required(run.observation_after_settle).value
    gap = run.staleness_max + PAST_STALENESS
    run.advance(gap)
    run.csim.observe(run.hub_chain_id, lane.get_balance(), label="nav_after_gap")
    run.expected_failures.add("nav_after_gap")
    remote_version_after_gap = run.csim.observe(
        run.hub_chain_id, lane.remote_state_version()
    )
    observation_after_gap = run.csim.observe(run.spoke_chain_id, lane.observation())
    results = run.relay()
    assert_reverted(
        call(results, run.hub_chain_id, "nav_after_gap"),
        NAV_STALE_ERROR[run.transport_kind],
    )
    after = observation_after_gap.value
    assert after.state_version == before.state_version
    assert remote_version_after_gap.value == after.state_version
    run.log(
        "gap",
        gap_s=gap,
        nav_error=NAV_STALE_ERROR[run.transport_kind],
        state_version=after.state_version,
        accounted_balance=after.accounted_balance,
    )
    settled = attest(
        run,
        tag="renewed",
        observed=observation_after_gap,
        # an earlier version than the one observed must be refused
        old_version=after.state_version - 1,
    )
    assert abs(settled - credited) <= credited // 1000
    return settled


def redeem_and_recall(run: Run, shares: int, *, credited: int) -> int:
    """REDEEM in a later spoke block, attest the redeemed amount, recall
    everything; returns the idle credited home."""
    lane = run.lane
    run.advance(60)
    remote_max_withdraw = run.csim.observe(
        run.spoke_chain_id,
        run.remote_vault.max_withdraw(lane.executor_address),
    )
    run.hub.execute(
        [lane.send_command(Command.redeem(run.remote_vault.address, shares))]
    )
    run.relay()
    assert remote_max_withdraw.value > 0
    run.advance(1)
    observation_after_redeem = run.csim.observe(run.spoke_chain_id, lane.observation())
    shares_after_redeem = run.csim.observe(
        run.spoke_chain_id,
        run.remote_vault.balance_of(lane.executor_address),
    )
    run.relay()
    remote_idle = observation_after_redeem.value.tracked_idle
    assert shares_after_redeem.value == 0
    assert 0 < remote_idle <= credited
    run.log(
        "redeem acked",
        shares_redeemed=shares,
        remote_idle=remote_idle,
        state_version=observation_after_redeem.value.state_version,
    )

    # The dispatcher carries the position at deposit cost and the vault's
    # management fee surfaces only at REDEEM, so a recall straight after it
    # would leave that fee in the settled bucket. Attest the redeemed amount
    # first (a change inside bigChangeBps), then recall exactly that.
    if run.min_update_interval:
        run.advance(run.min_update_interval)
    settled = attest(run, tag="redeemed", observed=observation_after_redeem)
    assert settled == remote_idle

    run.hub.execute(
        [
            lane.recall(
                amount=remote_idle,
                min_return=remote_idle * run.recall_min_return_bps // 10_000,
            )
        ]
    )
    run.relay()
    run.idle_ledger = idle_ledger = run.csim.observe(
        run.hub_chain_id, lane.idle_ledger()
    )
    pending_transfers = run.csim.observe(
        run.hub_chain_id, lane.pending_transfer_count()
    )
    run.settled_after_return = settled_after_return = run.csim.observe(
        run.hub_chain_id, lane.settled_remote_balance()
    )
    remote_version_after_return = run.csim.observe(
        run.hub_chain_id, lane.remote_state_version()
    )
    run.advance(1)
    run.observation_after_return = observation_after_return = run.csim.observe(
        run.spoke_chain_id, lane.observation()
    )
    run.relay()
    idle = idle_ledger.value
    run.log(
        "recall settled",
        recalled=remote_idle,
        idle_ledger=idle,
        settled_residue=settled_after_return.value,
        pending_transfers=pending_transfers.value,
        state_version=observation_after_return.value.state_version,
    )
    assert 0 < idle <= remote_idle
    if run.transport_kind == CrosschainTransportKind.STARGATE_LAYERZERO:
        assert idle >= remote_idle * 98 // 100
    assert pending_transfers.value == 0
    # Stargate and the new-accounting CCIP executors debit the amount sent
    # from the spoke; the CCIP pilot debits the amount actually received on
    # the hub after any token-pool fee, which then stays in the settled
    # bucket as a residue.
    debit = (
        idle
        if run.transport_kind == CrosschainTransportKind.CHAINLINK_CCIP
        and not run.ccip_debits_sent_amount
        else remote_idle
    )
    assert settled_after_return.value == max(remote_idle - debit, 0)
    after = observation_after_return.value
    assert after.tracked_idle == 0
    assert remote_version_after_return.value == after.state_version
    return idle


def _attest_residue(run: Run, idle: int) -> None:
    """The dispatcher observes zero after a full recall. A settled residue
    above ``attestation_zero_dust_sd`` cannot be re-marked to zero (the
    relative bound refuses it); at or below the bound the source generation
    resets it, and zero-to-zero is always allowed."""
    residue = required(run.settled_after_return).value
    refused = residue > run.attestation_zero_dust_sd
    run.log(
        "residue",
        settled_residue=residue,
        expect=BIG_CHANGE_EXCEEDED if refused else "approval",
    )
    # An approval that the balance checks would accept is otherwise refused
    # with `MinUpdateIntervalNotMet` (the relative bound rejects a residue
    # above the dust bound before that check is reached).
    if run.min_update_interval:
        run.advance(run.min_update_interval)
    attest(
        run,
        tag="residue",
        observed=required(run.observation_after_return),
        hub_idle=idle,
        approve_error=BIG_CHANGE_EXCEEDED if refused else None,
    )


def claim(run: Run, idle: int) -> None:
    lane = run.lane
    run.hub.execute([lane.claim(idle)])
    vault_usdc_after = run.csim.observe(
        run.hub_chain_id,
        run.hub_asset.balance_of(run.hub_vault_address),
    )
    executor_usdc_after = run.csim.observe(
        run.hub_chain_id,
        run.hub_asset.balance_of(lane.executor_address),
    )
    idle_after_claim = run.csim.observe(run.hub_chain_id, lane.idle_ledger())
    run.relay()
    vault_before = required(run.vault_usdc_before).value
    assert vault_usdc_after.value == vault_before - run.amount + idle
    assert executor_usdc_after.value == 0
    assert idle_after_claim.value == 0
    run.log(
        "claim",
        claimed=idle,
        vault_usdc_before=vault_before,
        vault_usdc_after=vault_usdc_after.value,
        round_trip_cost=run.amount - idle,
    )


def run_lifecycle(run: Run) -> None:
    """Execute the transport-independent lifecycle against a prepared lane."""
    credited = supply(run)
    attest(run, tag="initial", observed=required(run.observation_after_settle))
    shares = _deposit(run, credited)
    _renew_after_gap(run, credited)
    idle = redeem_and_recall(run, shares, credited=credited)
    _attest_residue(run, idle)
    claim(run, idle)
    run.log("done", messages=len(run.csim.delivered), relays=run.relays)

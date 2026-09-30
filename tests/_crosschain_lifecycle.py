"""Full crosschain lifecycle between a deployment's hub vault and one spoke,
on every transport, through the transport-agnostic ``CrosschainLane`` and the
``CrosschainSimulator`` relay (``eth_simulateV1``, no bridge: the LayerZero
endpoint or the CCIP Router is impersonated on delivery and the Stargate USDC
pools stand in for the token release). Parametrized over ``LIFECYCLES``.

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

from _crosschain import (
    Deployment,
    Spoke,
    assert_relay_success,
)
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion import (
    ERC20,
    BalanceObservation,
    Command,
    CrosschainLane,
    CrosschainSimulator,
    CrosschainTransportKind,
    LaneObservation,
    PlasmaVault,
    VaultSimulator,
    Web3Context,
    discover_deployment,
    open_lane,
)
from ipor_fusion.core.simulation import SimulatedCallResult, SimulationResult
from ipor_fusion.types import ChainId, MarketId

log = logging.getLogger(__name__)

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
    relays: int = 0
    #: Labels of calls that revert on purpose; they stay in the replayed list.
    expected_failures: set[str] = field(default_factory=set)

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


def prepare_deployed_run(
    web3_hub, web3_spoke, dep: Deployment, spoke: Spoke, transport_kind
) -> Run:
    hub_ctx = Web3Context(web3=web3_hub, chain_id=dep.hub.chain_id)
    hub_ctx.default_block = dep.hub.block
    spoke_ctx = Web3Context(web3=web3_spoke, chain_id=spoke.chain_id)
    spoke_ctx.default_block = spoke.chain.block
    remote_vault = spoke.remote_vault
    assert remote_vault is not None

    # Discovery exercised for real: transport detection plus the fuses found on
    # the vault by market id and selector must equal the pinned addresses.
    lane = open_lane(
        hub_ctx,
        spoke_ctx,
        executor=dep.executor(transport_kind),
        market_id=dep.market_id,
    )
    assert lane.transport_kind == transport_kind
    assert lane.fuses == dep.lane_fuses(transport_kind)
    # Drift guards: the executor generation the wrappers mirror comes from this
    # factory, and it must be bound to the hub vault.
    assert lane.executor.factory().call() == dep.factory(transport_kind)
    assert lane.executor.manager().call() == dep.vault
    # The spoke side really is this executor's dispatcher: same CREATE3 address
    # (the lane enforces it), created by the same factory, pointing at the hub.
    assert lane.dispatcher.executor_chain_id().call() == dep.hub.chain_id
    assert lane.dispatcher.factory().call() == dep.factory(transport_kind)
    assert lane.executor.has_dispatcher(spoke.chain_id).call()
    # Discovery from the vault alone must describe the pinned deployment.
    deployment = discover_deployment(hub_ctx, dep.vault, dep.market_id)
    assert {e.transport_kind for e in deployment.executors} == set(dep.transports)
    info = deployment.executor(transport_kind)
    assert info.address == dep.executor(transport_kind)
    assert info.factory == dep.factory(transport_kind)
    assert info.fuses == dep.lane_fuses(transport_kind)
    assert (info.balance_proposer, info.balance_approver) == (
        dep.balance_proposer,
        dep.balance_approver,
    )
    assert spoke.chain_id in info.spoke_chain_ids
    assert deployment.remote_vaults[spoke.chain_id] == (remote_vault,)

    csim = CrosschainSimulator(dep.transport(transport_kind))
    hub = csim.add_chain(
        dep.hub.chain_id,
        web3_hub,
        block=dep.hub.block,
        vault=dep.vault,
        alpha=dep.owner,
    )
    spoke_sim = csim.add_chain(spoke.chain_id, web3_spoke, block=spoke.chain.block)
    csim.fund_native(dep.hub.chain_id, lane.executor_address, NATIVE_BUDGET)
    csim.fund_native(spoke.chain_id, lane.executor_address, NATIVE_BUDGET)
    hub_start = int(web3_hub.eth.get_block(dep.hub.block)["timestamp"])
    spoke_start = int(web3_spoke.eth.get_block(spoke.chain.block)["timestamp"])
    run = Run(
        hub_chain_id=ChainId(dep.hub.chain_id),
        spoke_chain_id=ChainId(spoke.chain_id),
        spoke_name=spoke.name,
        hub_vault_address=dep.vault,
        hub_asset_address=dep.hub.usdc,
        market_id=dep.market_id,
        owner=dep.owner,
        balance_proposer=dep.balance_proposer,
        balance_approver=dep.balance_approver,
        transport_kind=transport_kind,
        lane=lane,
        csim=csim,
        hub=hub,
        spoke_sim=spoke_sim,
        vault=PlasmaVault(hub_ctx, dep.vault),
        remote_vault=PlasmaVault(spoke_ctx, remote_vault),
        hub_asset=ERC20(hub_ctx, dep.hub.usdc),
        amount=SUPPLY_AMOUNT[transport_kind],
        hub_start=hub_start,
        staleness_max=lane.staleness_max().call(),
    )
    run.log(
        "open_lane",
        executor=lane.executor_address,
        staleness_max=run.staleness_max,
        supply_fuse=lane.fuses.supply,
        command_fuse=lane.fuses.command,
        claim_fuse=lane.fuses.claim,
        hub_block=dep.hub.block,
        spoke_block=spoke.chain.block,
        pin_offset_s=hub_start - spoke_start,
    )
    return run


def supply(run: Run) -> int:
    """Bridge to the spoke; returns the amount credited to the dispatcher."""
    lane = run.lane
    delivered_before = len(run.csim.delivered)
    run.csim.observe(
        run.hub_chain_id,
        "vault_usdc_before",
        run.hub_asset.balance_of(run.hub_vault_address),
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
    run.csim.observe(run.hub_chain_id, "outbound_after_send", lane.outbound_in_flight())
    # The taxi and its settlement receipt land a couple of minutes after the send.
    run.advance(BRIDGE_LATENCY)
    results = run.relay()

    transfer, settled_receipt = run.csim.delivered[delivered_before:]
    credited = transfer.token_amount
    run.log(
        "supply sent",
        amount=run.amount,
        credited=credited,
        outbound_in_flight=results[run.hub_chain_id].get("outbound_after_send"),
        messages=len(run.csim.delivered),
    )
    assert (
        transfer.dst_chain_id == run.spoke_chain_id
        and settled_receipt.dst_chain_id == run.hub_chain_id
    )
    assert 0 < credited <= run.amount
    if lane.enforces_min_received:
        assert credited >= run.amount * 99 // 100
    outbound = results[run.hub_chain_id].get("outbound_after_send")
    if run.transport_kind == CrosschainTransportKind.CHAINLINK_CCIP:
        assert outbound == run.amount
    else:
        assert outbound == credited

    run.csim.observe(
        run.hub_chain_id, "settled_after_settle", lane.settled_remote_balance()
    )
    run.csim.observe(
        run.hub_chain_id, "outbound_after_settle", lane.outbound_in_flight()
    )
    run.csim.observe(run.hub_chain_id, "remote_version", lane.remote_state_version())
    run.csim.observe(run.spoke_chain_id, "observation_after_settle", lane.observation())
    results = run.relay()
    assert results[run.hub_chain_id].get("settled_after_settle") == credited
    assert results[run.hub_chain_id].get("outbound_after_settle") == 0
    observation = results[run.spoke_chain_id].get("observation_after_settle")
    assert observation.tracked_idle == credited
    assert observation.accounted_balance == credited
    assert results[run.hub_chain_id].get("remote_version") == observation.state_version
    run.log(
        "supply settled",
        settled=results[run.hub_chain_id].get("settled_after_settle"),
        outbound_in_flight=results[run.hub_chain_id].get("outbound_after_settle"),
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
    )
    return replace(proposal, state_version=state_version)


def attest(
    run: Run,
    *,
    tag: str,
    observation_label: str,
    hub_idle: int = 0,
    old_version: int | None = None,
    approve_error: str | None = None,
) -> int:
    """Two-key attestation of the observation under ``observation_label``,
    approved in a later block, then a market NAV refresh; returns the attested
    value. ``old_version`` also sends a proposal at that stale state version
    first, which must revert. ``approve_error`` names the custom error the
    approval must revert with instead of landing."""
    lane = run.lane
    observation = run.csim.results[run.spoke_chain_id].get(observation_label)
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

    run.csim.observe(run.hub_chain_id, f"executor_balance_{tag}", lane.get_balance())
    run.hub.add_call(
        run.vault.update_markets_balances([run.market_id]),
        from_=run.owner,
        label=f"update_balances_{tag}",
    )
    run.csim.observe(
        run.hub_chain_id,
        f"market_total_{tag}",
        run.vault.total_assets_in_market(run.market_id),
    )
    results = run.relay()
    attested = observation.accounted_balance
    expected = attested + hub_idle
    assert results[run.hub_chain_id].get(f"executor_balance_{tag}") == expected
    market_total = results[run.hub_chain_id].get(f"market_total_{tag}")
    assert abs(market_total - expected) <= expected // 50, (market_total, expected)
    run.log(
        f"attest {tag} approved",
        proposal_id=proposal_id,
        attested=attested,
        executor_balance=results[run.hub_chain_id].get(f"executor_balance_{tag}"),
        market_total=market_total,
    )
    return attested


def _deposit(run: Run, credited: int) -> int:
    """DEPOSIT the dispatcher's idle into the spoke vault; returns the shares."""
    lane = run.lane
    before = run.csim.results[run.spoke_chain_id].get("observation_after_settle")
    run.hub.execute(
        [lane.send_command(Command.deposit(run.remote_vault.address, credited))]
    )
    run.relay()
    run.csim.observe(
        run.spoke_chain_id, "observation_after_deposit", lane.observation()
    )
    run.csim.observe(
        run.spoke_chain_id,
        "remote_shares",
        run.remote_vault.balance_of(lane.executor_address),
    )
    run.csim.observe(run.hub_chain_id, "active_after_ack", lane.has_active_command())
    results = run.relay()
    after = results[run.spoke_chain_id].get("observation_after_deposit")
    shares = results[run.spoke_chain_id].get("remote_shares")
    assert shares > 0
    assert after.tracked_idle == 0
    assert abs(after.accounted_balance - credited) <= credited // 1000
    assert after.state_version == before.state_version + 1
    assert results[run.hub_chain_id].get("active_after_ack") is False
    run.log(
        "deposit acked",
        shares=shares,
        accounted_balance=after.accounted_balance,
        state_version=after.state_version,
        active_command=results[run.hub_chain_id].get("active_after_ack"),
    )
    return shares


def _renew_after_gap(run: Run, credited: int) -> int:
    """Past ``STALENESS_MAX`` the settled bucket closes NAV; a keeper re-attests
    from a fresh observation at the post-DEPOSIT state version. Returns the
    newly attested settled value."""
    lane = run.lane
    before = run.csim.results[run.spoke_chain_id].get("observation_after_settle")
    gap = run.staleness_max + PAST_STALENESS
    run.advance(gap)
    run.csim.observe(run.hub_chain_id, "nav_after_gap", lane.get_balance())
    run.expected_failures.add("nav_after_gap")
    run.csim.observe(
        run.hub_chain_id, "remote_version_after_gap", lane.remote_state_version()
    )
    run.csim.observe(run.spoke_chain_id, "observation_after_gap", lane.observation())
    results = run.relay()
    assert_reverted(
        call(results, run.hub_chain_id, "nav_after_gap"),
        NAV_STALE_ERROR[run.transport_kind],
    )
    after = results[run.spoke_chain_id].get("observation_after_gap")
    assert after.state_version == before.state_version + 1
    assert results[run.hub_chain_id].get("remote_version_after_gap") == (
        after.state_version
    )
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
        observation_label="observation_after_gap",
        old_version=before.state_version,
    )
    assert abs(settled - credited) <= credited // 1000
    return settled


def redeem_and_recall(run: Run, shares: int, *, credited: int, settled: int) -> int:
    """REDEEM in a later spoke block, recall everything; returns the idle
    credited home."""
    lane = run.lane
    run.advance(60)
    run.csim.observe(
        run.spoke_chain_id,
        "remote_max_withdraw",
        run.remote_vault.max_withdraw(lane.executor_address),
    )
    run.hub.execute(
        [lane.send_command(Command.redeem(run.remote_vault.address, shares))]
    )
    results = run.relay()
    assert results[run.spoke_chain_id].get("remote_max_withdraw") > 0
    run.csim.observe(run.spoke_chain_id, "observation_after_redeem", lane.observation())
    run.csim.observe(
        run.spoke_chain_id,
        "shares_after_redeem",
        run.remote_vault.balance_of(lane.executor_address),
    )
    results = run.relay()
    remote_idle = (
        results[run.spoke_chain_id].get("observation_after_redeem").tracked_idle
    )
    assert results[run.spoke_chain_id].get("shares_after_redeem") == 0
    assert 0 < remote_idle <= credited
    run.log(
        "redeem acked",
        shares_redeemed=shares,
        remote_idle=remote_idle,
        state_version=results[run.spoke_chain_id]
        .get("observation_after_redeem")
        .state_version,
    )

    run.hub.execute(
        [
            lane.recall(
                amount=remote_idle,
                min_return=remote_idle * run.recall_min_return_bps // 10_000,
            )
        ]
    )
    run.relay()
    run.csim.observe(run.hub_chain_id, "idle_ledger", lane.idle_ledger())
    run.csim.observe(
        run.hub_chain_id, "pending_transfers", lane.pending_transfer_count()
    )
    run.csim.observe(
        run.hub_chain_id, "settled_after_return", lane.settled_remote_balance()
    )
    run.csim.observe(
        run.hub_chain_id, "remote_version_after_return", lane.remote_state_version()
    )
    run.csim.observe(run.spoke_chain_id, "observation_after_return", lane.observation())
    results = run.relay()
    idle = results[run.hub_chain_id].get("idle_ledger")
    run.log(
        "recall settled",
        recalled=remote_idle,
        idle_ledger=idle,
        settled_residue=results[run.hub_chain_id].get("settled_after_return"),
        pending_transfers=results[run.hub_chain_id].get("pending_transfers"),
        state_version=results[run.spoke_chain_id]
        .get("observation_after_return")
        .state_version,
    )
    assert 0 < idle <= remote_idle
    if run.transport_kind == CrosschainTransportKind.STARGATE_LAYERZERO:
        assert idle >= remote_idle * 98 // 100
    assert results[run.hub_chain_id].get("pending_transfers") == 0
    # Stargate debits the amount sent from the spoke; CCIP debits the amount
    # actually received on the hub after any token-pool fee. Vault rounding
    # remains in the settled bucket until the next attestation.
    debit = (
        idle
        if run.transport_kind == CrosschainTransportKind.CHAINLINK_CCIP
        else remote_idle
    )
    assert results[run.hub_chain_id].get("settled_after_return") == max(
        settled - debit, 0
    )
    after = results[run.spoke_chain_id].get("observation_after_return")
    assert after.tracked_idle == 0
    assert results[run.hub_chain_id].get("remote_version_after_return") == (
        after.state_version
    )
    return idle


def _attest_residue(run: Run, idle: int) -> None:
    """The dispatcher observes zero after a full recall. If the settled bucket
    kept rounding dust, the relative bound refuses to re-mark it to zero;
    zero-to-zero is the one attestation allowed from an empty bucket."""
    residue = run.csim.results[run.hub_chain_id].get("settled_after_return")
    run.log(
        "residue",
        settled_residue=residue,
        expect=BIG_CHANGE_EXCEEDED if residue else "zero-to-zero approval",
    )
    attest(
        run,
        tag="residue",
        observation_label="observation_after_return",
        hub_idle=idle,
        approve_error=BIG_CHANGE_EXCEEDED if residue else None,
    )


def claim(run: Run, idle: int) -> None:
    lane = run.lane
    run.hub.execute([lane.claim(idle)])
    run.csim.observe(
        run.hub_chain_id,
        "vault_usdc_after",
        run.hub_asset.balance_of(run.hub_vault_address),
    )
    run.csim.observe(
        run.hub_chain_id,
        "executor_usdc_after",
        run.hub_asset.balance_of(lane.executor_address),
    )
    run.csim.observe(run.hub_chain_id, "idle_after_claim", lane.idle_ledger())
    results = run.relay()
    vault_before = results[run.hub_chain_id].get("vault_usdc_before")
    assert (
        results[run.hub_chain_id].get("vault_usdc_after")
        == vault_before - run.amount + idle
    )
    assert results[run.hub_chain_id].get("executor_usdc_after") == 0
    assert results[run.hub_chain_id].get("idle_after_claim") == 0
    run.log(
        "claim",
        claimed=idle,
        vault_usdc_before=vault_before,
        vault_usdc_after=results[run.hub_chain_id].get("vault_usdc_after"),
        round_trip_cost=run.amount - idle,
    )


def run_lifecycle(run: Run) -> None:
    """Execute the transport-independent lifecycle against a prepared lane."""
    credited = supply(run)
    attest(run, tag="initial", observation_label="observation_after_settle")
    shares = _deposit(run, credited)
    settled = _renew_after_gap(run, credited)
    idle = redeem_and_recall(run, shares, credited=credited, settled=settled)
    _attest_residue(run, idle)
    claim(run, idle)
    run.log("done", messages=len(run.csim.delivered), relays=run.relays)

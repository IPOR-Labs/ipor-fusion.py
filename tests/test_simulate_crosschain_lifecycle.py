"""Full crosschain lifecycle between a deployment's hub vault and one spoke,
on every transport, through the transport-agnostic ``CrosschainLane`` and the
``CrosschainSimulator`` relay (``eth_simulateV1``, no bridge: the LayerZero
endpoint or the CCIP Router is impersonated on delivery and the Stargate USDC
pools stand in for the token release). Parametrized over ``LIFECYCLES``.

1. ``lane.supply``: USDC leaves the vault, the transport credits the
   dispatcher, the settlement receipt fills the executor's settled bucket;
2. proposer/approver attest the settled balance (a settled bucket fails NAV
   closed until they do) and the hub vault refreshes the crosschain market;
3. ``lane.send_command`` DEPOSIT: the dispatcher deposits into the spoke
   PlasmaVault and ACKs;
4. REDEEM in a later spoke block (past the 1 s redemption delay), then
   ``lane.recall``: the return leg lands in the executor's idle ledger;
5. ``lane.claim`` pulls the idle ledger back into the vault.

Every ``relay()`` re-runs both chains from their pinned blocks, so the phases
build one growing call list per chain; reads are queued only after the relay
that appended the deliveries they observe.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import pytest
from _crosschain import (
    LIFECYCLES,
    Deployment,
    Spoke,
    assert_relay_success,
)

from ipor_fusion import (
    ERC20,
    BalanceObservation,
    Command,
    CrosschainLane,
    CrosschainSimulator,
    CrosschainTransportKind,
    PlasmaVault,
    VaultSimulator,
    Web3Context,
    open_lane,
)

log = logging.getLogger(__name__)

SUPPLY_AMOUNT = {
    CrosschainTransportKind.STARGATE_LAYERZERO: 1_000_000,  # 1 USDC
    CrosschainTransportKind.CHAINLINK_CCIP: 100_000,  # 0.1 USDC, the canary size
}
NATIVE_BUDGET = 10**18  # executor and dispatcher pay bridge fees themselves


@dataclass
class Run:
    """One lifecycle run: the lane under test, both chains' simulators and the
    hub-side handles the phases read through."""

    dep: Deployment
    spoke: Spoke
    lane: CrosschainLane
    csim: CrosschainSimulator
    hub: VaultSimulator
    spoke_sim: VaultSimulator
    vault: PlasmaVault
    remote_vault: PlasmaVault
    usdc_hub: ERC20
    hub_timestamp: int
    spoke_timestamp: int
    amount: int

    @property
    def hub_chain_id(self) -> int:
        return self.dep.hub.chain_id

    @property
    def spoke_chain_id(self) -> int:
        return self.spoke.chain_id


def _run(web3_hub, web3_spoke, dep: Deployment, spoke: Spoke, transport_kind) -> Run:
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
    return Run(
        dep=dep,
        spoke=spoke,
        lane=lane,
        csim=csim,
        hub=hub,
        spoke_sim=spoke_sim,
        vault=PlasmaVault(hub_ctx, dep.vault),
        remote_vault=PlasmaVault(spoke_ctx, remote_vault),
        usdc_hub=ERC20(hub_ctx, dep.hub.usdc),
        hub_timestamp=int(web3_hub.eth.get_block(dep.hub.block)["timestamp"]),
        spoke_timestamp=int(web3_spoke.eth.get_block(spoke.chain.block)["timestamp"]),
        amount=SUPPLY_AMOUNT[transport_kind],
    )


def _supply(run: Run) -> int:
    """Bridge to the spoke; returns the amount credited to the dispatcher."""
    lane = run.lane
    run.csim.observe(
        run.hub_chain_id, "vault_usdc_before", run.usdc_hub.balance_of(run.dep.vault)
    )
    run.hub.execute(
        [
            lane.supply(
                asset=run.dep.hub.usdc,
                amount=run.amount,
                min_received=run.amount * 99 // 100,
            )
        ]
    )
    run.csim.observe(run.hub_chain_id, "outbound_after_send", lane.outbound_in_flight())
    results = run.csim.relay()
    assert_relay_success(results)

    transfer, settled_receipt = run.csim.delivered
    credited = transfer.token_amount
    log.info("%s: bridged %s, credited %s", run.spoke.name, run.amount, credited)
    assert (
        transfer.dst_chain_id == run.spoke_chain_id
        and settled_receipt.dst_chain_id == run.hub_chain_id
    )
    assert run.amount * 99 // 100 <= credited <= run.amount
    assert results[run.hub_chain_id].get("outbound_after_send") == credited

    run.csim.observe(
        run.hub_chain_id, "settled_after_settle", lane.settled_remote_balance()
    )
    run.csim.observe(
        run.hub_chain_id, "outbound_after_settle", lane.outbound_in_flight()
    )
    run.csim.observe(run.hub_chain_id, "remote_version", lane.remote_state_version())
    run.csim.observe(run.spoke_chain_id, "observation_after_settle", lane.observation())
    results = run.csim.relay()
    assert_relay_success(results)
    assert results[run.hub_chain_id].get("settled_after_settle") == credited
    assert results[run.hub_chain_id].get("outbound_after_settle") == 0
    observation = results[run.spoke_chain_id].get("observation_after_settle")
    assert observation.tracked_idle == credited
    assert observation.accounted_balance == credited
    assert results[run.hub_chain_id].get("remote_version") == observation.state_version
    return credited


def _attest(run: Run, credited: int) -> None:
    """Two-key attestation of the settled balance, then a market NAV refresh.
    A settled bucket makes getBalance() fail closed until this lands, and the
    DEPOSIT gate reads getBalance() first."""
    lane = run.lane
    observation = run.csim.results[run.spoke_chain_id].get("observation_after_settle")
    run.hub.add_call(
        lane.propose_balance(
            BalanceObservation(
                chain_id=run.spoke_chain_id,
                settled_balance=observation.accounted_balance,
                state_version=observation.state_version,
                remote_block=run.spoke.chain.block,
                remote_timestamp=run.spoke_timestamp,
                expiry=run.hub_timestamp + 30 * 60,
                tracked_position_set_hash=observation.tracked_position_set_hash,
            )
        ),
        from_=run.dep.balance_proposer,
        label="propose_balance",
    )
    results = run.csim.relay()
    assert_relay_success(results)
    proposed = next(
        c for c in results[run.hub_chain_id].calls if c.label == "propose_balance"
    )
    proposal_id = lane.proposal_id_from_logs(proposed.logs)
    run.hub.add_call(
        lane.approve_balance(proposal_id),
        from_=run.dep.balance_approver,
        label="approve_balance",
    )
    run.csim.observe(run.hub_chain_id, "executor_balance", lane.get_balance())
    run.hub.add_call(
        run.vault.update_markets_balances([run.dep.market_id]),
        from_=run.dep.owner,
        label="update_balances",
    )
    run.csim.observe(
        run.hub_chain_id,
        "market_total",
        run.vault.total_assets_in_market(run.dep.market_id),
    )
    results = run.csim.relay()
    assert_relay_success(results)
    assert results[run.hub_chain_id].get("executor_balance") == credited
    market_total = results[run.hub_chain_id].get("market_total")
    assert abs(market_total - credited) <= credited // 50, (market_total, credited)


def _deposit(run: Run, credited: int) -> int:
    """DEPOSIT the dispatcher's idle into the spoke vault; returns the shares."""
    lane = run.lane
    before = run.csim.results[run.spoke_chain_id].get("observation_after_settle")
    run.hub.execute(
        [lane.send_command(Command.deposit(run.remote_vault.address, credited))]
    )
    assert_relay_success(run.csim.relay())
    run.csim.observe(
        run.spoke_chain_id, "observation_after_deposit", lane.observation()
    )
    run.csim.observe(
        run.spoke_chain_id,
        "remote_shares",
        run.remote_vault.balance_of(lane.executor_address),
    )
    run.csim.observe(run.hub_chain_id, "active_after_ack", lane.has_active_command())
    results = run.csim.relay()
    assert_relay_success(results)
    after = results[run.spoke_chain_id].get("observation_after_deposit")
    shares = results[run.spoke_chain_id].get("remote_shares")
    assert shares > 0
    assert after.tracked_idle == 0
    assert abs(after.accounted_balance - credited) <= credited // 1000
    assert after.state_version == before.state_version + 1
    assert results[run.hub_chain_id].get("active_after_ack") is False
    return shares


def _redeem_and_recall(run: Run, shares: int, credited: int) -> int:
    """REDEEM in a later spoke block, recall everything; returns the idle credited home."""
    lane = run.lane
    run.spoke_sim.next_block(time_shift_seconds=60)
    run.hub.execute(
        [lane.send_command(Command.redeem(run.remote_vault.address, shares))]
    )
    assert_relay_success(run.csim.relay())
    run.csim.observe(run.spoke_chain_id, "observation_after_redeem", lane.observation())
    run.csim.observe(
        run.spoke_chain_id,
        "shares_after_redeem",
        run.remote_vault.balance_of(lane.executor_address),
    )
    results = run.csim.relay()
    assert_relay_success(results)
    remote_idle = (
        results[run.spoke_chain_id].get("observation_after_redeem").tracked_idle
    )
    assert results[run.spoke_chain_id].get("shares_after_redeem") == 0
    assert 0 < remote_idle <= credited

    run.hub.execute(
        [lane.recall(amount=remote_idle, min_return=remote_idle * 98 // 100)]
    )
    assert_relay_success(run.csim.relay())
    run.csim.observe(run.hub_chain_id, "idle_ledger", lane.idle_ledger())
    run.csim.observe(
        run.hub_chain_id, "pending_transfers", lane.pending_transfer_count()
    )
    run.csim.observe(
        run.hub_chain_id, "settled_after_return", lane.settled_remote_balance()
    )
    run.csim.observe(run.spoke_chain_id, "observation_after_return", lane.observation())
    results = run.csim.relay()
    assert_relay_success(results)
    idle = results[run.hub_chain_id].get("idle_ledger")
    log.info(
        "%s: recalled %s, %s arrived on the hub", run.spoke.name, remote_idle, idle
    )
    assert remote_idle * 98 // 100 <= idle <= remote_idle
    assert results[run.hub_chain_id].get("pending_transfers") == 0
    # The spoke vault's deposit/redeem rounding stays in the settled bucket
    # until the next attestation re-marks it; the recall debits only what the
    # dispatcher actually returned.
    assert (
        results[run.hub_chain_id].get("settled_after_return") == credited - remote_idle
    )
    assert results[run.spoke_chain_id].get("observation_after_return").tracked_idle == 0
    return idle


def _claim(run: Run, idle: int) -> None:
    lane = run.lane
    run.hub.execute([lane.claim(idle)])
    run.csim.observe(
        run.hub_chain_id, "vault_usdc_after", run.usdc_hub.balance_of(run.dep.vault)
    )
    run.csim.observe(
        run.hub_chain_id,
        "executor_usdc_after",
        run.usdc_hub.balance_of(lane.executor_address),
    )
    run.csim.observe(run.hub_chain_id, "idle_after_claim", lane.idle_ledger())
    results = run.csim.relay()
    assert_relay_success(results)
    vault_before = results[run.hub_chain_id].get("vault_usdc_before")
    assert (
        results[run.hub_chain_id].get("vault_usdc_after")
        == vault_before - run.amount + idle
    )
    assert results[run.hub_chain_id].get("executor_usdc_after") == 0
    assert results[run.hub_chain_id].get("idle_after_claim") == 0


@pytest.mark.parametrize(("dep", "spoke", "transport_kind"), LIFECYCLES)
def test_simulate_crosschain_lifecycle(
    request, dep: Deployment, spoke: Spoke, transport_kind
):
    spoke.chain.require_available()
    web3_hub = request.getfixturevalue(dep.hub.web3_fixture)
    web3_spoke = request.getfixturevalue(spoke.chain.web3_fixture)
    run = _run(web3_hub, web3_spoke, dep, spoke, transport_kind)
    credited = _supply(run)
    _attest(run, credited)
    shares = _deposit(run, credited)
    idle = _redeem_and_recall(run, shares, credited)
    _claim(run, idle)
    log.info(
        "%s/%s: relayed %d messages",
        transport_kind.name,
        spoke.name,
        len(run.csim.delivered),
    )

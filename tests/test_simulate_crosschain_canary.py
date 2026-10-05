"""The live Arbitrum -> HyperEVM USDC canaries as regression fixtures.

``CANARY`` and ``CANARY_V3`` (``_crosschain.py``) are the deployments the
canary runner drove end to end on 2026-10-02, on the pilot-v2 and the v3 CCIP
factory pair: one executor with its dispatcher at the same address on
HyperEVM, a 1 USDC round trip credited in full both ways. Pinned right after its last transaction, with every bucket at
zero, it is driven through the same ``prepare_deployed_run`` / ``run_lifecycle``
path as the Ethereum POC, then held to the gates the live run ended on and to
the gas limits of the routes its messages travel. Live on v2, every non-token
hub -> HyperEVM delivery needed a manual ``OffRamp.execute`` (Chainlink's
executors there submit in 3 M small blocks, the v2 route stamps 6 M); on v3
(2 M) they executed automatically. The relay delivers directly, so that
constraint is the readiness probe's; this test holds every delivery to the
route limit it was sent with.
"""

from __future__ import annotations

import pytest
from _crosschain import CANARY, CANARY_V3, Deployment, Spoke
from _crosschain_lifecycle import Run, prepare_deployed_run, run_lifecycle

from ipor_fusion import CcipLane

CANARY_LIFECYCLES = [
    pytest.param(
        canary,
        spoke,
        transport_kind,
        id=f"{factory}-ccip-{canary.hub.name}-to-{spoke.name}",
    )
    for factory, canary in (("v2", CANARY), ("v3", CANARY_V3))
    for spoke in canary.spokes
    for transport_kind in spoke.transport_kinds
]


@pytest.mark.parametrize(("dep", "spoke", "transport_kind"), CANARY_LIFECYCLES)
def test_simulate_canary_lifecycle(
    request, dep: Deployment, spoke: Spoke, transport_kind
):
    web3_hub = request.getfixturevalue(dep.hub.web3_fixture)
    web3_spoke = request.getfixturevalue(spoke.chain.web3_fixture)
    run = prepare_deployed_run(web3_hub, web3_spoke, dep, spoke, transport_kind)
    run_lifecycle(run)
    _assert_final_gates(run)
    _assert_deliveries_within_route_limits(run)


def _assert_final_gates(run: Run) -> None:
    """The state the live canary ended on: nothing in flight or pending on
    either side, every bucket at zero, the market refreshable to zero and the
    executor holding no asset."""
    lane = run.lane
    hub, spoke = run.hub_chain_id, run.spoke_chain_id
    run.hub.add_call(
        run.vault.update_markets_balances([run.market_id]),
        from_=run.owner,
        label="update_balances_final",
    )
    gates = {
        "settled_final": lane.settled_remote_balance(),
        "idle_final": lane.idle_ledger(),
        "outbound_final": lane.outbound_in_flight(),
        "pending_final": lane.pending_transfer_count(),
        "active_final": lane.has_active_command(),
        "nav_final": lane.get_balance(),
        "market_final": run.vault.total_assets_in_market(run.market_id),
        "executor_asset_final": run.hub_asset.balance_of(lane.executor_address),
    }
    for label, call in gates.items():
        run.csim.observe(hub, label, call)
    run.csim.observe(spoke, "observation_final", lane.observation())
    run.csim.observe(
        spoke, "remote_shares_final", run.remote_vault.balance_of(lane.executor_address)
    )
    results = run.relay()
    observed = {label: results[hub].get(label) for label in gates}
    observation = results[spoke].get("observation_final")
    run.log(
        "final gates",
        **observed,
        remote_idle=observation.tracked_idle,
        remote_shares=results[spoke].get("remote_shares_final"),
    )
    assert observed == dict.fromkeys(gates, 0) | {"active_final": False}
    assert (observation.tracked_idle, observation.accounted_balance) == (0, 0)
    assert results[spoke].get("remote_shares_final") == 0


def _assert_deliveries_within_route_limits(run: Run) -> None:
    """Every delivery ran within the gas limit stamped on it: the token gas
    limit on a token leg, the message gas limit on a command, acknowledgement
    or settlement receipt, from the route its sender holds (the executor's
    for hub -> spoke, the dispatcher's for spoke -> hub). The relay calls
    ``ccipReceive`` directly, so ``gas_used`` is the receiver's own handling;
    the OffRamp's overhead on top of it (about 0.16 M measured live) is not
    part of this bound."""
    lane = run.lane
    assert isinstance(lane, CcipLane)
    hub, spoke = run.hub_chain_id, run.spoke_chain_id
    run.csim.observe(hub, "route_hub", lane.executor.ccip_route(spoke))
    run.csim.observe(spoke, "route_spoke", lane.dispatcher.ccip_route(hub))
    results = run.relay()
    routes = {
        spoke: results[hub].get("route_hub"),
        hub: results[spoke].get("route_spoke"),
    }
    assert run.csim.delivered, "no message was delivered"
    for message in run.csim.delivered:
        route = routes[message.dst_chain_id]
        limit = (
            route.token_gas_limit if message.token_amount else route.message_gas_limit
        )
        label = f"ccip_receive:{message.message_id.hex()[:8]}"
        delivery = next(
            call for call in results[message.dst_chain_id].calls if call.label == label
        )
        run.log(
            "delivery gas",
            dst_chain_id=message.dst_chain_id,
            token_amount=message.token_amount,
            gas_used=delivery.gas_used,
            limit=limit,
        )
        assert 0 < delivery.gas_used <= limit

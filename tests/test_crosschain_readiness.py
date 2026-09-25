"""Live readiness of every spoke, read on the bridge and IPOR contracts: what
must be true before the lifecycle can run there. Base and Arbitrum pass every
check; HyperEVM is the next spoke and each check it still fails is a strict
xfail with the reason, so the test fails loudly the day that piece lands
(Chainlink opening USDC on the lane, the factory's route policy, the
contracts on HyperEVM)."""

from __future__ import annotations

import pytest
from _crosschain import CHAINS, POC, Chain, Spoke
from web3 import Web3

from ipor_fusion import (
    CcipCrosschainExecutor,
    CcipCrosschainFactory,
    CrosschainTransportKind,
    StargateCrosschainFactory,
    StargateTokenMessaging,
    Web3Context,
    ccip_token_lane,
)

HUB = CHAINS["ethereum"]
LIVE_SPOKES = [pytest.param(spoke, id=spoke.name) for spoke in POC.spokes]
(HYPEREVM,) = POC.planned_spokes


def _spokes(hyperevm_reason: str | None) -> list:
    """Every spoke; HyperEVM as a strict xfail when the check is known to fail
    there, as a plain param when it already passes."""
    marks = (
        (pytest.mark.xfail(strict=True, reason=hyperevm_reason),)
        if hyperevm_reason
        else ()
    )
    return [*LIVE_SPOKES, pytest.param(HYPEREVM, id=HYPEREVM.name, marks=marks)]


def _ctx(request, chain: Chain) -> Web3Context:
    return Web3Context(
        web3=request.getfixturevalue(chain.web3_fixture), chain_id=chain.chain_id
    )


def _usdc_lane(request, src: Chain, dst: Chain):
    return ccip_token_lane(
        _ctx(request, src),
        Web3.to_checksum_address(src.ccip_router),
        Web3.to_checksum_address(src.usdc),
        dst.chain_selector,
    )


@pytest.mark.parametrize("spoke", _spokes(None))
def test_ccip_message_lanes_exist_both_ways(request, spoke: Spoke):
    out, back = (
        _usdc_lane(request, HUB, spoke.chain),
        _usdc_lane(request, spoke.chain, HUB),
    )
    assert out.message_lane, f"no CCIP lane {HUB.name} -> {spoke.name}"
    assert back.message_lane, f"no CCIP lane {spoke.name} -> {HUB.name}"
    # The transport decodes MessageV1 as OnRamp 2.0.0 emits it.
    for lane in (out, back):
        assert lane.on_ramp_version is not None
        assert lane.on_ramp_version.startswith("OnRamp 2."), lane.on_ramp_version


@pytest.mark.parametrize(
    "spoke",
    _spokes("USDC has no CCIP pool for HyperEVM yet; announced for 2026-09-30"),
)
def test_ccip_usdc_travels_both_ways(request, spoke: Spoke):
    for src, dst in ((HUB, spoke.chain), (spoke.chain, HUB)):
        lane = _usdc_lane(request, src, dst)
        assert lane.pool is not None, f"USDC has no CCIP pool on {src.name}"
        assert lane.token_lane, (
            f"USDC pool {lane.pool} ({lane.pool_version}) on {src.name} "
            f"does not serve {dst.name}"
        )


@pytest.mark.parametrize(
    "spoke",
    _spokes("the hub CCIP factory has no timelocked route policy for HyperEVM"),
)
def test_hub_ccip_factory_route_policy(request, spoke: Spoke):
    factory = CcipCrosschainFactory(
        _ctx(request, HUB), POC.factory(CrosschainTransportKind.CHAINLINK_CCIP)
    )
    route = factory.ccip_route(spoke.chain_id).call()
    assert route.enabled, f"factory route for {spoke.name} disabled"
    assert route.chain_selector == spoke.chain.chain_selector


@pytest.mark.parametrize(
    "spoke",
    _spokes("the hub CCIP executor has no route registered for HyperEVM"),
)
def test_hub_ccip_executor_route(request, spoke: Spoke):
    executor = CcipCrosschainExecutor(
        _ctx(request, HUB), POC.executor(CrosschainTransportKind.CHAINLINK_CCIP)
    )
    route = executor.ccip_route(spoke.chain_id).call()
    assert route.enabled and route.chain_selector == spoke.chain.chain_selector
    assert executor.has_dispatcher(spoke.chain_id).call()


@pytest.mark.parametrize(
    "spoke",
    _spokes("Stargate has no route from Ethereum to HyperEVM (eid 30367)"),
)
def test_stargate_route_and_hub_factory_eid(request, spoke: Spoke):
    ctx = _ctx(request, HUB)
    peer = StargateTokenMessaging(ctx, checksum(HUB.token_messaging)).peers(
        spoke.chain.eid
    )
    assert peer.call() != bytes(32), f"no Stargate route to {spoke.name}"
    factory = StargateCrosschainFactory(
        ctx, POC.factory(CrosschainTransportKind.STARGATE_LAYERZERO)
    )
    assert factory.eid_of(spoke.chain_id).call() == spoke.chain.eid


@pytest.mark.parametrize(
    "spoke",
    _spokes("no crosschain factory or dispatcher is deployed on HyperEVM"),
)
def test_contracts_exist_on_the_spoke(request, spoke: Spoke):
    web3 = request.getfixturevalue(spoke.chain.web3_fixture)
    for transport_kind in spoke.transport_kinds:
        for role, address in (
            ("factory", POC.factory(transport_kind)),
            ("dispatcher", POC.executor(transport_kind)),
        ):
            code = web3.eth.get_code(Web3.to_checksum_address(address))
            assert len(code) > 0, (
                f"{transport_kind.name} {role} has no code on {spoke.name}"
            )


def checksum(address: str):
    return Web3.to_checksum_address(address)

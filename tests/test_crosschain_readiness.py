"""Live readiness of the CCIP lanes between the hub and every spoke, read on
Chainlink's own contracts: the message lane exists in both directions, runs
the OnRamp generation the SDK decodes, and USDC has a pool that serves the
other chain. A spoke whose USDC lane is not open yet is a strict xfail: the
day Chainlink opens it, the test fails and says so."""

from __future__ import annotations

import pytest
from _crosschain import CHAINS, Chain
from web3 import Web3

from ipor_fusion import Web3Context, ccip_token_lane

HUB = CHAINS["ethereum"]
USDC_ON_CCIP_PENDING = pytest.mark.xfail(
    strict=True,
    reason="USDC has no CCIP pool for HyperEVM yet; announced for 2026-09-30",
)


def _pair(src: Chain, dst: Chain, *marks):
    return pytest.param(src, dst, id=f"{src.name}-to-{dst.name}", marks=marks)


LANES = [
    _pair(HUB, CHAINS["base"]),
    _pair(CHAINS["base"], HUB),
    _pair(HUB, CHAINS["arbitrum"]),
    _pair(CHAINS["arbitrum"], HUB),
    _pair(HUB, CHAINS["hyperevm"], USDC_ON_CCIP_PENDING),
    _pair(CHAINS["hyperevm"], HUB, USDC_ON_CCIP_PENDING),
]


@pytest.mark.parametrize(("src", "dst"), LANES)
def test_ccip_usdc_lane_is_open(request, src: Chain, dst: Chain):
    web3 = request.getfixturevalue(src.web3_fixture)
    ctx = Web3Context(web3=web3, chain_id=src.chain_id)
    lane = ccip_token_lane(
        ctx,
        Web3.to_checksum_address(src.ccip_router),
        Web3.to_checksum_address(src.usdc),
        dst.chain_selector,
    )
    assert lane.message_lane, f"no CCIP lane {src.name} -> {dst.name}"
    # The transport decodes MessageV1 as OnRamp 2.0.0 emits it.
    assert lane.on_ramp_version is not None
    assert lane.on_ramp_version.startswith("OnRamp 2."), lane.on_ramp_version
    assert lane.pool is not None, f"USDC has no CCIP pool on {src.name}"
    assert lane.token_lane, (
        f"USDC pool {lane.pool} ({lane.pool_version}) on {src.name} "
        f"does not serve {dst.name}"
    )

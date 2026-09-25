"""Offline tests for the Chainlink CCIP reads behind ``ccip_token_lane``."""

from __future__ import annotations

from unittest.mock import MagicMock

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector as selector
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion.crosschain import CcipTokenLane, ccip_token_lane

ROUTER = Web3.to_checksum_address("0x80226fc0Ee2b096224EeAc085Bb9a8cba1146f7D")
ON_RAMP = Web3.to_checksum_address("0xc3423f3f3f3f3f3f3f3f3f3f3f3f3f3f3f3f3f3f")
REGISTRY = Web3.to_checksum_address("0xb22764f98dd05c789929716d677382df22c05cb6")
POOL = Web3.to_checksum_address("0xf70b4b6ec7adb8822b23119c844729e9b1b1683d")
RMN = Web3.to_checksum_address("0x411de17f12d1a34ecc7f45f49844626267c75e81")
USDC = Web3.to_checksum_address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
HYPEREVM = 2442541497099098535


def _ctx(answers: dict[tuple[str, bytes], bytes]) -> MagicMock:
    ctx = MagicMock()

    def call(to, data, block=None):
        key = (to, bytes(data)[:4])
        if key in answers:
            return answers[key]
        raise ContractLogicError("execution reverted")

    ctx.call.side_effect = call
    return ctx


def _open_lane(pool: str | None, pool_serves: bool) -> dict[tuple[str, bytes], bytes]:
    answers: dict[tuple[str, bytes], bytes] = {
        (ROUTER, selector("isChainSupported(uint64)")): encode(["bool"], [True]),
        (ROUTER, selector("getOnRamp(uint64)")): encode(["address"], [ON_RAMP]),
        (ON_RAMP, selector("typeAndVersion()")): encode(["string"], ["OnRamp 2.0.0"]),
        (ON_RAMP, selector("getStaticConfig()")): encode(
            ["uint64", "address", "uint256", "address"],
            [HYPEREVM, RMN, 1_000_000, REGISTRY],
        ),
        (REGISTRY, selector("getPool(address)")): encode(
            ["address"], [pool or "0x" + "00" * 20]
        ),
    }
    if pool:
        answers[(pool, selector("typeAndVersion()"))] = encode(
            ["string"], ["USDCTokenPoolProxy 2.0.0"]
        )
        answers[(pool, selector("isSupportedChain(uint64)"))] = encode(
            ["bool"], [pool_serves]
        )
    return answers


def test_no_message_lane_stops_at_the_router():
    ctx = _ctx(
        {(ROUTER, selector("isChainSupported(uint64)")): encode(["bool"], [False])}
    )
    assert ccip_token_lane(ctx, ROUTER, USDC, HYPEREVM) == CcipTokenLane(
        HYPEREVM, message_lane=False
    )
    assert ctx.call.call_count == 1


def test_lane_without_a_pool_for_the_token():
    ctx = _ctx(_open_lane(pool=None, pool_serves=False))
    assert ccip_token_lane(ctx, ROUTER, USDC, HYPEREVM) == CcipTokenLane(
        HYPEREVM, True, ON_RAMP, "OnRamp 2.0.0"
    )


def test_pool_that_does_not_serve_the_destination():
    ctx = _ctx(_open_lane(pool=POOL, pool_serves=False))
    lane = ccip_token_lane(ctx, ROUTER, USDC, HYPEREVM)
    assert (lane.pool, lane.pool_version, lane.token_lane) == (
        POOL,
        "USDCTokenPoolProxy 2.0.0",
        False,
    )


def test_open_token_lane():
    ctx = _ctx(_open_lane(pool=POOL, pool_serves=True))
    lane = ccip_token_lane(ctx, ROUTER, USDC, HYPEREVM)
    assert lane.message_lane and lane.token_lane
    assert lane.on_ramp == ON_RAMP
    # the selector arguments are the destination selector
    sent = [bytes(c.args[1]) for c in ctx.call.call_args_list]
    assert sent[0][4:] == encode(["uint64"], [HYPEREVM])
    assert sent[-1][4:] == encode(["uint64"], [HYPEREVM])

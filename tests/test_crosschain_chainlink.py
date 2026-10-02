"""Offline tests for the Chainlink CCIP reads behind ``ccip_token_lane``."""

from __future__ import annotations

import re
from unittest.mock import MagicMock

import pytest
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


def _open_lane(
    pool: str | None,
    pool_serves: bool,
    on_ramp_version: str = "OnRamp 2.0.0",
) -> dict[tuple[str, bytes], bytes]:
    answers: dict[tuple[str, bytes], bytes] = {
        (ROUTER, selector("isChainSupported(uint64)")): encode(["bool"], [True]),
        (ROUTER, selector("getOnRamp(uint64)")): encode(["address"], [ON_RAMP]),
        (ON_RAMP, selector("typeAndVersion()")): encode(["string"], [on_ramp_version]),
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


@pytest.mark.parametrize(
    "version",
    [
        "EVM2EVMOnRamp 1.5.0",
        "OnRamp 1.6.0",
        "OnRamp 2.1.3",
        "OnRamp 3.0.0",
        "OnRamp 20.0.0",
        "",
        "OnRamp",
    ],
)
def test_unsupported_on_ramp_version_stops_before_static_config(version):
    ctx = _ctx(_open_lane(pool=None, pool_serves=False, on_ramp_version=version))
    message = (
        f"unsupported CCIP OnRamp version {version!r} at {ON_RAMP}; "
        "ccip_token_lane supports OnRamp 2.0.0"
    )

    with pytest.raises(ValueError, match=re.escape(message)):
        ccip_token_lane(ctx, ROUTER, USDC, HYPEREVM)

    assert ctx.call.call_count == 3
    called_selectors = {bytes(call.args[1])[:4] for call in ctx.call.call_args_list}
    assert selector("getStaticConfig()") not in called_selectors


# --- OffRamp 2.0.0, the verifier resolver and the committee verifier ----------

OFF_RAMP = Web3.to_checksum_address("0x99bf17a320a981710f9b53c0c0b27219c1121d8d")
RESOLVER = Web3.to_checksum_address("0x2CaAfd3B4Cf606220580c885Bd2B448FB93dC03b")
COMMITTEE = Web3.to_checksum_address("0x35d59e7bd6e607f28a62bf93524663f504b04a3c")
MESSAGE_ID = bytes.fromhex(
    "0bb7cd4dd3b756d8d7d97590db208df08435ca50c8a14dce248671002cca10bc"
)
ENCODED = b"\x01" + bytes(range(40))
CCV_DATA = bytes.fromhex("e9a05a20") + (2).to_bytes(2, "big") + b"\xab\xcd"


def test_off_ramp_reads_decode_to_typed_values():
    from ipor_fusion import CcipOffRamp, CcvRequirements, MessageExecutionState

    ctx = _ctx(
        {
            (OFF_RAMP, selector("getExecutionState(bytes32)")): encode(["uint8"], [2]),
            (OFF_RAMP, selector("getCCVsForMessage(bytes)")): encode(
                ["address[]", "address[]", "uint8"], [[RESOLVER], [COMMITTEE], 1]
            ),
            (OFF_RAMP, selector("typeAndVersion()")): encode(
                ["string"], ["OffRamp 2.0.0"]
            ),
        }
    )
    off_ramp = CcipOffRamp(ctx, OFF_RAMP)

    assert off_ramp.type_and_version().call() == "OffRamp 2.0.0"
    assert off_ramp.execution_state(MESSAGE_ID).call() is MessageExecutionState.SUCCESS
    assert off_ramp.ccvs_for_message(ENCODED).call() == CcvRequirements(
        (RESOLVER,), (COMMITTEE,), 1
    )


def test_off_ramp_execute_encodes_one_result_per_ccv():
    from ipor_fusion import CcipOffRamp

    off_ramp = CcipOffRamp(_ctx({}), OFF_RAMP)

    call = off_ramp.execute(ENCODED, [RESOLVER], [CCV_DATA])

    assert call.to == OFF_RAMP
    assert call.data[:4] == selector("execute(bytes,address[],bytes[],uint32)")
    from eth_abi import decode

    assert decode(["bytes", "address[]", "bytes[]", "uint32"], call.data[4:]) == (
        ENCODED,
        (RESOLVER.lower(),),
        (CCV_DATA,),
        0,
    )
    overridden = off_ramp.execute(ENCODED, [RESOLVER], [CCV_DATA], 7_000_000)
    assert decode(["bytes", "address[]", "bytes[]", "uint32"], overridden.data[4:])[
        3
    ] == (7_000_000)
    with pytest.raises(ValueError, match="one verifier result per CCV"):
        off_ramp.execute(ENCODED, [RESOLVER], [])
    with pytest.raises(ValueError, match="one verifier result per CCV"):
        off_ramp.execute(ENCODED, [], [])
    with pytest.raises(ValueError, match="must not be empty"):
        off_ramp.execute(b"", [RESOLVER], [CCV_DATA])
    with pytest.raises(ValueError, match="uint32"):
        off_ramp.execute(ENCODED, [RESOLVER], [CCV_DATA], 2**32)


def test_resolver_and_committee_verifier_reads():
    from ipor_fusion import CcipCommitteeVerifier, CcipVerifierResolver

    ctx = _ctx(
        {
            (RESOLVER, selector("getInboundImplementation(bytes)")): encode(
                ["address"], [COMMITTEE]
            ),
            (RESOLVER, selector("getAllInboundImplementations()")): encode(
                ["(bytes4,address)[]"], [[(bytes.fromhex("e9a05a20"), COMMITTEE)]]
            ),
            (COMMITTEE, selector("versionTag()")): encode(
                ["bytes4"], [bytes.fromhex("e9a05a20")]
            ),
            (COMMITTEE, selector("getStorageLocations()")): encode(
                ["string[]"],
                [["aggregator-1.ccip.chain.link", "aggregator-2.ccip.chain.link"]],
            ),
            (COMMITTEE, selector("typeAndVersion()")): encode(
                ["string"], ["CommitteeVerifier 2.0.0"]
            ),
        }
    )
    resolver = CcipVerifierResolver(ctx, RESOLVER)
    committee = CcipCommitteeVerifier(ctx, COMMITTEE)

    assert resolver.inbound_implementation(CCV_DATA).call() == COMMITTEE
    assert resolver.all_inbound_implementations().call() == (
        (bytes.fromhex("e9a05a20"), COMMITTEE),
    )
    assert committee.type_and_version().call() == "CommitteeVerifier 2.0.0"
    assert committee.version_tag().call() == bytes.fromhex("e9a05a20")
    assert committee.storage_locations().call() == (
        "aggregator-1.ccip.chain.link",
        "aggregator-2.ccip.chain.link",
    )

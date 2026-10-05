"""The HyperCore event mirror: topics pinned to the run's receipts, decoding,
and the receipt shape of every fuse action in the HIP-3 flow fixture."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from eth_abi import encode
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion import (
    HYPERCORE_EVENTS,
    HyperCoreEvent,
    decode_hypercore_event,
    find_hypercore_events,
    hypercore_event_name,
    hypercore_events,
)

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "hypercore_hip3_flow.json").read_text()
)
VAULT = Web3.to_checksum_address(FIXTURE["vault"])
CREATED = {
    row["contract"]: Web3.to_checksum_address(row["created"])
    for row in FIXTURE["txs"]
    if row["kind"] == "create"
}

# topic0 of the HyperCore events the run's receipts carry (log_topics in the
# fixture), by the step that emitted them.
OBSERVED_TOPICS = {
    "HyperCoreActionEnqueued": "0xc744297f13e13026567ca438c2875ca5df339e35cce57028b712b54bd368d636",
    "HyperCoreDepositFuseEnter": "0x01dffe96335727c004f22bd1070c4f0d772f04e784454c3928a3252e7c31edfd",
}
FUSE_EVENT_BY_STEP = {
    "DepositFuse": "HyperCoreDepositFuseEnter",
    "SendFuse": "HyperCoreSendFuseSendAsset",
    "OrderFuse": "HyperCoreOrderFuseEnter",
    "CancelFuse": "HyperCoreCancelFuseCancelByCloid",
}


def test_topics_match_the_receipts_of_the_run():
    for name, topic in OBSERVED_TOPICS.items():
        assert hypercore_event_name(topic) == name
        assert HYPERCORE_EVENTS[bytes.fromhex(topic[2:])].name == name
    assert hypercore_event_name(b"\x00" * 32) is None


def _fuse_action_rows() -> list[dict]:
    return [
        row
        for row in FIXTURE["txs"]
        if row["kind"] == "call"
        and any(step in row["step"] for step in FUSE_EVENT_BY_STEP)
    ]


@pytest.mark.parametrize("row", _fuse_action_rows(), ids=lambda r: f"n{r['nonce']}")
def test_every_fuse_action_enqueued_one_action_and_emitted_its_fuse_event(row):
    vault_topics = [
        hypercore_event_name(topic)
        for topic, emitter in zip(row["log_topics"], row["log_emitters"], strict=True)
        if Web3.to_checksum_address(emitter) == VAULT
    ]
    hypercore = [name for name in vault_topics if name]
    step = next(step for step in FUSE_EVENT_BY_STEP if step in row["step"])
    assert hypercore == [
        FUSE_EVENT_BY_STEP[step],
        "HyperCoreActionEnqueued",
    ] or hypercore == [
        "HyperCoreActionEnqueued",
        FUSE_EVENT_BY_STEP[step],
    ]


def _log(topic: bytes, data: bytes, address=VAULT, index=0) -> dict:
    return {
        "address": address,
        "topics": [HexBytes(topic)],
        "data": HexBytes(data),
        "logIndex": index,
    }


def test_decodes_fuse_and_library_events_from_data_alone():
    deposit = HYPERCORE_EVENTS[
        bytes.fromhex(OBSERVED_TOPICS["HyperCoreDepositFuseEnter"][2:])
    ]
    enqueued = HYPERCORE_EVENTS[
        bytes.fromhex(OBSERVED_TOPICS["HyperCoreActionEnqueued"][2:])
    ]
    fuse = CREATED["HyperCoreDepositFuse"]
    usdc = Web3.to_checksum_address(FIXTURE["usdc"])
    receipt = {
        "logs": [
            _log(b"\x11" * 32, b"", index=0),  # not ours
            _log(
                deposit.topic,
                encode(
                    [t for t, _ in deposit.params],
                    [fuse, 0, usdc, 15_000_000, 1_500_000_000],
                ),
                index=1,
            ),
            _log(
                enqueued.topic,
                encode(
                    [t for t, _ in enqueued.params],
                    [
                        7,
                        1,
                        0,
                        1_790_000_002,
                        10**16,
                        1_164_341_828,
                        47_111_884,
                        13,
                        b"\x42" * 32,
                    ],
                ),
                index=2,
            ),
        ]
    }
    events = hypercore_events(receipt)  # type: ignore[arg-type]
    assert [e.name for e in events] == [
        "HyperCoreDepositFuseEnter",
        "HyperCoreActionEnqueued",
    ]
    deposit_event, enqueued_event = events
    assert isinstance(deposit_event, HyperCoreEvent)
    assert deposit_event.fuse == fuse and deposit_event.emitter == VAULT
    assert deposit_event.args["evmAsset"] == usdc
    assert (deposit_event.args["amount"], deposit_event.args["coreWei"]) == (
        15_000_000,
        1_500_000_000,
    )
    assert enqueued_event.fuse is None
    assert enqueued_event.args["nonce"] == 7 and enqueued_event.args["actionId"] == 13
    assert enqueued_event.args["payloadHash"] == b"\x42" * 32
    assert enqueued_event.log_index == 2
    assert find_hypercore_events(receipt, "HyperCoreActionEnqueued") == [enqueued_event]  # type: ignore[arg-type]
    assert decode_hypercore_event(receipt["logs"][0]) is None  # type: ignore[arg-type]
    with pytest.raises(ValueError, match="unknown HyperCore event"):
        find_hypercore_events(receipt, "Transfer")  # type: ignore[arg-type]


def test_every_spec_has_a_distinct_topic():
    assert len(HYPERCORE_EVENTS) == 16

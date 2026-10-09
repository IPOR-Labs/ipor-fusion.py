"""The ExternalState event registry against the Solidity declarations at the
pinned upstream commit (fetched, not derived from the Python list), and the
decoder's identity, strictness and unknown-topic behavior."""

import re

import pytest
from eth_abi import encode
from test_solidity_mirrors import _solidity_source
from web3 import Web3

from ipor_fusion import (
    EXTERNAL_STATE_EVENT_SPECS,
    EXTERNAL_STATE_TOPICS,
    ExternalStateEventDecodeError,
    decode_external_state_event,
    external_state_event_name,
    external_state_events,
)

_SOURCES = (
    "contracts/fuses/external_state/ExternalStateBalanceFuse.sol",
    "contracts/fuses/external_state/ExternalStateUnpauseFuse.sol",
    "contracts/fuses/external_state/ExternalStateRescueFuse.sol",
    "contracts/fuses/external_state/ExternalStateOperationFuse.sol",
    "contracts/fuses/external_state/lib/ExternalStateExecutorStorageLib.sol",
)
_EVENT_RE = re.compile(r"\bevent\s+(\w+)\s*\(([^)]*)\)\s*;", re.S)

VAULT = "0xdBEe6621Febce4C51Fa9DD1dB12dCd9d4CC3e7b9"
SIGNER = "0x00000000000000000000000000000000000000a5"


def _declared():
    out = {}
    for path in _SOURCES:
        for name, body in _EVENT_RE.findall(_solidity_source(path)):
            params = []
            for raw in [p.strip() for p in body.split(",") if p.strip()]:
                words = raw.split()
                assert "indexed" not in words, f"{name}: indexed parameter {raw!r}"
                params.append((words[0], words[-1]))
            out[name] = tuple(params)
    assert len(out) >= 9, "the event regex stopped matching the sources"
    return out


def test_the_registry_mirrors_every_declared_event_exactly():
    declared = _declared()
    assert {s.name: s.params for s in EXTERNAL_STATE_EVENT_SPECS} == declared


def test_topics_are_distinct_and_hex():
    assert len(set(EXTERNAL_STATE_TOPICS)) == len(EXTERNAL_STATE_EVENT_SPECS)
    assert all(t.startswith("0x") and len(t) == 66 for t in EXTERNAL_STATE_TOPICS)


def _log(name, values, **over):
    spec = next(s for s in EXTERNAL_STATE_EVENT_SPECS if s.name == name)
    log = {
        "address": VAULT,
        "topics": [spec.topic],
        "data": encode([t for t, _ in spec.params], values),
        "blockNumber": 47_978_867,
        "transactionHash": bytes.fromhex("fa" * 32),
        "logIndex": 3,
    }
    log.update(over)
    return log


def test_unpause_decodes_signer_total_nonce_and_the_raw_identity():
    event = decode_external_state_event(
        _log("ExternalStateUnpaused", (SIGNER, 32_928_135, 7))
    )
    assert event is not None
    assert event.name == "ExternalStateUnpaused"
    assert event.emitter == VAULT
    assert event.args == {
        "signer": Web3.to_checksum_address(SIGNER),
        "confirmedTotalBalance": 32_928_135,
        "nonce": 7,
    }
    assert (event.block_number, event.log_index) == (47_978_867, 3)
    assert event.transaction_hash == "0x" + "fa" * 32


def test_hex_text_data_and_topics_from_an_indexer_decode_too():
    log = _log("ExternalStateAssetRescued", (SIGNER,))
    log["data"] = "0x" + log["data"].hex()
    log["transactionHash"] = "0x" + "fb" * 32
    event = decode_external_state_event(log)
    assert event is not None and event.args == {
        "asset": Web3.to_checksum_address(SIGNER)
    }
    assert event.transaction_hash == "0x" + "fb" * 32


def test_an_unknown_topic_is_none_and_a_malformed_listed_one_raises():
    assert (
        decode_external_state_event(
            {"address": VAULT, "topics": [b"\x11" * 32], "data": b""}
        )
        is None
    )
    assert (
        decode_external_state_event({"address": VAULT, "topics": [], "data": b""})
        is None
    )
    with pytest.raises(ExternalStateEventDecodeError):
        decode_external_state_event(
            _log("ExternalStateUnpaused", (SIGNER, 1, 2), data=b"\x00" * 31)
        )
    with pytest.raises(ExternalStateEventDecodeError, match="data bytes"):
        good = _log("ExternalStateAssetRescued", (SIGNER,))
        decode_external_state_event(dict(good, data=good["data"] + b"\x00" * 32))
    with pytest.raises(ExternalStateEventDecodeError, match="indexed"):
        good = _log("ExternalStateAssetRescued", (SIGNER,))
        decode_external_state_event(dict(good, topics=good["topics"] + [b"\x00" * 32]))


def test_both_executor_events_are_distinct_facts():
    created = decode_external_state_event(_log("ExecutorCreated", (SIGNER, 50)))
    deployed = decode_external_state_event(
        _log("ExternalStateExecutorDeployed", (SIGNER, 50))
    )
    assert created is not None and deployed is not None
    assert (created.name, deployed.name) == (
        "ExecutorCreated",
        "ExternalStateExecutorDeployed",
    )
    assert (
        external_state_event_name(EXTERNAL_STATE_TOPICS[0])
        == EXTERNAL_STATE_EVENT_SPECS[0].name
    )


def test_a_receipt_yields_its_events_in_log_order():
    receipt = {
        "logs": [
            _log(
                "ExternalStateBalanceFuseLastTotalBalanceUpdated",
                (288_466, 32_732_448),
                logIndex=1,
            ),
            {"address": VAULT, "topics": [b"\x22" * 32], "data": b"", "logIndex": 2},
            _log(
                "ExternalStateBigChangeDetected",
                (288_466, 32_732_448, 50_000),
                logIndex=3,
            ),
        ]
    }
    assert [e.name for e in external_state_events(receipt)] == [
        "ExternalStateBalanceFuseLastTotalBalanceUpdated",
        "ExternalStateBigChangeDetected",
    ]

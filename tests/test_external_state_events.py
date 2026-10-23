"""The ExternalState event registry against the Solidity declarations at the
pinned upstream commit (fetched, not derived from the Python list), and the
decoder's identity, strictness and unknown-topic behavior."""

import re

import pytest
from _event_args import assert_event_class_mirrors_spec
from eth_abi import encode
from test_solidity_mirrors import IPOR_FUSION_REF, _solidity_source
from web3 import Web3

from ipor_fusion import (
    EXTERNAL_STATE_EVENT_CLASSES,
    EXTERNAL_STATE_EVENT_SPECS,
    EXTERNAL_STATE_TOPICS,
    ExternalStateEventDecodeError,
    decode_external_state_event,
    external_state_event_name,
    external_state_events,
)
from ipor_fusion.fuses.events import abi_value

_SOURCES = (
    "contracts/fuses/external_state/ExternalStateBalanceFuse.sol",
    "contracts/fuses/external_state/ExternalStateUnpauseFuse.sol",
    "contracts/fuses/external_state/ExternalStateRescueFuse.sol",
    "contracts/fuses/external_state/ExternalStateOperationFuse.sol",
    "contracts/fuses/external_state/lib/ExternalStateExecutorStorageLib.sol",
)
# Both published builds: the release pinned in test_solidity_mirrors (Arbitrum)
# and the earlier one HyperEVM, Base and Ethereum still publish. The released
# balance fuse declares its events in a library of its own.
_EARLIER_REF = "a1f79d6c3d0d426ace4df55ed0fb8ea340580739"
_BUILDS = (
    (
        IPOR_FUSION_REF,
        (*_SOURCES, "contracts/fuses/external_state/lib/ExternalStateBigChangeLib.sol"),
    ),
    (_EARLIER_REF, _SOURCES),
)
_EVENT_RE = re.compile(r"\bevent\s+(\w+)\s*\(([^)]*)\)\s*;", re.S)

VAULT = "0xdBEe6621Febce4C51Fa9DD1dB12dCd9d4CC3e7b9"
SIGNER = "0x00000000000000000000000000000000000000a5"


def _declared():
    """Every event either build declares; a name both declare must agree."""
    out = {}
    for ref, paths in _BUILDS:
        for name, params in _declared_at(ref, paths).items():
            assert out.setdefault(name, params) == params, name
    return out


def _declared_at(ref, paths):
    out = {}
    for path in paths:
        for name, body in _EVENT_RE.findall(_solidity_source(path, ref)):
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


def test_a_short_transaction_hash_is_a_decode_error():
    log = _log("ExternalStateAssetRescued", (SIGNER,))
    log["transactionHash"] = bytes(31)
    with pytest.raises(ExternalStateEventDecodeError, match="transactionHash"):
        decode_external_state_event(log)


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


@pytest.mark.parametrize(
    ("change", "match"),
    [
        ({"data": "0xzz"}, "invalid hex"),
        ({"data": "00" * 32}, "without 0x prefix"),
        ({"data": 32}, "unsupported type int"),
        ({"data": None}, "unsupported type NoneType"),
        ({"blockNumber": "0xzz"}, "invalid quantity"),
        ({"logIndex": True}, "boolean"),
        ({"logIndex": 2.0}, "unsupported quantity"),
    ],
)
def test_a_recognized_topic_with_malformed_parts_raises_the_declared_error(
    change, match
):
    log = dict(_log("ExternalStateAssetRescued", (SIGNER,)), **change)
    with pytest.raises(ExternalStateEventDecodeError, match=match):
        decode_external_state_event(log)


@pytest.mark.parametrize(
    "address", [None, 12345, "0xnot-an-address"], ids=["missing", "int", "invalid"]
)
def test_a_recognized_topic_needs_an_emitter_address(address):
    log = dict(_log("ExternalStateAssetRescued", (SIGNER,)), address=address)
    with pytest.raises(ExternalStateEventDecodeError, match="emitter"):
        decode_external_state_event(log)


def test_missing_data_on_a_recognized_topic_raises():
    log = _log("ExternalStateAssetRescued", (SIGNER,))
    del log["data"]
    with pytest.raises(ExternalStateEventDecodeError, match="no data"):
        decode_external_state_event(log)


def test_an_unrecognizable_topic_is_simply_not_ours():
    assert (
        decode_external_state_event({"address": VAULT, "topics": [12345], "data": 32})
        is None
    )
    assert (
        decode_external_state_event(
            {"address": VAULT, "topics": ["0xzz"], "data": "0x"}
        )
        is None
    )


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


def test_a_full_raw_json_rpc_log_decodes_to_typed_identity():
    """Every field as JSON-RPC or the indexer serves it: hex topic, data, hash
    and quantities — the typed result has int identity."""
    spec = next(
        s for s in EXTERNAL_STATE_EVENT_SPECS if s.name == "ExternalStateAssetRescued"
    )
    raw = {
        "address": VAULT.lower(),
        "topics": ["0x" + spec.topic.hex()],
        "data": "0x" + encode(["address"], [SIGNER]).hex(),
        "blockNumber": "0x2dc1a73",
        "transactionHash": "0x" + "fb" * 32,
        "logIndex": "0x2",
    }
    event = decode_external_state_event(raw)
    assert event is not None
    assert event.args == {"asset": Web3.to_checksum_address(SIGNER)}
    assert (event.block_number, event.log_index) == (0x2DC1A73, 2)
    assert isinstance(event.block_number, int) and isinstance(event.log_index, int)
    assert event.transaction_hash == "0x" + "fb" * 32 and event.emitter == VAULT


def test_a_valid_unlisted_hex_topic_is_none():
    raw = {"address": VAULT, "topics": ["0x" + "11" * 32], "data": "0x"}
    assert decode_external_state_event(raw) is None


@pytest.mark.parametrize("spec", EXTERNAL_STATE_EVENT_SPECS, ids=lambda spec: spec.name)
def test_event_class_mirrors_the_spec(spec):
    assert_event_class_mirrors_spec(
        EXTERNAL_STATE_EVENT_CLASSES[spec.name], spec.name, spec.params
    )


def test_every_spec_has_an_event_class():
    assert set(EXTERNAL_STATE_EVENT_CLASSES) == {
        spec.name for spec in EXTERNAL_STATE_EVENT_SPECS
    }


@pytest.mark.parametrize(
    ("abi_type", "value"),
    [("address", 1), ("bool", 1), ("uint256", True), ("bytes32", "0x00")],
)
def test_a_value_of_the_wrong_python_type_is_rejected(abi_type, value):
    with pytest.raises(TypeError, match="decoded as"):
        abi_value(abi_type, value)

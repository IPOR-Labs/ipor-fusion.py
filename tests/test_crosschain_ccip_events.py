"""The CCIP event registry: every spec has a unique topic, the registry is
sorted, every spec round-trips through an ``eth_simulateV1``-shaped log, and
the executor readers encode and decode.

The registry is mirrored from a contracts checkout when
``IPOR_FUSION_CONTRACTS_DIR`` points at ``CONTRACTS_REVISION``.
"""

from __future__ import annotations

import os
import re
import shutil
import subprocess
from pathlib import Path
from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector, keccak
from web3 import Web3

from ipor_fusion import (
    CCIP_EVENT_TOPICS,
    CCIP_EVENTS,
    CcipCrosschainExecutor,
    CcipEvent,
    CcipEventSpec,
    ccip_events,
    decode_ccip_event,
    find_ccip_events,
)

ADDR = Web3.to_checksum_address("0x1d5c9d44f8d556ec7f557ae992401cc770937e6e")
PEER = Web3.to_checksum_address("0x0Aa75BfD30Ae2d4061FF8261453b933Ab5959991")
ROUTE = "(uint64,address,address,uint96,uint96,uint256,bool)"

_IDENTIFIER = re.compile(r"^[A-Za-z_]\w*$")
_ABI_TYPE = re.compile(r"^(u?int\d+|bytes32|address|bool|\((\w+,)*\w+\))$")

# Signatures whose field names matter to readers, with their fields.
KNOWN_FIELDS = {
    "ReturnFinalized(uint256,bytes32,uint256,uint256)": (
        "chainId",
        "operationId",
        "sentSD",
        "received",
    ),
    "LateReturnFinalized(uint256,bytes32,uint256,uint256)": (
        "chainId",
        "operationId",
        "sentSD",
        "received",
    ),
    "CommandAcknowledged(uint256,bytes32,uint64,uint64,int256,uint8,uint64)": (
        "srcChainId",
        "commandId",
        "sequence",
        "stateVersion",
        "valueDelta",
        "resolution",
        "commandConfigEpoch",
    ),
    "CommandSent(uint256,bytes32,uint64,uint8,bool,uint64)": (
        "dstChainId",
        "commandId",
        "sequence",
        "action",
        "advancesConfig",
        "commandConfigEpoch",
    ),
    "ProposalInvalidated(uint256,uint256,uint64)": (
        "chainId",
        "proposalId",
        "newEpoch",
    ),
    "AttestationAnchorUpdated(uint256,uint256)": ("chainId", "principalSD"),
    "ReturnDebitExceededAccountingBound(uint256,bytes32,uint256,uint256)": (
        "chainId",
        "operationId",
        "sentSD",
        "maxAccounted",
    ),
    "SettledCreditClamped(uint256,bytes32,uint256,uint256)": (
        "chainId",
        "operationId",
        "reported",
        "credited",
    ),
    "CcipMessageSent(bytes32,uint256,bool,uint256)": (
        "messageId",
        "dstChainId",
        "withToken",
        "fee",
    ),
    "ReturnBelowMinimumCredited(uint256,bytes32,uint256,uint256)": (
        "chainId",
        "operationId",
        "received",
        "minimum",
    ),
    "BalanceProposed(uint256,uint256,uint256,uint64,bytes32)": (
        "proposalId",
        "chainId",
        "balance",
        "stateVersion",
        "trackedPositionSetHash",
    ),
    "BalanceApproved(uint256,uint256,uint256)": ("proposalId", "chainId", "balance"),
    "AssetSettled(uint256,bytes32,uint256)": ("chainId", "operationId", "received"),
    "InboundSettled(bytes32,uint256,uint64)": ("operationId", "amount", "stateVersion"),
    f"CcipRoutePolicyUpdated(uint256,{ROUTE})": ("chainId", "route"),
    "DispatcherDeploymentGasLimitScheduled(uint256,uint96,uint256)": (
        "chainId",
        "gasLimit",
        "executableAt",
    ),
    "DispatcherDeploymentGasLimitExecuted(uint256,uint96)": ("chainId", "gasLimit"),
}

BY_SIGNATURE = {spec.signature: spec for spec in CCIP_EVENTS}


def _sample(abi_type: str) -> object:
    """A distinctive value of each ABI type; addresses are checksummed."""
    if abi_type == "address":
        return PEER
    if abi_type == "bytes32":
        return b"\x07" * 32
    if abi_type == "bool":
        return True
    if abi_type.startswith("int"):
        return -42
    if abi_type.startswith("("):
        return tuple(_sample(member) for member in abi_type[1:-1].split(","))
    return 200 if abi_type == "uint8" else 12345


def _log(spec: CcipEventSpec, values: tuple, *, as_bytes: bool = False) -> dict:
    data = encode(list(spec.types), list(values))
    if as_bytes:
        return {"address": ADDR.lower(), "topics": [spec.topic], "data": data}
    return {
        "address": ADDR.lower(),
        "topics": ["0x" + spec.topic.hex()],
        "data": "0x" + data.hex(),
    }


def _sample_log(signature: str) -> tuple[CcipEventSpec, tuple, dict]:
    spec = BY_SIGNATURE[signature]
    values = tuple(_sample(abi_type) for abi_type in spec.types)
    return spec, values, _log(spec, values)


def test_every_spec_parses_and_has_a_unique_topic():
    assert len(CCIP_EVENTS) == 72
    for spec in CCIP_EVENTS:
        assert _IDENTIFIER.fullmatch(spec.name), spec
        assert len(spec.fields) == len(spec.types), spec
        assert all(_IDENTIFIER.fullmatch(field) for field in spec.fields), spec
        assert all(_ABI_TYPE.fullmatch(abi_type) for abi_type in spec.types), spec
        assert spec.topic == keccak(text=spec.signature)
    topics = [spec.topic for spec in CCIP_EVENTS]
    assert len(set(topics)) == len(topics)


def test_registry_is_sorted_and_the_topic_map_follows_it():
    signatures = [spec.signature for spec in CCIP_EVENTS]
    assert signatures == sorted(signatures)
    assert len(set(signatures)) == len(signatures)
    assert list(CCIP_EVENT_TOPICS) == [spec.topic for spec in CCIP_EVENTS]
    assert list(CCIP_EVENT_TOPICS.values()) == list(CCIP_EVENTS)


@pytest.mark.parametrize(
    ("signature", "fields"), list(KNOWN_FIELDS.items()), ids=list(KNOWN_FIELDS)
)
def test_known_signatures_carry_their_fields(signature: str, fields: tuple[str, ...]):
    assert BY_SIGNATURE[signature].fields == fields


@pytest.mark.parametrize("signature", list(BY_SIGNATURE), ids=list(BY_SIGNATURE))
def test_every_spec_round_trips_through_a_simulate_log(signature: str):
    spec, values, log = _sample_log(signature)
    event = decode_ccip_event(log)
    assert event == CcipEvent(
        spec.name, ADDR, dict(zip(spec.fields, values, strict=True))
    )
    assert event is not None
    for field, abi_type in zip(spec.fields, spec.types, strict=True):
        if abi_type == "address":
            assert event.values[field] == PEER
        elif abi_type == "bytes32":
            assert isinstance(event.values[field], bytes)
        elif abi_type == "bool":
            assert event.values[field] is True


def test_bytes_valued_log_fields_decode_too():
    spec, values, _ = _sample_log("ReturnFinalized(uint256,bytes32,uint256,uint256)")
    event = decode_ccip_event(_log(spec, values, as_bytes=True))
    assert event is not None
    assert event.values == {
        "chainId": 12345,
        "operationId": b"\x07" * 32,
        "sentSD": 12345,
        "received": 12345,
    }


def test_struct_parameter_decodes_to_a_tuple_with_checksummed_addresses():
    spec = BY_SIGNATURE[f"CcipRoutePolicyUpdated(uint256,{ROUTE})"]
    route = (7, PEER.lower(), ADDR.lower(), 200_000, 50_000, 10**18, True)
    event = decode_ccip_event(_log(spec, (8453, route)))
    assert event is not None
    assert event.values["route"] == (7, PEER, ADDR, 200_000, 50_000, 10**18, True)


def test_unknown_topic_and_anonymous_log_decode_to_none():
    unknown = keccak(text="Transfer(address,address,uint256)")
    assert (
        decode_ccip_event({"address": ADDR, "topics": [unknown], "data": "0x"}) is None
    )
    assert decode_ccip_event({"address": ADDR, "topics": [], "data": "0x"}) is None


def test_decoded_values_are_read_only():
    _, _, log = _sample_log("DispatcherReady(uint256)")
    event = decode_ccip_event(log)
    assert event is not None
    with pytest.raises(TypeError):
        event.values["chainId"] = 1  # type: ignore[index]


def _mixed_logs() -> list[dict]:
    _, _, ready = _sample_log("DispatcherReady(uint256)")
    _, _, settled = _sample_log("AssetSettled(uint256,bytes32,uint256)")
    _, _, returned = _sample_log("ReturnFinalized(uint256,bytes32,uint256,uint256)")
    transfer = {
        "address": ADDR,
        "topics": [keccak(text="Transfer(address,address,uint256)")],
        "data": "0x",
    }
    return [transfer, ready, settled, transfer, returned]


def test_ccip_events_keeps_log_order_and_skips_unknown_logs():
    names = [event.name for event in ccip_events(_mixed_logs())]
    assert names == ["DispatcherReady", "AssetSettled", "ReturnFinalized"]


def test_find_ccip_events_selects_by_name():
    logs = _mixed_logs()
    (returned,) = find_ccip_events(logs, "ReturnFinalized")
    assert sorted(returned.values) == ["chainId", "operationId", "received", "sentSD"]
    assert len(find_ccip_events(logs, "DispatcherReady")) == 1
    assert find_ccip_events(logs, "AssetClaimed") == []


@pytest.mark.parametrize(
    ("method", "signature", "return_type", "raw", "expected"),
    [
        ("accounting_epoch", "accountingEpoch(uint256)", "uint64", 9, 9),
        ("active_proposal_id", "activeProposalId(uint256)", "uint256", 0, 0),
        (
            "attestation_anchor_principal",
            "attestationAnchorPrincipal(uint256)",
            "uint256",
            5_000_000,
            5_000_000,
        ),
    ],
)
def test_accounting_readers_encode_selector_and_decode_return(
    method: str, signature: str, return_type: str, raw: int, expected: int
):
    ctx = MagicMock()
    ctx.call.return_value = encode([return_type], [raw])
    call = getattr(CcipCrosschainExecutor(ctx, ADDR), method)(42161)
    assert call.to == ADDR
    assert call.data == function_signature_to_4byte_selector(signature) + encode(
        ["uint256"], [42161]
    )
    assert call.call() == expected


# --- mirrored from a contracts checkout ------------------------------------------

#: The v3 factory pair's source (the ticket's own gas limit on the pilot-v2 revision ``810e260``).
CONTRACTS_REVISION = "7eccea2357f25e0f795dd9b895e1c497677c2cd4"
CCIP_SOURCES = "contracts/crosschain/ccip"
# Stripped before parsing, as tests/test_solidity_mirrors.py does: a trailing
# `// note` would otherwise become a member and a `)` inside prose would
# truncate a body.
_COMMENT_RE = re.compile(r"//[^\n]*|/\*.*?\*/", re.DOTALL)
_EVENT_RE = re.compile(r"\bevent\s+(?P<name>\w+)\s*\((?P<body>[^)]*)\)\s*;")
_STRUCT_RE = re.compile(r"\bstruct\s+(?P<name>\w+)\s*\{(?P<body>[^}]*)\}")
_ENUM_RE = re.compile(r"\benum\s+(?P<name>\w+)\s*\{")


def _ccip_sources() -> list[str]:
    contracts_dir = os.environ.get("IPOR_FUSION_CONTRACTS_DIR")
    if not contracts_dir:
        pytest.skip(
            "IPOR_FUSION_CONTRACTS_DIR not set; the event registry is not mirrored"
        )
    git = shutil.which("git")
    if git is None:
        pytest.skip("git not found on PATH")
    root = Path(contracts_dir).expanduser()
    completed = subprocess.run(  # noqa: S603 - resolved via shutil.which
        [git, "-C", str(root), "rev-parse", "HEAD"],
        capture_output=True,
        text=True,
        timeout=30.0,
        check=False,
    )
    revision = completed.stdout.strip() or completed.stderr.strip()
    if revision != CONTRACTS_REVISION:
        pytest.skip(
            f"IPOR_FUSION_CONTRACTS_DIR is at {revision!r}; "
            f"the event registry pins {CONTRACTS_REVISION}"
        )
    paths = sorted((root / CCIP_SOURCES).rglob("*.sol"))
    assert paths, f"no Solidity sources under {root / CCIP_SOURCES}"
    return [_COMMENT_RE.sub("", path.read_text()) for path in paths]


def _members(body: str, separator: str) -> list[list[str]]:
    return [words for words in map(str.split, body.split(separator)) if words]


def _declared_events(sources: list[str]) -> dict[str, tuple[str, ...]]:
    """``signature -> fields`` over every event in ``sources``, with enum
    parameters as ``uint8`` and struct parameters as their ABI tuple."""
    enums = {m.group("name") for s in sources for m in _ENUM_RE.finditer(s)}
    structs = {
        m.group("name"): tuple(w[0] for w in _members(m.group("body"), ";"))
        for s in sources
        for m in _STRUCT_RE.finditer(s)
    }

    def abi_type(solidity_type: str) -> str:
        if solidity_type in enums:
            return "uint8"
        if solidity_type in structs:
            return "(" + ",".join(map(abi_type, structs[solidity_type])) + ")"
        return solidity_type

    declared: dict[str, tuple[str, ...]] = {}
    for source in sources:
        for match in _EVENT_RE.finditer(source):
            params = _members(match.group("body"), ",")
            assert not any("indexed" in words for words in params), match.group(0)
            signature = (
                f"{match.group('name')}({','.join(abi_type(w[0]) for w in params)})"
            )
            fields = tuple(words[-1] for words in params)
            # A library and its ABI interface declare the same event; they must agree.
            assert declared.setdefault(signature, fields) == fields, signature
    return declared


def test_event_registry_mirrors_the_contracts_checkout():
    declared = _declared_events(_ccip_sources())
    registered = {s.signature: s.fields for s in CCIP_EVENTS}
    assert registered == declared

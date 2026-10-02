"""The CCIP 2.0 indexer client and the manual-execution call it feeds."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
import requests
from eth_abi import decode
from eth_utils import function_signature_to_4byte_selector as selector
from eth_utils import keccak
from web3 import Web3

from ipor_fusion import (
    INDEXER_URLS,
    CcipOffRamp,
    VerifierResult,
    VerifierResultUnavailable,
    fetch_verifier_result,
    manual_execution,
)
from ipor_fusion.crosschain.ccip import indexer as indexer_module

OFF_RAMP = Web3.to_checksum_address("0x99bf17a320a981710f9b53c0c0b27219c1121d8d")
RESOLVER = Web3.to_checksum_address("0x2CaAfd3B4Cf606220580c885Bd2B448FB93dC03b")
EXECUTOR = Web3.to_checksum_address("0x6608d995bbde874de5292bfd289643c88d176ed3")
ENCODED = b"\x01" + bytes(range(60))
MESSAGE_ID = keccak(ENCODED)
CCV_DATA = bytes.fromhex("e9a05a20") + (2).to_bytes(2, "big") + b"\xab\xcd"


def _body(with_result: bool = True) -> dict:
    if not with_result:
        return {"success": True, "results": []}
    return {
        "success": True,
        "results": [
            {
                "verifierResult": {
                    "message_id": "0x" + MESSAGE_ID.hex(),
                    "message": {
                        "sequence_number": 8,
                        "ccip_receive_gas_limit": 6_000_000,
                    },
                    "message_ccv_addresses": [RESOLVER.lower()],
                    "message_executor_address": EXECUTOR.lower(),
                    "ccv_data": "0x" + CCV_DATA.hex(),
                }
            }
        ],
    }


def _response(status: int = 200, body: dict | None = None) -> MagicMock:
    response = MagicMock()
    response.status_code = status
    response.json.return_value = body if body is not None else _body()
    return response


def test_fetch_verifier_result_from_the_first_indexer(monkeypatch):
    get = MagicMock(return_value=_response())
    monkeypatch.setattr(indexer_module.requests, "get", get)

    result = fetch_verifier_result(MESSAGE_ID)

    assert result == VerifierResult(
        message_id=MESSAGE_ID,
        ccv_addresses=(RESOLVER,),
        ccv_data=CCV_DATA,
        executor=EXECUTOR,
        message={"sequence_number": 8, "ccip_receive_gas_limit": 6_000_000},
        indexer=INDEXER_URLS[0],
    )
    get.assert_called_once_with(
        f"{INDEXER_URLS[0]}/v1/verifierresults/0x{MESSAGE_ID.hex()}",
        headers={"Accept": "application/json", "User-Agent": "ipor-fusion-sdk"},
        timeout=15.0,
    )


def test_fetch_falls_through_unreachable_and_empty_indexers(monkeypatch):
    get = MagicMock(
        side_effect=[
            requests.ConnectionError("down"),
            _response(200, _body(False)),
            _response(),
        ]
    )
    monkeypatch.setattr(indexer_module.requests, "get", get)

    result = fetch_verifier_result(
        MESSAGE_ID, indexers=("https://a", "https://b", "https://c")
    )

    assert result.indexer == "https://c"
    assert get.call_count == 3


def test_fetch_reports_every_indexer_when_none_serves(monkeypatch):
    get = MagicMock(side_effect=[_response(503, {}), _response(200, _body(False))])
    monkeypatch.setattr(indexer_module.requests, "get", get)

    with pytest.raises(
        VerifierResultUnavailable, match="https://a: HTTP 503; https://b: no result yet"
    ):
        fetch_verifier_result(MESSAGE_ID, indexers=("https://a", "https://b"))
    with pytest.raises(ValueError, match="32 bytes"):
        fetch_verifier_result(b"\x01")


def test_manual_execution_builds_execute_and_checks_the_message_hash():
    off_ramp = CcipOffRamp(MagicMock(), OFF_RAMP)
    result = VerifierResult(
        MESSAGE_ID, (RESOLVER,), CCV_DATA, EXECUTOR, {}, INDEXER_URLS[0]
    )

    call = manual_execution(off_ramp, ENCODED, result)

    assert call.to == OFF_RAMP
    assert call.data[:4] == selector("execute(bytes,address[],bytes[],uint32)")
    assert decode(["bytes", "address[]", "bytes[]", "uint32"], call.data[4:]) == (
        ENCODED,
        (RESOLVER.lower(),),
        (CCV_DATA,),
        0,
    )
    with pytest.raises(ValueError, match="does not hash"):
        manual_execution(off_ramp, ENCODED + b"\x00", result)

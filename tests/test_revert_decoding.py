# pyright: reportOperatorIssue=false
from unittest.mock import MagicMock

from eth_abi import encode as abi_encode
from web3 import Web3

from ipor_fusion import VaultSimulator
from ipor_fusion.errors import (
    TransactionError,
    _decode_revert_reason,
    get_revert_reason,
)
from ipor_fusion.fuses.base import FuseAction

ERROR_SELECTOR = bytes.fromhex("08c379a0")
PANIC_SELECTOR = bytes.fromhex("4e487b71")


def test_decode_error_string():
    payload = abi_encode(["string"], ["Insufficient balance"])
    data = ERROR_SELECTOR + payload
    result = _decode_revert_reason(data)
    assert result == 'Error("Insufficient balance")'


def test_decode_error_empty_string():
    payload = abi_encode(["string"], [""])
    data = ERROR_SELECTOR + payload
    result = _decode_revert_reason(data)
    assert result == 'Error("")'


def test_decode_panic_overflow():
    payload = abi_encode(["uint256"], [0x11])
    data = PANIC_SELECTOR + payload
    result = _decode_revert_reason(data)
    assert result == "Panic(0x11: arithmetic overflow/underflow)"


def test_decode_panic_division_by_zero():
    payload = abi_encode(["uint256"], [0x12])
    data = PANIC_SELECTOR + payload
    result = _decode_revert_reason(data)
    assert result == "Panic(0x12: division or modulo by zero)"


def test_decode_panic_assert_failure():
    payload = abi_encode(["uint256"], [0x01])
    data = PANIC_SELECTOR + payload
    result = _decode_revert_reason(data)
    assert result == "Panic(0x01: assert failure)"


def test_decode_panic_unknown_code():
    payload = abi_encode(["uint256"], [0xFF])
    data = PANIC_SELECTOR + payload
    result = _decode_revert_reason(data)
    assert result == "Panic(0xff: unknown panic code)"


def test_decode_unknown_selector():
    data = bytes.fromhex("deadbeef" + "00" * 32)
    result = _decode_revert_reason(data)
    assert result.startswith("0x")
    assert "deadbeef" in result


def test_decode_empty_data():
    result = _decode_revert_reason(b"")
    assert result == "empty revert data"


def test_decode_short_data():
    data = bytes.fromhex("abcd")
    result = _decode_revert_reason(data)
    assert result == "0xabcd"


def test_decode_long_unknown_data_truncated():
    data = bytes(range(256)) * 2
    result = _decode_revert_reason(data)
    assert result.endswith("...")
    assert len(result) <= 76  # "0x" + 70 hex chars + "..."


def test_transaction_error_with_revert_reason():
    err = TransactionError(
        "Transaction failed",
        tx_hash="0xabc",
        revert_reason='Error("Insufficient balance")',
    )
    assert "Insufficient balance" in str(err)
    assert "0xabc" in str(err)
    assert err.revert_reason == 'Error("Insufficient balance")'
    assert err.tx_hash == "0xabc"


def test_transaction_error_without_revert_reason():
    err = TransactionError("Transaction failed", tx_hash="0xabc")
    assert str(err) == "Transaction failed, tx_hash=0xabc"
    assert err.revert_reason is None


def test_transaction_error_no_optional_fields():
    err = TransactionError("Transaction failed")
    assert str(err) == "Transaction failed"
    assert err.tx_hash is None
    assert err.revert_reason is None


# ---------------------------------------------------------------------------
# _decode_revert_reason — decode failure branches
# ---------------------------------------------------------------------------


def test_decode_error_selector_malformed_payload():
    """Error selector with payload that cannot be decoded as string."""
    bad_payload = b"\xff" * 4
    data = ERROR_SELECTOR + bad_payload
    result = _decode_revert_reason(data)
    assert result.startswith("Error(<decode failed>: 0x")


def test_decode_panic_selector_malformed_payload():
    """Panic selector with payload that cannot be decoded as uint256."""
    bad_payload = b"\xff" * 4
    data = PANIC_SELECTOR + bad_payload
    result = _decode_revert_reason(data)
    assert result.startswith("Panic(<decode failed>: 0x")


# ---------------------------------------------------------------------------
# get_revert_reason — mocked web3
# ---------------------------------------------------------------------------


def _make_web3(*, call_side_effect=None, tx_has_gas=True):
    """Build a mock Web3 wired for get_revert_reason."""
    web3 = MagicMock()
    tx = {
        "from": "0xaaaa",
        "to": "0xbbbb",
        "input": b"\x00",
        "value": 0,
    }
    if tx_has_gas:
        tx["gas"] = 21000
    web3.eth.get_transaction.return_value = tx
    if call_side_effect:
        web3.eth.call.side_effect = call_side_effect
    return web3


def _receipt(block=100):
    return {"blockNumber": block}


def test_get_revert_reason_replay_succeeds():
    """When eth_call replays without error, return None."""
    web3 = _make_web3()
    web3.eth.call.return_value = b""
    assert get_revert_reason(web3, b"\x00" * 32, _receipt()) is None


def test_get_revert_reason_exception_with_hex_data():
    """Exception with .data attribute containing 0x-prefixed revert data."""
    reason_bytes = abi_encode(["string"], ["not enough"])
    hex_data = "0x" + (ERROR_SELECTOR + reason_bytes).hex()
    exc = Exception("reverted")
    exc.data = hex_data  # type: ignore[attr-defined]

    web3 = _make_web3(call_side_effect=exc)
    result = get_revert_reason(web3, b"\x00" * 32, _receipt())
    assert result == 'Error("not enough")'


def test_get_revert_reason_message_contains_revert():
    """Exception message containing 'revert' is returned as-is."""
    exc = Exception("execution reverted: some reason")
    web3 = _make_web3(call_side_effect=exc)
    result = get_revert_reason(web3, b"\x00" * 32, _receipt())
    assert "execution reverted" in result


def test_get_revert_reason_message_contains_revert_case_insensitive():
    """Case-insensitive match on 'Revert'."""
    exc = Exception("Revert happened")
    web3 = _make_web3(call_side_effect=exc)
    result = get_revert_reason(web3, b"\x00" * 32, _receipt())
    assert "Revert" in result


def test_get_revert_reason_unrecognised_exception():
    """Unrelated exception returns None."""
    exc = Exception("network timeout")
    web3 = _make_web3(call_side_effect=exc)
    assert get_revert_reason(web3, b"\x00" * 32, _receipt()) is None


def test_get_revert_reason_tx_without_gas():
    """Transaction without 'gas' field still works."""
    web3 = _make_web3(tx_has_gas=False)
    web3.eth.call.return_value = b""
    assert get_revert_reason(web3, b"\x00" * 32, _receipt()) is None


def test_get_revert_reason_data_not_hex_prefix():
    """Exception with .data that is a string but not 0x-prefixed."""
    exc = Exception("something")
    exc.data = "not-hex"  # type: ignore[attr-defined]
    web3 = _make_web3(call_side_effect=exc)
    # Message doesn't contain "revert", data doesn't start with "0x"
    assert get_revert_reason(web3, b"\x00" * 32, _receipt()) is None


# ---------------------------------------------------------------------------
# VaultSimulator._parse_response — eth_simulateV1 client-variant error shapes.
# The spec's error-object form ({code,message,data} with the revert payload in
# error.data) is verified against a real provider by
# test_simulate_claim_merkl_wrapper_base.py::
# test_simulate_merkl_wrapper_claim_rejects_ungranted_received_token.
# The variants below cannot be reproduced on that provider, so they are the
# only synthetic-response tests: a plain-string error and an error object
# carrying no revert payload at all.
# ---------------------------------------------------------------------------

_SIM_VAULT = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
_SIM_ALPHA = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")


def _sim_with_one_execute() -> VaultSimulator:
    sim = VaultSimulator(MagicMock(), vault=_SIM_VAULT, alpha=_SIM_ALPHA)
    sim.execute([FuseAction(fuse=_SIM_VAULT, data=b"\x01")])
    return sim


def _failed_call_response(error: object, return_data: str = "0x") -> list[dict]:
    return [
        {
            "calls": [
                {
                    "status": "0x0",
                    "returnData": return_data,
                    "gasUsed": "0x100",
                    "error": error,
                }
            ]
        }
    ]


def test_parse_response_error_object_without_data():
    """Error object with no revert payload falls back to its message."""
    result = _sim_with_one_execute()._parse_response(
        _failed_call_response({"code": -32000, "message": "out of gas"})
    )
    assert result.revert_reason == "out of gas"
    assert result.calls[0].error == "out of gas"


def test_parse_response_plain_string_error_passthrough():
    """Clients reporting error as a plain string keep the old behavior."""
    result = _sim_with_one_execute()._parse_response(
        _failed_call_response("execution reverted")
    )
    assert result.revert_reason == "execution reverted"
    assert result.calls[0].error == "execution reverted"

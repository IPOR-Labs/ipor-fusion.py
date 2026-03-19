from eth_abi import encode as abi_encode

from ipor_fusion.errors import _decode_revert_reason, TransactionError

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

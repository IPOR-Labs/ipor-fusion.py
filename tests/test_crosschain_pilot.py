"""Offline guards against silently accepting changed pilot code or code stores."""

from unittest.mock import MagicMock

import pytest
from _crosschain_pilot import _assert_creation_store, _assert_runtime
from web3 import Web3

ADDRESS = "0x" + "22" * 20
SECOND_ADDRESS = "0x" + "33" * 20
BLOCK = 123


@pytest.mark.parametrize("actual", [b"\x60\x00", b"\x60", b"\x60\x01"])
def test_runtime_requires_exact_size_and_hash(actual):
    web3 = MagicMock()
    web3.eth.get_code.return_value = actual
    expected = {"size": 2, "runtime_keccak": "0x" + Web3.keccak(b"\x60\x00").hex()}
    if actual == b"\x60\x00":
        _assert_runtime(web3, ADDRESS, BLOCK, expected)
    else:
        with pytest.raises(AssertionError, match="pilot runtime"):
            _assert_runtime(web3, ADDRESS, BLOCK, expected)
    web3.eth.get_code.assert_called_once_with(ADDRESS, block_identifier=BLOCK)


@pytest.mark.parametrize(
    "mutation", [None, "pointer", "length", "prefix", "hash", "extra_bytes"]
)
def test_creation_store_is_fail_closed(mutation):
    store = {
        "addresses": [ADDRESS, SECOND_ADDRESS],
        "address_slots": [12, 13],
        "length_slot": 16,
        "length_offset": 4,
        "size": 2,
        "creation_keccak": "0x" + Web3.keccak(b"\x01\x02").hex(),
    }
    slots = {
        12: bytes(12) + bytes.fromhex(ADDRESS[2:]),
        13: bytes(12) + bytes.fromhex(SECOND_ADDRESS[2:]),
        16: ((2 << 32) | 999).to_bytes(32, "big"),
    }
    codes = {ADDRESS: b"\x00\x01", SECOND_ADDRESS: b"\x00\x02"}
    if mutation == "pointer":
        slots[12] = bytes(32)
    if mutation == "length":
        slots[16] = (3 << 32).to_bytes(32, "big")
    if mutation in ("prefix", "hash", "extra_bytes"):
        codes[ADDRESS] = {
            "prefix": b"\x01\x01",
            "hash": b"\x00\x03",
            "extra_bytes": b"\x00\x01\x04",
        }[mutation]
    web3 = MagicMock()
    web3.eth.get_storage_at.side_effect = lambda address, slot, block_identifier: slots[
        slot
    ]
    web3.eth.get_code.side_effect = lambda address, block_identifier: codes[address]
    if mutation is None:
        _assert_creation_store(web3, BLOCK, "dispatcher", store)
    else:
        with pytest.raises(AssertionError):
            _assert_creation_store(web3, BLOCK, "dispatcher", store)
    for call in web3.eth.get_storage_at.call_args_list:
        assert call.kwargs["block_identifier"] == BLOCK
    for call in web3.eth.get_code.call_args_list:
        assert call.kwargs["block_identifier"] == BLOCK

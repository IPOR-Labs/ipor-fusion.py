"""Unit tests for `Call.call(ctx)` — the eth_call + decode path shared by every
reader and wrapper."""

from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_abi.exceptions import DecodingError, InsufficientDataBytes
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion.core.contract import Call
from ipor_fusion.errors import EmptyCallResultError, IporFusionError

TARGET = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
SELECTOR = bytes.fromhex("12345678")


def _ctx(raw: bytes) -> MagicMock:
    ctx = MagicMock()
    ctx.chain_id = 42161
    ctx.call.return_value = HexBytes(raw)
    return ctx


def test_decodes_return_values():
    ctx = _ctx(encode(["uint256"], [7]))
    call: Call[int] = Call(to=TARGET, data=SELECTOR, output_types=["uint256"])
    assert call.call(ctx) == 7


def test_map_composes_decoder_and_preserves_call():
    ctx = _ctx(encode(["uint256"], [7]))
    call: Call[str] = Call(
        to=TARGET,
        data=SELECTOR,
        output_types=["uint256"],
        decoder=lambda value: f"value={value}",
    )

    mapped = call.map(len)

    assert mapped.call(ctx) == len("value=7")
    assert mapped.to == call.to
    assert mapped.data == call.data
    assert mapped.output_types == call.output_types
    assert mapped.output_types is not call.output_types


def test_map_rejects_write_only_call():
    call: Call[None] = Call(to=TARGET, data=SELECTOR)

    with pytest.raises(ValueError, match="cannot map a write-only Call"):
        call.map(lambda value: value)


def test_empty_return_raises_typed_error_naming_target_chain_and_selector():
    """An address with no code answers eth_call with empty data; surface that
    instead of the decoder's bare "Tried to read 32 bytes, only got 0 bytes"."""
    ctx = _ctx(b"")
    call: Call[int] = Call(to=TARGET, data=SELECTOR, output_types=["uint256"])

    with pytest.raises(EmptyCallResultError) as exc_info:
        call.call(ctx)

    message = str(exc_info.value)
    assert TARGET in message
    assert "chain 42161" in message
    assert "0x12345678" in message


def test_empty_return_error_is_an_sdk_error_not_a_decode_error():
    """An empty answer is a fact about the chain, not malformed data: catch
    `EmptyCallResultError`, not eth_abi's decoding errors."""
    assert issubclass(EmptyCallResultError, IporFusionError)
    assert not issubclass(EmptyCallResultError, DecodingError)


def test_short_return_data_still_raises_the_decoder_error():
    ctx = _ctx(b"\x01")
    call: Call[int] = Call(to=TARGET, data=SELECTOR, output_types=["uint256"])

    with pytest.raises(InsufficientDataBytes):
        call.call(ctx)


def test_write_only_call_cannot_be_read():
    ctx = _ctx(b"")
    call: Call[None] = Call(to=TARGET, data=SELECTOR)

    with pytest.raises(ValueError, match="write-only Call"):
        call.call(ctx)
    with pytest.raises(ValueError, match="write-only Call"):
        call.decode(b"")

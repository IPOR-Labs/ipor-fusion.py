"""Unit tests for `Call.call()` — the eth_call + decode path shared by every
reader and wrapper."""

from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_abi.exceptions import InsufficientDataBytes
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
    call: Call[int] = Call(
        to=TARGET,
        data=SELECTOR,
        output_types=["uint256"],
        ctx=_ctx(encode(["uint256"], [7])),
    )
    assert call.call() == 7


def test_map_composes_decoder_and_preserves_call():
    call: Call[str] = Call(
        to=TARGET,
        data=SELECTOR,
        output_types=["uint256"],
        decoder=lambda value: f"value={value}",
        ctx=_ctx(encode(["uint256"], [7])),
    )

    mapped = call.map(len)

    assert mapped.call() == len("value=7")
    assert mapped.to == call.to
    assert mapped.data == call.data
    assert mapped.output_types == call.output_types
    assert mapped.output_types is not call.output_types
    assert mapped.ctx is call.ctx


def test_map_rejects_write_only_call():
    call: Call[None] = Call(to=TARGET, data=SELECTOR)

    with pytest.raises(ValueError, match="cannot map a write-only Call"):
        call.map(lambda value: value)


def test_empty_return_raises_typed_error_naming_target_chain_and_selector():
    """An address with no code answers eth_call with empty data; surface that
    instead of the decoder's bare "Tried to read 32 bytes, only got 0 bytes"."""
    call: Call[int] = Call(
        to=TARGET, data=SELECTOR, output_types=["uint256"], ctx=_ctx(b"")
    )

    with pytest.raises(EmptyCallResultError) as exc_info:
        call.call()

    message = str(exc_info.value)
    assert TARGET in message
    assert "chain 42161" in message
    assert "0x12345678" in message


def test_empty_return_error_is_still_a_decode_error():
    """Callers that already tolerate `InsufficientDataBytes` (optional
    functions probed on older contracts) keep working unchanged."""
    assert issubclass(EmptyCallResultError, InsufficientDataBytes)
    assert issubclass(EmptyCallResultError, IporFusionError)

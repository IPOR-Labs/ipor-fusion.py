"""Unit tests for `Call.call(ctx)` — the eth_call + decode path shared by every
reader and wrapper."""

import dataclasses
import importlib
import pkgutil
from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_abi.exceptions import DecodingError, InsufficientDataBytes
from hexbytes import HexBytes
from web3 import Web3

import ipor_fusion
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
    assert hash(mapped.output_types) == hash(call.output_types)


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


def test_subscripted_construction_and_hashing():
    call = Call[int](to=TARGET, data=SELECTOR, output_types=["uint256"])
    assert call.output_types == ("uint256",)
    same = Call[int](to=TARGET, data=SELECTOR, output_types=("uint256",))
    assert call == same
    assert hash(call) == hash(same)
    assert {call, same} == {call}


def test_no_generic_dataclass_is_slotted():
    """Early 3.11/3.12 patch releases (3.11.1, 3.12.0, 3.12.1) raise
    ``TypeError`` from ``Cls[T](...)`` on a frozen slotted generic dataclass,
    which current interpreters do not reproduce; keep the pattern out."""
    offenders = []
    for module_name in _sdk_modules():
        module = importlib.import_module(module_name)
        for obj in vars(module).values():
            if (
                isinstance(obj, type)
                and obj.__module__ == module_name
                and dataclasses.is_dataclass(obj)
                and getattr(obj, "__parameters__", ())
                and "__slots__" in vars(obj)
            ):
                offenders.append(f"{module_name}.{obj.__name__}")
    assert offenders == []


def _sdk_modules() -> list[str]:
    return [
        info.name
        for info in pkgutil.walk_packages(ipor_fusion.__path__, "ipor_fusion.")
    ]

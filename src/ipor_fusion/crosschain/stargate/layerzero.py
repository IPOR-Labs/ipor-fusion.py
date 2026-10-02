"""LayerZero V2 and Stargate V2 wire codecs used by the Stargate transport.

Pure byte layouts, sourced from the libraries vendored in the contracts repo;
names follow those libraries:

- ``OptionsBuilder`` mirrors ``OptionsBuilder.sol`` (type-3 options).
- ``EnforcedOptionParam`` / ``encode_enforced_options`` build the
  ``abi.encode(EnforcedOptionParam[])`` blob ``registerChain`` takes.
- ``Packet`` reads the ``PacketV1Codec`` layout carried by the endpoint's
  ``PacketSent(bytes encodedPayload, bytes options, address sendLibrary)``.
- ``TaxiMessage`` reads ``TaxiCodec`` (the Stargate V2 message inside a
  packet sent by the ``TokenMessaging`` OApp).
- ``encode_oft_compose_msg`` is ``OFTComposeMsgCodec.encode``, the message a
  Stargate pool hands to ``endpoint.sendCompose`` on delivery.
- ``lz_receive_calldata`` / ``lz_compose_calldata`` build the ``lzReceive``
  and ``lzCompose`` calls the endpoint makes into a receiver, so a simulation
  can impersonate it.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3 import Web3

from ipor_fusion.core.contract import _encode_calldata
from ipor_fusion.crosschain.logs import log_bytes
from ipor_fusion.fuses.base import ZERO_ADDRESS, _validate_not_zero_address

_TYPE_3 = b"\x00\x03"
_EXECUTOR_WORKER_ID = 1
_OPTION_TYPE_LZRECEIVE = 1
_OPTION_TYPE_NATIVE_DROP = 2
_OPTION_TYPE_LZCOMPOSE = 3
_PACKET_VERSION = 1
_MSG_TYPE_TAXI = 1

#: topic0 of ``ILayerZeroEndpointV2.PacketSent``.
PACKET_SENT_TOPIC: bytes = keccak(text="PacketSent(bytes,bytes,address)")


def _uint(value: int, bits: int, name: str) -> bytes:
    if not 0 <= value < (1 << bits):
        raise ValueError(f"{name} out of range for uint{bits}: {value}")
    return value.to_bytes(bits // 8, "big")


def address_to_bytes32(address: ChecksumAddress) -> bytes:
    """``AddressCast.toBytes32``: left-pad a 20-byte address to 32 bytes."""
    return bytes(12) + bytes.fromhex(address.removeprefix("0x"))


def bytes32_to_address(value: bytes) -> ChecksumAddress:
    """``AddressCast.toAddress``: the address in the low 20 bytes."""
    if len(value) != 32 or value[:12] != bytes(12):
        raise ValueError(f"not a left-padded address: 0x{value.hex()}")
    return Web3.to_checksum_address(value[12:])


@dataclass(frozen=True, slots=True)
class OptionsBuilder:
    """Immutable type-3 options builder (``OptionsBuilder.sol``).

    Each method returns a new instance; ``bytes(options)`` yields the blob::

        OptionsBuilder.new_options()
            .add_executor_lz_receive_option(250_000)
            .add_executor_lz_compose_option(0, 1_500_000)
    """

    data: bytes = _TYPE_3

    def __bytes__(self) -> bytes:
        return self.data

    @classmethod
    def new_options(cls) -> OptionsBuilder:
        """``newOptions()``: the bare type-3 header."""
        return cls()

    def add_executor_lz_receive_option(
        self, gas: int, value: int = 0
    ) -> OptionsBuilder:
        """``addExecutorLzReceiveOption(gas, value)``."""
        option = _uint(gas, 128, "gas")
        if value:
            option += _uint(value, 128, "value")
        return self._add_executor_option(_OPTION_TYPE_LZRECEIVE, option)

    def add_executor_native_drop_option(
        self, amount: int, receiver: ChecksumAddress
    ) -> OptionsBuilder:
        """``addExecutorNativeDropOption(amount, receiver)``."""
        _validate_not_zero_address(receiver, "receiver")
        return self._add_executor_option(
            _OPTION_TYPE_NATIVE_DROP,
            _uint(amount, 128, "amount") + address_to_bytes32(receiver),
        )

    def add_executor_lz_compose_option(
        self, index: int, gas: int, value: int = 0
    ) -> OptionsBuilder:
        """``addExecutorLzComposeOption(index, gas, value)``; Stargate taxi
        deliveries compose at index 0."""
        option = _uint(index, 16, "index") + _uint(gas, 128, "gas")
        if value:
            option += _uint(value, 128, "value")
        return self._add_executor_option(_OPTION_TYPE_LZCOMPOSE, option)

    def _add_executor_option(self, option_type: int, option: bytes) -> OptionsBuilder:
        return OptionsBuilder(
            self.data
            + bytes([_EXECUTOR_WORKER_ID])
            + _uint(len(option) + 1, 16, "option length")
            + bytes([option_type])
            + option
        )


@dataclass(frozen=True, slots=True)
class EnforcedOptionParam:
    """``IOAppOptionsType3.EnforcedOptionParam``: options an OApp enforces on
    every send of ``msg_type`` to ``eid``. The executor's command lane is
    ``msg_type`` 1 and must carry a non-zero ``lzReceive`` gas; the compose
    (asset) lane is 2."""

    eid: int
    msg_type: int
    options: bytes | OptionsBuilder

    def as_tuple(self) -> tuple[int, int, bytes]:
        return (self.eid, self.msg_type, bytes(self.options))


def encode_enforced_options(params: Sequence[EnforcedOptionParam]) -> bytes:
    """``abi.encode(EnforcedOptionParam[])``, the blob ``registerChain`` takes."""
    if not params:
        raise ValueError("params must not be empty")
    return encode(["(uint32,uint16,bytes)[]"], [[p.as_tuple() for p in params]])


@dataclass(frozen=True, slots=True)
class Packet:
    """One ``PacketV1Codec`` packet: the payload of a ``PacketSent`` event."""

    nonce: int
    src_eid: int
    sender: ChecksumAddress
    dst_eid: int
    receiver: ChecksumAddress
    guid: bytes
    message: bytes

    @classmethod
    def decode(cls, encoded: bytes) -> Packet:
        if len(encoded) < 113 or encoded[0] != _PACKET_VERSION:
            raise ValueError("not a version-1 LayerZero packet")
        return cls(
            nonce=int.from_bytes(encoded[1:9], "big"),
            src_eid=int.from_bytes(encoded[9:13], "big"),
            sender=bytes32_to_address(encoded[13:45]),
            dst_eid=int.from_bytes(encoded[45:49], "big"),
            receiver=bytes32_to_address(encoded[49:81]),
            guid=encoded[81:113],
            message=encoded[113:],
        )

    @classmethod
    def from_log(cls, log: Mapping) -> Packet:
        """Decode the packet inside a ``PacketSent`` log (raw dict with hex fields)."""
        encoded_payload, _options, _send_library = decode(
            ["bytes", "bytes", "address"], log_bytes(log["data"])
        )
        return cls.decode(bytes(encoded_payload))


@dataclass(frozen=True, slots=True)
class TaxiMessage:
    """A Stargate V2 taxi message (``TaxiCodec.decodeTaxi``), as carried by a
    ``TokenMessaging`` packet. ``compose_msg`` is the raw compose payload
    ``composeFrom (bytes32) || message`` or empty."""

    asset_id: int
    receiver: ChecksumAddress
    amount_sd: int
    compose_msg: bytes

    @classmethod
    def decode(cls, message: bytes) -> TaxiMessage:
        if len(message) < 43 or message[0] != _MSG_TYPE_TAXI:
            raise ValueError("not a Stargate taxi message")
        return cls(
            asset_id=int.from_bytes(message[1:3], "big"),
            receiver=bytes32_to_address(message[3:35]),
            amount_sd=int.from_bytes(message[35:43], "big"),
            compose_msg=message[43:],
        )

    @property
    def compose_from(self) -> ChecksumAddress | None:
        """``OFTComposeMsgCodec.composeFrom``: who supplied the compose payload."""
        return bytes32_to_address(self.compose_msg[:32]) if self.compose_msg else None

    @property
    def compose_payload(self) -> bytes:
        """The compose payload without its ``composeFrom`` prefix."""
        return self.compose_msg[32:] if self.compose_msg else b""


def encode_oft_compose_msg(
    nonce: int, src_eid: int, amount_ld: int, compose_msg: bytes
) -> bytes:
    """``OFTComposeMsgCodec.encode(nonce, srcEid, amountLD, composeMsg)``: the
    message the endpoint passes to ``lzCompose`` after a Stargate delivery.
    ``compose_msg`` is ``composeFrom || payload`` as carried by the taxi."""
    return (
        _uint(nonce, 64, "nonce")
        + _uint(src_eid, 32, "src_eid")
        + _uint(amount_ld, 256, "amount_ld")
        + compose_msg
    )


def lz_receive_calldata(
    *,
    src_eid: int,
    sender: ChecksumAddress,
    nonce: int,
    guid: bytes,
    message: bytes,
    executor: ChecksumAddress = ZERO_ADDRESS,  # type: ignore[assignment]
    extra_data: bytes = b"",
) -> bytes:
    """``lzReceive(Origin, guid, message, executor, extraData)`` as the endpoint calls it."""
    return _encode_calldata(
        "lzReceive((uint32,bytes32,uint64),bytes32,bytes,address,bytes)",
        (src_eid, address_to_bytes32(sender), nonce),
        guid,
        message,
        executor,
        extra_data,
    )


def lz_compose_calldata(
    *,
    from_: ChecksumAddress,
    guid: bytes,
    message: bytes,
    executor: ChecksumAddress = ZERO_ADDRESS,  # type: ignore[assignment]
    extra_data: bytes = b"",
) -> bytes:
    """``lzCompose(from, guid, message, executor, extraData)`` as the endpoint calls it."""
    return _encode_calldata(
        "lzCompose(address,bytes32,bytes,address,bytes)",
        from_,
        guid,
        message,
        executor,
        extra_data,
    )

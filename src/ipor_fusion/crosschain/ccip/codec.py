"""Chainlink CCIP wire codecs used by the CCIP transport.

The deployed lanes run ``OnRamp 2.0.0``, whose ``CCIPMessageSent`` event
carries the message as a packed ``MessageV1Codec`` blob rather than an ABI
struct. Names follow ``MessageV1Codec.sol`` and the ``CcipClient`` library
vendored in the contracts repo: ``MessageV1``, ``TokenTransferV1``,
``Any2EVMMessage``, ``EVMTokenAmount``. ``ccip_receive_calldata`` builds the
``ccipReceive(Any2EVMMessage)`` call the Router makes into a receiver, so a
simulation can impersonate the Router.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass

from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3 import Web3

from ipor_fusion.core.contract import _encode_calldata
from ipor_fusion.crosschain.logs import log_bytes

#: topic0 of ``OnRamp 2.0.0``'s ``CCIPMessageSent``; ``destChainSelector``,
#: ``sender`` and ``messageId`` are indexed.
CCIP_MESSAGE_SENT_TOPIC: bytes = keccak(
    text=(
        "CCIPMessageSent(uint64,address,bytes32,address,uint256,bytes,"
        "(address,uint32,uint32,uint256,bytes)[],bytes[])"
    )
)
_EVENT_DATA_TYPES = [
    "address",  # feeToken
    "uint256",  # tokenAmountBeforeTokenPoolFees
    "bytes",  # encodedMessage
    "(address,uint32,uint32,uint256,bytes)[]",  # receipts
    "bytes[]",  # verifierBlobs
]
_MESSAGE_VERSION = 1


class _Reader:
    """Cursor over a packed ``MessageV1Codec`` blob."""

    def __init__(self, data: bytes) -> None:
        self._data = data
        self._offset = 0

    def take(self, size: int) -> bytes:
        end = self._offset + size
        if end > len(self._data):
            raise ValueError("truncated CCIP MessageV1 blob")
        chunk = self._data[self._offset : end]
        self._offset = end
        return chunk

    def uint(self, size: int) -> int:
        return int.from_bytes(self.take(size), "big")

    def prefixed(self, length_size: int) -> bytes:
        return self.take(self.uint(length_size))

    @property
    def exhausted(self) -> bool:
        return self._offset == len(self._data)


@dataclass(frozen=True, slots=True)
class TokenTransferV1:
    """``MessageV1Codec.TokenTransferV1``. Address fields are raw bytes as
    encoded for the destination chain (20 bytes on EVM)."""

    amount: int
    source_pool_address: bytes
    source_token_address: bytes
    dest_token_address: bytes
    token_receiver: bytes
    extra_data: bytes

    @classmethod
    def _read(cls, reader: _Reader) -> TokenTransferV1:
        if reader.uint(1) != _MESSAGE_VERSION:
            raise ValueError("unsupported CCIP token transfer version")
        return cls(
            amount=reader.uint(32),
            source_pool_address=reader.prefixed(1),
            source_token_address=reader.prefixed(1),
            dest_token_address=reader.prefixed(1),
            token_receiver=reader.prefixed(1),
            extra_data=reader.prefixed(2),
        )


@dataclass(frozen=True, slots=True)
class MessageV1:
    """``MessageV1Codec.MessageV1``: the message a CCIP 2.0 OnRamp commits to.

    ``sender`` is ``abi.encode(address)`` (32 bytes) and ``receiver`` is the
    unpadded destination address (20 bytes on EVM), exactly as the OnRamp
    encodes them.
    """

    source_chain_selector: int
    dest_chain_selector: int
    message_number: int
    execution_gas_limit: int
    ccip_receive_gas_limit: int
    finality: bytes
    ccv_and_executor_hash: bytes
    on_ramp_address: bytes
    off_ramp_address: bytes
    sender: bytes
    receiver: bytes
    dest_blob: bytes
    token_transfer: tuple[TokenTransferV1, ...]
    data: bytes

    @classmethod
    def decode(cls, encoded: bytes) -> MessageV1:
        """``MessageV1Codec._decodeMessageV1``."""
        reader = _Reader(encoded)
        if reader.uint(1) != _MESSAGE_VERSION:
            raise ValueError("unsupported CCIP message version")
        header = (
            reader.uint(8),
            reader.uint(8),
            reader.uint(8),
            reader.uint(4),
            reader.uint(4),
            reader.take(4),
            reader.take(32),
        )
        addresses = (reader.prefixed(1), reader.prefixed(1), reader.prefixed(1))
        receiver = reader.prefixed(1)
        dest_blob = reader.prefixed(2)
        transfers = cls._read_token_transfer(reader)
        data = reader.prefixed(2)
        if not reader.exhausted:
            raise ValueError("trailing bytes in CCIP MessageV1 blob")
        return cls(*header, *addresses, receiver, dest_blob, transfers, data)

    @staticmethod
    def _read_token_transfer(reader: _Reader) -> tuple[TokenTransferV1, ...]:
        # At most one transfer per message (MAX_NUMBER_OF_TOKENS = 1); the
        # length prefix counts the encoded bytes of that single transfer.
        encoded = reader.prefixed(2)
        if not encoded:
            return ()
        inner = _Reader(encoded)
        transfer = TokenTransferV1._read(inner)
        if not inner.exhausted:
            raise ValueError("trailing bytes in CCIP token transfer")
        return (transfer,)

    @property
    def sender_address(self) -> ChecksumAddress:
        (sender,) = decode(["address"], self.sender)
        return Web3.to_checksum_address(sender)

    @property
    def receiver_address(self) -> ChecksumAddress:
        return evm_address(self.receiver)


def evm_address(raw: bytes) -> ChecksumAddress:
    """An EVM address encoded either unpadded (20 bytes) or ABI-padded (32)."""
    if len(raw) == 32:
        raw = raw[12:]
    if len(raw) != 20:
        raise ValueError(f"not an EVM address: 0x{raw.hex()}")
    return Web3.to_checksum_address(raw)


@dataclass(frozen=True, slots=True)
class CcipMessageSent:
    """One ``CCIPMessageSent`` log: the Router-assigned id plus the decoded message."""

    message_id: bytes
    dest_chain_selector: int
    sender: ChecksumAddress
    message: MessageV1

    @classmethod
    def from_log(cls, log: Mapping) -> CcipMessageSent:
        topics = [log_bytes(topic) for topic in log["topics"]]
        if len(topics) != 4 or topics[0] != CCIP_MESSAGE_SENT_TOPIC:
            raise ValueError("not a CCIPMessageSent log")
        _fee_token, _amount_before_fees, encoded, _receipts, _blobs = decode(
            _EVENT_DATA_TYPES, log_bytes(log["data"])
        )
        return cls(
            message_id=topics[3],
            dest_chain_selector=int.from_bytes(topics[1], "big"),
            sender=Web3.to_checksum_address(topics[2][12:]),
            message=MessageV1.decode(bytes(encoded)),
        )


@dataclass(frozen=True, slots=True)
class EVMTokenAmount:
    """``CcipClient.EVMTokenAmount``."""

    token: ChecksumAddress
    amount: int


@dataclass(frozen=True, slots=True)
class Any2EVMMessage:
    """``CcipClient.Any2EVMMessage``: what the Router delivers to ``ccipReceive``.
    ``sender`` is the source address; it is ABI-encoded into the wire field,
    which is how the receivers decode it."""

    message_id: bytes
    source_chain_selector: int
    sender: ChecksumAddress
    data: bytes
    dest_token_amounts: tuple[EVMTokenAmount, ...] = ()


def ccip_receive_calldata(message: Any2EVMMessage) -> bytes:
    """``ccipReceive(Any2EVMMessage)`` as the Router calls it on delivery."""
    return _encode_calldata(
        "ccipReceive((bytes32,uint64,bytes,bytes,(address,uint256)[]))",
        (
            message.message_id,
            message.source_chain_selector,
            encode(["address"], [message.sender]),
            message.data,
            [(t.token, t.amount) for t in message.dest_token_amounts],
        ),
    )

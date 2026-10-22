"""Helpers over raw log dicts as ``eth_simulateV1`` returns them (hex fields)."""

from __future__ import annotations

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.logs import RawLog


def log_bytes(value: object) -> bytes:
    """A log field as bytes, whether the client returned hex text or bytes."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return bytes.fromhex(value.removeprefix("0x"))
    raise TypeError(f"unsupported log field type {type(value).__name__}")


def log_topics(log: RawLog) -> list[bytes]:
    """Every topic of a raw log dict; an absent ``topics`` is none."""
    topics = log.get("topics") or []
    if not isinstance(topics, list | tuple):
        raise TypeError(f"unsupported log topics type {type(topics).__name__}")
    return [log_bytes(topic) for topic in topics]


def log_topic0(log: RawLog) -> bytes | None:
    """The first topic of a raw log dict, or ``None`` for an anonymous log."""
    topics = log_topics(log)
    return topics[0] if topics else None


def log_address(log: RawLog) -> ChecksumAddress:
    address = log["address"]
    if not isinstance(address, str | bytes):
        raise TypeError(f"unsupported log address type {type(address).__name__}")
    return Web3.to_checksum_address(address)

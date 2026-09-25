"""Helpers over raw log dicts as ``eth_simulateV1`` returns them (hex fields)."""

from __future__ import annotations

from collections.abc import Mapping

from eth_typing import ChecksumAddress
from web3 import Web3


def log_bytes(value: object) -> bytes:
    """A log field as bytes, whether the client returned hex text or bytes."""
    if isinstance(value, bytes):
        return value
    if isinstance(value, str):
        return bytes.fromhex(value.removeprefix("0x"))
    raise TypeError(f"unsupported log field type {type(value).__name__}")


def log_topic0(log: Mapping) -> bytes | None:
    """The first topic of a raw log dict, or ``None`` for an anonymous log."""
    topics = log.get("topics") or []
    return log_bytes(topics[0]) if topics else None


def log_address(log: Mapping) -> ChecksumAddress:
    return Web3.to_checksum_address(log["address"])

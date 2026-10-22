from collections.abc import Callable
from typing import TypeVar

from eth_abi import decode
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import TxReceipt

T = TypeVar("T")


def extract_events(
    receipt: TxReceipt,
    event_signature: str,
    abi_types: list[str],
    dataclass_type: Callable[..., T],
) -> list[T]:
    sig_hash = Web3.keccak(text=event_signature)
    events: list[T] = []
    for log in receipt["logs"]:
        if log["topics"][0] == sig_hash:
            decoded = decode(abi_types, log["data"])
            events.append(
                dataclass_type(
                    *(
                        Web3.to_checksum_address(value) if abi == "address" else value
                        for abi, value in zip(abi_types, decoded, strict=True)
                    )
                )
            )
    return events


def abi_value(abi_type: str, value: object) -> int | bool | bytes | ChecksumAddress:
    """One ``eth_abi``-decoded event parameter as the Python type its ABI type
    decodes to: an address checksummed, a ``uintN`` / ``intN`` an int, a
    ``bool`` a bool, a ``bytesN`` bytes. Anything else raises ``TypeError``."""
    if abi_type == "address" and isinstance(value, str):
        return Web3.to_checksum_address(value)
    if abi_type == "bool" and isinstance(value, bool):
        return value
    if (
        abi_type.startswith(("uint", "int"))
        and isinstance(value, int)
        and not isinstance(value, bool)
    ):
        return value
    if abi_type.startswith("bytes") and isinstance(value, bytes):
        return value
    raise TypeError(f"{abi_type} decoded as {type(value).__name__}")

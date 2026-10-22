from collections.abc import Callable, Mapping
from typing import Protocol, TypeVar

from eth_abi import decode
from eth_abi.exceptions import DecodingError
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.core.logs import RawLog
from ipor_fusion.types import TxHash, parse_tx_hash

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


class EventSpec(Protocol):
    """An event declaration whose parameters are all unindexed."""

    @property
    def name(self) -> str: ...

    @property
    def params(self) -> tuple[tuple[str, str], ...]: ...


def _log_bytes(value: object, what: str, error: type[ValueError]) -> bytes:
    """Raw log bytes from what a node or an indexer serves: bytes-like
    (bytes, bytearray, HexBytes) or ``0x``-prefixed hex text. Anything else —
    an int (``bytes(32)`` would invent 32 zero bytes), unprefixed or invalid
    hex, a missing value — raises ``error``."""
    if isinstance(value, bytes | bytearray | memoryview):
        return bytes(value)
    if isinstance(value, str):
        if not value.startswith("0x"):
            raise error(f"{what}: hex text without 0x prefix")
        try:
            return bytes.fromhex(value[2:])
        except ValueError as exc:
            raise error(f"{what}: invalid hex") from exc
    raise error(f"{what}: unsupported type {type(value).__name__}")


def _log_quantity(value: object, what: str, error: type[ValueError]) -> int | None:
    """A block number or log index: int, or a JSON-RPC ``0x`` quantity;
    None stays None (optional identity); anything else raises ``error``."""
    if value is None:
        return None
    if isinstance(value, bool):
        raise error(f"{what}: boolean is not a quantity")
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.startswith("0x"):
        try:
            return int(value, 16)
        except ValueError as exc:
            raise error(f"{what}: invalid quantity") from exc
    raise error(f"{what}: unsupported quantity {value!r}")


def _transaction_hash(value: object, error: type[ValueError]) -> TxHash | None:
    if value is None:
        return None
    raw = _log_bytes(value, "transactionHash", error)
    if len(raw) != 32:
        raise error(f"transactionHash: {len(raw)} bytes, expected 32")
    return parse_tx_hash(raw)


def _topic_key(topic: object) -> bytes | None:
    """topic0 as 32 bytes, or None when it cannot be a topic at all (then the
    log is simply not one of ours)."""
    try:
        raw = _log_bytes(topic, "topic0", ValueError)
    except ValueError:
        return None
    return raw if len(raw) == 32 else None


def event_name(topic: object, specs: Mapping[bytes, EventSpec]) -> str | None:
    """The name of the event in ``specs`` behind ``topic``; None for any other
    topic and for a value that cannot be a topic."""
    key = _topic_key(topic)
    spec = specs.get(key) if key is not None else None
    return spec.name if spec else None


def decode_event_log(
    log: RawLog,
    specs: Mapping[bytes, EventSpec],
    classes: Mapping[str, Callable[..., T]],
    error: type[ValueError],
) -> T | None:
    """Decode a log of one of ``specs`` into its class from ``classes`` (built
    with ``emitter``, ``args`` and the log's identity). None when topic0 is not
    one of ``specs``; once it is, any malformed part raises ``error``."""
    topics = log.get("topics")
    if not isinstance(topics, list | tuple) or not topics:
        return None
    key = _topic_key(topics[0])
    spec = specs.get(key) if key is not None else None
    if spec is None:
        return None
    if len(topics) != 1:
        raise error(
            f"{spec.name}: {len(topics) - 1} indexed topic(s), the declaration has none"
        )
    if "data" not in log:
        raise error(f"{spec.name}: no data")
    raw = _log_bytes(log["data"], f"{spec.name} data", error)
    if len(raw) != 32 * len(spec.params):
        raise error(
            f"{spec.name}: {len(raw)} data bytes, {32 * len(spec.params)} declared"
        )
    try:
        values = decode([abi_type for abi_type, _ in spec.params], raw)
    except DecodingError as exc:
        raise error(f"{spec.name}: {exc}") from exc
    # Each value is narrowed to its ABI type; the class's args TypedDict is the
    # spec's shape (pinned by the tests), so the dict is that class's args.
    args = {
        name: abi_value(abi_type, value)
        for (abi_type, name), value in zip(spec.params, values, strict=True)
    }
    address = log.get("address")
    if not isinstance(address, str | bytes):
        raise error(f"{spec.name}: emitter")
    try:
        emitter = Web3.to_checksum_address(address)
    except ValueError as exc:
        raise error(f"{spec.name}: emitter") from exc
    return classes[spec.name](
        emitter=emitter,
        args=args,
        block_number=_log_quantity(log.get("blockNumber"), "blockNumber", error),
        transaction_hash=_transaction_hash(log.get("transactionHash"), error),
        log_index=_log_quantity(log.get("logIndex"), "logIndex", error),
    )

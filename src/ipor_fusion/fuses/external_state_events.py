"""ExternalState (market 50) events by topic, as the vault emits them.

Mirrors the ``event`` declarations in ``contracts/fuses/external_state``: the
balance fuse (big change, cache updates), the unpause, rescue and operation
fuses, and ``ExternalStateExecutorStorageLib``. Every one runs in the vault's
delegatecall context, so the emitter of a real log is the vault. Every
parameter is unindexed, so a log decodes from its ``data`` alone.

Two semantics to keep apart: ``ExecutorCreated`` is also emitted by an
idempotent create call that finds the executor already deployed, while
``ExternalStateExecutorDeployed`` marks the first deployment;
``ExternalStateAssetRescued`` names an asset the rescue fuse returned from the
executor to the vault (untracked or accidentally received tokens), not capital
leaving the vault, and carries no amount. The executor's own events
(``BalanceProposed``, ``BalanceConfirmed``, ``AssetWithdrawn``) are emitted by
the executor and are not listed here.

Decoding never guesses: a log whose ``topic0`` is not listed decodes to
``None``; a listed topic whose data does not decode to its declared types
raises ``ExternalStateEventDecodeError``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from eth_abi import decode
from eth_abi.exceptions import DecodingError
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import LogReceipt, TxReceipt


@dataclass(frozen=True, slots=True)
class ExternalStateEventSpec:
    """One event: its name and ``(abi_type, parameter_name)`` pairs."""

    name: str
    params: tuple[tuple[str, str], ...]

    @property
    def signature(self) -> str:
        return f"{self.name}({','.join(abi_type for abi_type, _ in self.params)})"

    @property
    def topic(self) -> bytes:
        return bytes(Web3.keccak(text=self.signature))


EXTERNAL_STATE_EVENT_SPECS: tuple[ExternalStateEventSpec, ...] = (
    # ExternalStateBalanceFuse
    ExternalStateEventSpec(
        "ExternalStateBigChangeDetected",
        (
            ("uint256", "previousTotal"),
            ("uint256", "newTotal"),
            ("uint256", "thresholdBps"),
        ),
    ),
    ExternalStateEventSpec(
        "ExternalStateBalanceFuseLastCustodianTimestampUpdated",
        (("uint256", "oldTimestamp"), ("uint256", "newTimestamp")),
    ),
    ExternalStateEventSpec(
        "ExternalStateBalanceFuseLastTotalBalanceUpdated",
        (("uint256", "oldTotalBalance"), ("uint256", "newTotalBalance")),
    ),
    # ExternalStateUnpauseFuse
    ExternalStateEventSpec(
        "ExternalStateUnpaused",
        (
            ("address", "signer"),
            ("uint256", "confirmedTotalBalance"),
            ("uint256", "nonce"),
        ),
    ),
    # ExternalStateRescueFuse
    ExternalStateEventSpec("ExternalStateAssetRescued", (("address", "asset"),)),
    # ExternalStateOperationFuse
    ExternalStateEventSpec(
        "ExecutorCreated", (("address", "executor"), ("uint256", "marketId"))
    ),
    ExternalStateEventSpec(
        "ExternalStateOperationFuseEnter",
        (
            ("address", "version"),
            ("address", "asset"),
            ("uint256", "amount"),
            ("address", "balanceAccount"),
            ("uint256", "valueInUnderlying"),
            ("uint256", "actionsCount"),
        ),
    ),
    ExternalStateEventSpec(
        "ExternalStateOperationFuseExit",
        (
            ("address", "version"),
            ("address", "asset"),
            ("uint256", "amount"),
            ("address", "balanceAccount"),
            ("uint256", "valueInUnderlying"),
            ("uint256", "actionsCount"),
        ),
    ),
    # ExternalStateExecutorStorageLib
    ExternalStateEventSpec(
        "ExternalStateExecutorDeployed",
        (("address", "executor"), ("uint256", "marketId")),
    ),
)

#: topic0 -> spec, for every ExternalState event the vault emits.
EXTERNAL_STATE_EVENTS: dict[bytes, ExternalStateEventSpec] = {
    spec.topic: spec for spec in EXTERNAL_STATE_EVENT_SPECS
}
#: topic0 hex strings, for an ``eth_getLogs`` / indexer topic filter.
EXTERNAL_STATE_TOPICS: tuple[str, ...] = tuple(
    "0x" + spec.topic.hex() for spec in EXTERNAL_STATE_EVENT_SPECS
)


class ExternalStateEventDecodeError(ValueError):
    """A log carries a listed ExternalState topic but its data does not decode
    to the declared parameters: malformed, never a plausible event."""


@dataclass(frozen=True, slots=True)
class ExternalStateEvent:
    """One decoded ExternalState log with its raw identity."""

    name: str
    emitter: ChecksumAddress
    args: dict[str, Any]
    block_number: int | None = None
    transaction_hash: str | None = None
    log_index: int | None = None


def external_state_event_name(topic: bytes | str) -> str | None:
    """The ExternalState event behind ``topic0``, or None for any other log."""
    raw = (
        bytes.fromhex(topic[2:] if topic.startswith("0x") else topic)
        if isinstance(topic, str)
        else bytes(topic)
    )
    spec = EXTERNAL_STATE_EVENTS.get(raw)
    return spec.name if spec else None


def _hex(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        return value if value.startswith("0x") else "0x" + value
    return "0x" + bytes(value).hex()


def decode_external_state_event(log: LogReceipt) -> ExternalStateEvent | None:
    """Decode one log; None when its topic is not an ExternalState event,
    ``ExternalStateEventDecodeError`` when it is but the data is malformed."""
    topics = log.get("topics") or []
    if not topics:
        return None
    spec = EXTERNAL_STATE_EVENTS.get(bytes(topics[0]))
    if spec is None:
        return None
    if len(topics) != 1:
        raise ExternalStateEventDecodeError(
            f"{spec.name}: {len(topics) - 1} indexed topic(s), the declaration has none"
        )
    data = log["data"]
    raw = bytes.fromhex(data[2:]) if isinstance(data, str) else bytes(data)
    try:
        values = decode([abi_type for abi_type, _ in spec.params], raw)
    except DecodingError as exc:
        raise ExternalStateEventDecodeError(f"{spec.name}: {exc}") from exc
    if len(raw) != 32 * len(spec.params):
        raise ExternalStateEventDecodeError(
            f"{spec.name}: {len(raw)} data bytes, {32 * len(spec.params)} declared"
        )
    args = {
        name: Web3.to_checksum_address(value) if abi_type == "address" else value
        for (abi_type, name), value in zip(spec.params, values, strict=True)
    }
    return ExternalStateEvent(
        name=spec.name,
        emitter=Web3.to_checksum_address(log["address"]),
        args=args,
        block_number=log.get("blockNumber"),
        transaction_hash=_hex(log.get("transactionHash")),
        log_index=log.get("logIndex"),
    )


def external_state_events(receipt: TxReceipt) -> list[ExternalStateEvent]:
    """Every ExternalState event in ``receipt``, in log order."""
    return [
        event
        for log in receipt["logs"]
        if (event := decode_external_state_event(log)) is not None
    ]

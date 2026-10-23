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
from typing import Literal, TypeAlias, TypedDict

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import Timestamp, TxReceipt

from ipor_fusion.core.logs import RawLog
from ipor_fusion.errors import ExternalStateEventDecodeError
from ipor_fusion.fuses.events import decode_event_log, event_name
from ipor_fusion.types import Amount, FuseAddress, MarketId, TxHash


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
        "ExternalStateBalanceFuseLastCustodianConfirmCountUpdated",
        (("uint256", "oldConfirmCount"), ("uint256", "newConfirmCount")),
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
    ExternalStateEventSpec(
        "ExternalStateOperationFuseSweepSurplus",
        (("address", "version"), ("address", "asset"), ("uint256", "amount")),
    ),
    # ExternalStateExecutorStorageLib
    ExternalStateEventSpec(
        "ExternalStateExecutorDeployed",
        (("address", "executor"), ("uint256", "marketId")),
    ),
)


class ExternalStateBigChangeDetectedArgs(TypedDict):
    previousTotal: Amount
    newTotal: Amount
    thresholdBps: int


class ExternalStateCustodianTimestampUpdatedArgs(TypedDict):
    """``ExternalStateBalanceFuseLastCustodianTimestampUpdated``."""

    oldTimestamp: Timestamp
    newTimestamp: Timestamp


class ExternalStateCustodianConfirmCountUpdatedArgs(TypedDict):
    """``ExternalStateBalanceFuseLastCustodianConfirmCountUpdated`` (balance
    fuses of the 2026-10-21 contracts release; earlier ones emit the
    timestamp event)."""

    oldConfirmCount: int
    newConfirmCount: int


class ExternalStateOperationFuseSweepSurplusArgs(TypedDict):
    """``ExternalStateOperationFuseSweepSurplus``: ``amount`` of ``asset``
    swept from the executor to the vault."""

    version: FuseAddress
    asset: ChecksumAddress
    amount: Amount


class ExternalStateTotalBalanceUpdatedArgs(TypedDict):
    """``ExternalStateBalanceFuseLastTotalBalanceUpdated``."""

    oldTotalBalance: Amount
    newTotalBalance: Amount


class ExternalStateUnpausedArgs(TypedDict):
    signer: ChecksumAddress
    confirmedTotalBalance: Amount
    nonce: int


class ExternalStateAssetRescuedArgs(TypedDict):
    asset: ChecksumAddress


class ExternalStateExecutorArgs(TypedDict):
    """``ExecutorCreated`` and ``ExternalStateExecutorDeployed``."""

    executor: ChecksumAddress
    marketId: MarketId


class ExternalStateOperationArgs(TypedDict):
    """``ExternalStateOperationFuseEnter`` and ``ExternalStateOperationFuseExit``;
    ``version`` is the operation fuse's address."""

    version: FuseAddress
    asset: ChecksumAddress
    amount: Amount
    balanceAccount: ChecksumAddress
    valueInUnderlying: Amount
    actionsCount: int


#: The parameters of one ExternalState event, by its Solidity names.
ExternalStateEventArgs: TypeAlias = (
    ExternalStateBigChangeDetectedArgs
    | ExternalStateCustodianTimestampUpdatedArgs
    | ExternalStateTotalBalanceUpdatedArgs
    | ExternalStateUnpausedArgs
    | ExternalStateAssetRescuedArgs
    | ExternalStateExecutorArgs
    | ExternalStateOperationArgs
)

#: topic0 -> spec, for every ExternalState event the vault emits.
EXTERNAL_STATE_EVENTS: dict[bytes, ExternalStateEventSpec] = {
    spec.topic: spec for spec in EXTERNAL_STATE_EVENT_SPECS
}
#: topic0 hex strings, for an ``eth_getLogs`` / indexer topic filter.
EXTERNAL_STATE_TOPICS: tuple[str, ...] = tuple(
    "0x" + spec.topic.hex() for spec in EXTERNAL_STATE_EVENT_SPECS
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _ExternalStateEventBase:
    """One decoded ExternalState log with its raw identity; each event is a
    subclass that pins ``name`` to a literal and ``args`` to its shape."""

    name: str
    emitter: ChecksumAddress
    args: ExternalStateEventArgs
    block_number: int | None = None
    transaction_hash: TxHash | None = None
    log_index: int | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalStateBigChangeDetectedEvent(_ExternalStateEventBase):
    name: Literal["ExternalStateBigChangeDetected"] = "ExternalStateBigChangeDetected"
    args: ExternalStateBigChangeDetectedArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalStateBalanceFuseLastCustodianTimestampUpdatedEvent(
    _ExternalStateEventBase
):
    name: Literal["ExternalStateBalanceFuseLastCustodianTimestampUpdated"] = (
        "ExternalStateBalanceFuseLastCustodianTimestampUpdated"
    )
    args: ExternalStateCustodianTimestampUpdatedArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalStateBalanceFuseLastCustodianConfirmCountUpdatedEvent(
    _ExternalStateEventBase
):
    name: Literal["ExternalStateBalanceFuseLastCustodianConfirmCountUpdated"] = (
        "ExternalStateBalanceFuseLastCustodianConfirmCountUpdated"
    )
    args: ExternalStateCustodianConfirmCountUpdatedArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalStateOperationFuseSweepSurplusEvent(_ExternalStateEventBase):
    name: Literal["ExternalStateOperationFuseSweepSurplus"] = (
        "ExternalStateOperationFuseSweepSurplus"
    )
    args: ExternalStateOperationFuseSweepSurplusArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalStateBalanceFuseLastTotalBalanceUpdatedEvent(_ExternalStateEventBase):
    name: Literal["ExternalStateBalanceFuseLastTotalBalanceUpdated"] = (
        "ExternalStateBalanceFuseLastTotalBalanceUpdated"
    )
    args: ExternalStateTotalBalanceUpdatedArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalStateUnpausedEvent(_ExternalStateEventBase):
    name: Literal["ExternalStateUnpaused"] = "ExternalStateUnpaused"
    args: ExternalStateUnpausedArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalStateAssetRescuedEvent(_ExternalStateEventBase):
    name: Literal["ExternalStateAssetRescued"] = "ExternalStateAssetRescued"
    args: ExternalStateAssetRescuedArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalStateExecutorCreatedEvent(_ExternalStateEventBase):
    name: Literal["ExecutorCreated"] = "ExecutorCreated"
    args: ExternalStateExecutorArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalStateOperationFuseEnterEvent(_ExternalStateEventBase):
    name: Literal["ExternalStateOperationFuseEnter"] = "ExternalStateOperationFuseEnter"
    args: ExternalStateOperationArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalStateOperationFuseExitEvent(_ExternalStateEventBase):
    name: Literal["ExternalStateOperationFuseExit"] = "ExternalStateOperationFuseExit"
    args: ExternalStateOperationArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class ExternalStateExecutorDeployedEvent(_ExternalStateEventBase):
    name: Literal["ExternalStateExecutorDeployed"] = "ExternalStateExecutorDeployed"
    args: ExternalStateExecutorArgs


#: One decoded ExternalState event; ``event.name == "..."`` narrows it, and
#: its ``args``, to that event's class.
ExternalStateEvent: TypeAlias = (
    ExternalStateBigChangeDetectedEvent
    | ExternalStateBalanceFuseLastCustodianTimestampUpdatedEvent
    | ExternalStateBalanceFuseLastCustodianConfirmCountUpdatedEvent
    | ExternalStateOperationFuseSweepSurplusEvent
    | ExternalStateBalanceFuseLastTotalBalanceUpdatedEvent
    | ExternalStateUnpausedEvent
    | ExternalStateAssetRescuedEvent
    | ExternalStateExecutorCreatedEvent
    | ExternalStateOperationFuseEnterEvent
    | ExternalStateOperationFuseExitEvent
    | ExternalStateExecutorDeployedEvent
)

#: Event name -> its event class (``args`` annotation: its TypedDict).
EXTERNAL_STATE_EVENT_CLASSES: dict[str, type[ExternalStateEvent]] = {
    "ExternalStateBigChangeDetected": ExternalStateBigChangeDetectedEvent,
    "ExternalStateBalanceFuseLastCustodianTimestampUpdated": ExternalStateBalanceFuseLastCustodianTimestampUpdatedEvent,
    "ExternalStateBalanceFuseLastCustodianConfirmCountUpdated": ExternalStateBalanceFuseLastCustodianConfirmCountUpdatedEvent,
    "ExternalStateOperationFuseSweepSurplus": ExternalStateOperationFuseSweepSurplusEvent,
    "ExternalStateBalanceFuseLastTotalBalanceUpdated": ExternalStateBalanceFuseLastTotalBalanceUpdatedEvent,
    "ExternalStateUnpaused": ExternalStateUnpausedEvent,
    "ExternalStateAssetRescued": ExternalStateAssetRescuedEvent,
    "ExecutorCreated": ExternalStateExecutorCreatedEvent,
    "ExternalStateOperationFuseEnter": ExternalStateOperationFuseEnterEvent,
    "ExternalStateOperationFuseExit": ExternalStateOperationFuseExitEvent,
    "ExternalStateExecutorDeployed": ExternalStateExecutorDeployedEvent,
}


def external_state_event_name(topic: bytes | str) -> str | None:
    """The ExternalState event behind ``topic0``; None for any other topic and
    for a value that cannot be a topic."""
    return event_name(topic, EXTERNAL_STATE_EVENTS)


def decode_external_state_event(log: RawLog) -> ExternalStateEvent | None:
    """Decode one log — a web3 receipt log or the raw JSON-RPC / indexer
    mapping (hex text topics, data, hashes and quantities). None when its
    topic is not an ExternalState event; once the topic is recognized, any
    malformed part (data, topic count, identity) raises
    ``ExternalStateEventDecodeError``."""
    return decode_event_log(
        log,
        EXTERNAL_STATE_EVENTS,
        EXTERNAL_STATE_EVENT_CLASSES,
        ExternalStateEventDecodeError,
    )


def external_state_events(receipt: TxReceipt) -> list[ExternalStateEvent]:
    """Every ExternalState event in ``receipt``, in log order."""
    return [
        event
        for log in receipt["logs"]
        if (event := decode_external_state_event(dict(log))) is not None
    ]

"""HyperCore events by topic: the fuses', the pending library's, the
settlement fuse's and the settlement reporter's.

Mirrors the ``event`` declarations in ``contracts/fuses/hypercore``. Every
parameter is unindexed (the repository's event policy), so a log decodes from
its ``data`` alone; the first parameter of a fuse event, ``version``, is the
fuse's own address (``VERSION``). ``HyperCoreActionEnqueued`` is emitted by
the vault (the fuses run as delegatecalls) once per enqueued action and
``HyperCoreActionSettled`` only from a settlement fuse transaction; TIMING
settlements emit nothing.

Status: preview, not production. The contracts behind it are under active development, the mainnet deployments are a proof of concept and IPOR Labs canaries, and interfaces may change between minor versions; see ``ipor_fusion.about.PREVIEW_FEATURES``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal, TypeAlias, TypedDict

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import Timestamp, TxReceipt

from ipor_fusion.core.logs import RawLog
from ipor_fusion.errors import HyperCoreEventDecodeError
from ipor_fusion.fuses.events import decode_event_log, event_name
from ipor_fusion.types import (
    Amount,
    FuseAddress,
    HyperCoreActionId,
    HyperCoreAsset,
    HyperCoreClientOrderId,
    HyperCoreOrderId,
    HyperCoreTokenIndex,
    PayloadHash,
    TxHash,
)


@dataclass(frozen=True, slots=True)
class HyperCoreEventSpec:
    """One event: its name and ``(abi_type, parameter_name)`` pairs."""

    name: str
    params: tuple[tuple[str, str], ...]

    @property
    def signature(self) -> str:
        return f"{self.name}({','.join(abi_type for abi_type, _ in self.params)})"

    @property
    def topic(self) -> bytes:
        return bytes(Web3.keccak(text=self.signature))


HYPERCORE_EVENT_SPECS: tuple[HyperCoreEventSpec, ...] = (
    # fuses (first parameter: the fuse's VERSION)
    HyperCoreEventSpec(
        "HyperCoreDepositFuseEnter",
        (
            ("address", "version"),
            ("uint64", "tokenIndex"),
            ("address", "evmAsset"),
            ("uint256", "amount"),
            ("uint64", "coreWei"),
        ),
    ),
    HyperCoreEventSpec(
        "HyperCoreMarginFuseEnter",
        (("address", "version"), ("uint64", "usd6"), ("bool", "toPerp")),
    ),
    HyperCoreEventSpec(
        "HyperCoreSendFuseSpotSend",
        (
            ("address", "version"),
            ("address", "destination"),
            ("uint64", "tokenIndex"),
            ("uint64", "amountWei"),
        ),
    ),
    HyperCoreEventSpec(
        "HyperCoreSendFuseSendAsset",
        (
            ("address", "version"),
            ("address", "destination"),
            ("uint32", "sourceDex"),
            ("uint32", "destinationDex"),
            ("uint64", "tokenIndex"),
            ("uint64", "amountWei"),
        ),
    ),
    HyperCoreEventSpec(
        "HyperCoreOrderFuseEnter",
        (
            ("address", "version"),
            ("uint32", "asset"),
            ("bool", "isBuy"),
            ("uint64", "limitPx"),
            ("uint64", "sz"),
            ("bool", "reduceOnly"),
            ("uint8", "tif"),
            ("uint128", "cloid"),
        ),
    ),
    HyperCoreEventSpec(
        "HyperCoreCancelFuseCancelByOid",
        (("address", "version"), ("uint32", "asset"), ("uint64", "oid")),
    ),
    HyperCoreEventSpec(
        "HyperCoreCancelFuseCancelByCloid",
        (("address", "version"), ("uint32", "asset"), ("uint128", "cloid")),
    ),
    HyperCoreEventSpec(
        "HyperCoreBuilderFeeFuseEnter",
        (
            ("address", "version"),
            ("address", "builder"),
            ("uint64", "maxFeeRateDecibps"),
        ),
    ),
    # HyperCorePendingLib (emitted by the vault)
    HyperCoreEventSpec(
        "HyperCoreActionEnqueued",
        (
            ("uint256", "nonce"),
            ("uint8", "actionClass"),
            ("uint8", "settlementMode"),
            ("uint64", "pendingUntil"),
            ("uint256", "cachedValueWad"),
            ("uint64", "enqueuedL1Block"),
            ("uint64", "enqueuedEvmBlock"),
            ("uint24", "actionId"),
            ("bytes32", "payloadHash"),
        ),
    ),
    HyperCoreEventSpec("HyperCoreActionSettled", (("uint256", "nonce"),)),
    # HyperCoreSettlementFuse
    HyperCoreEventSpec(
        "HyperCoreReportedActionSettled",
        (
            ("uint256", "nonce"),
            ("uint24", "actionId"),
            ("bytes32", "payloadHash"),
            ("uint8", "outcome"),
            ("uint64", "resultL1Block"),
            ("bytes32", "evidenceHash"),
            ("bytes32", "reasonHash"),
        ),
    ),
    # HyperCoreSettlementReporter
    HyperCoreEventSpec(
        "ReportSubmitted",
        (("bytes32", "digest"), ("uint256", "nonce"), ("uint8", "outcome")),
    ),
    HyperCoreEventSpec("ObserverSetProposed", (("uint64", "executeAfter"),)),
    HyperCoreEventSpec(
        "ObserverSetChanged",
        (("address", "observer0"), ("address", "observer1"), ("address", "observer2")),
    ),
    HyperCoreEventSpec(
        "RecoveryProposed",
        (
            ("bytes32", "digest"),
            ("bytes32", "evidenceHash"),
            ("bytes32", "reasonHash"),
            ("uint64", "executeAfter"),
        ),
    ),
    HyperCoreEventSpec(
        "RecoveryExecuted",
        (("bytes32", "digest"), ("bytes32", "evidenceHash"), ("bytes32", "reasonHash")),
    ),
)

# Event parameters by their Solidity names; ``version`` is the emitting fuse.


class HyperCoreDepositFuseEnterArgs(TypedDict):
    version: FuseAddress
    tokenIndex: HyperCoreTokenIndex
    evmAsset: ChecksumAddress
    amount: Amount
    coreWei: int


class HyperCoreMarginFuseEnterArgs(TypedDict):
    version: FuseAddress
    usd6: int
    toPerp: bool


class HyperCoreSendFuseSpotSendArgs(TypedDict):
    version: FuseAddress
    destination: ChecksumAddress
    tokenIndex: HyperCoreTokenIndex
    amountWei: int


class HyperCoreSendFuseSendAssetArgs(TypedDict):
    version: FuseAddress
    destination: ChecksumAddress
    sourceDex: int
    destinationDex: int
    tokenIndex: HyperCoreTokenIndex
    amountWei: int


class HyperCoreOrderFuseEnterArgs(TypedDict):
    version: FuseAddress
    asset: HyperCoreAsset
    isBuy: bool
    limitPx: int
    sz: int
    reduceOnly: bool
    tif: int
    cloid: HyperCoreClientOrderId


class HyperCoreCancelByOidArgs(TypedDict):
    version: FuseAddress
    asset: HyperCoreAsset
    oid: HyperCoreOrderId


class HyperCoreCancelByCloidArgs(TypedDict):
    version: FuseAddress
    asset: HyperCoreAsset
    cloid: HyperCoreClientOrderId


class HyperCoreBuilderFeeFuseEnterArgs(TypedDict):
    version: FuseAddress
    builder: ChecksumAddress
    maxFeeRateDecibps: int


class HyperCoreActionEnqueuedArgs(TypedDict):
    nonce: int
    actionClass: int
    settlementMode: int
    pendingUntil: Timestamp
    cachedValueWad: int
    enqueuedL1Block: int
    enqueuedEvmBlock: int
    actionId: HyperCoreActionId
    payloadHash: PayloadHash


class HyperCoreActionSettledArgs(TypedDict):
    nonce: int


class HyperCoreReportedActionSettledArgs(TypedDict):
    nonce: int
    actionId: HyperCoreActionId
    payloadHash: PayloadHash
    outcome: int
    resultL1Block: int
    evidenceHash: bytes
    reasonHash: bytes


class HyperCoreReportSubmittedArgs(TypedDict):
    digest: bytes
    nonce: int
    outcome: int


class HyperCoreObserverSetProposedArgs(TypedDict):
    executeAfter: Timestamp


class HyperCoreObserverSetChangedArgs(TypedDict):
    observer0: ChecksumAddress
    observer1: ChecksumAddress
    observer2: ChecksumAddress


class HyperCoreRecoveryProposedArgs(TypedDict):
    digest: bytes
    evidenceHash: bytes
    reasonHash: bytes
    executeAfter: Timestamp


class HyperCoreRecoveryExecutedArgs(TypedDict):
    digest: bytes
    evidenceHash: bytes
    reasonHash: bytes


#: The parameters of one HyperCore event.
HyperCoreEventArgs: TypeAlias = (
    HyperCoreDepositFuseEnterArgs
    | HyperCoreMarginFuseEnterArgs
    | HyperCoreSendFuseSpotSendArgs
    | HyperCoreSendFuseSendAssetArgs
    | HyperCoreOrderFuseEnterArgs
    | HyperCoreCancelByOidArgs
    | HyperCoreCancelByCloidArgs
    | HyperCoreBuilderFeeFuseEnterArgs
    | HyperCoreActionEnqueuedArgs
    | HyperCoreActionSettledArgs
    | HyperCoreReportedActionSettledArgs
    | HyperCoreReportSubmittedArgs
    | HyperCoreObserverSetProposedArgs
    | HyperCoreObserverSetChangedArgs
    | HyperCoreRecoveryProposedArgs
    | HyperCoreRecoveryExecutedArgs
)

#: topic0 -> spec, for every HyperCore event.
HYPERCORE_EVENTS: dict[bytes, HyperCoreEventSpec] = {
    spec.topic: spec for spec in HYPERCORE_EVENT_SPECS
}
#: Names of the events whose first parameter is the emitting fuse's address.
HYPERCORE_FUSE_EVENTS = frozenset(
    spec.name
    for spec in HYPERCORE_EVENT_SPECS
    if spec.params[:1] == (("address", "version"),)
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _HyperCoreEventBase:
    """One decoded HyperCore log with its raw identity; each event is a
    subclass that pins ``name`` to a literal and ``args`` to its shape."""

    name: str
    emitter: ChecksumAddress
    args: HyperCoreEventArgs
    block_number: int | None = None
    transaction_hash: TxHash | None = None
    log_index: int | None = None

    @property
    def fuse(self) -> FuseAddress | None:
        """The fuse that emitted a fuse event (its ``version``), else None."""
        if self.name not in HYPERCORE_FUSE_EVENTS or "version" not in self.args:
            return None
        return self.args["version"]


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreDepositFuseEnterEvent(_HyperCoreEventBase):
    name: Literal["HyperCoreDepositFuseEnter"] = "HyperCoreDepositFuseEnter"
    args: HyperCoreDepositFuseEnterArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreMarginFuseEnterEvent(_HyperCoreEventBase):
    name: Literal["HyperCoreMarginFuseEnter"] = "HyperCoreMarginFuseEnter"
    args: HyperCoreMarginFuseEnterArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreSendFuseSpotSendEvent(_HyperCoreEventBase):
    name: Literal["HyperCoreSendFuseSpotSend"] = "HyperCoreSendFuseSpotSend"
    args: HyperCoreSendFuseSpotSendArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreSendFuseSendAssetEvent(_HyperCoreEventBase):
    name: Literal["HyperCoreSendFuseSendAsset"] = "HyperCoreSendFuseSendAsset"
    args: HyperCoreSendFuseSendAssetArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreOrderFuseEnterEvent(_HyperCoreEventBase):
    name: Literal["HyperCoreOrderFuseEnter"] = "HyperCoreOrderFuseEnter"
    args: HyperCoreOrderFuseEnterArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreCancelFuseCancelByOidEvent(_HyperCoreEventBase):
    name: Literal["HyperCoreCancelFuseCancelByOid"] = "HyperCoreCancelFuseCancelByOid"
    args: HyperCoreCancelByOidArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreCancelFuseCancelByCloidEvent(_HyperCoreEventBase):
    name: Literal["HyperCoreCancelFuseCancelByCloid"] = (
        "HyperCoreCancelFuseCancelByCloid"
    )
    args: HyperCoreCancelByCloidArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreBuilderFeeFuseEnterEvent(_HyperCoreEventBase):
    name: Literal["HyperCoreBuilderFeeFuseEnter"] = "HyperCoreBuilderFeeFuseEnter"
    args: HyperCoreBuilderFeeFuseEnterArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreActionEnqueuedEvent(_HyperCoreEventBase):
    name: Literal["HyperCoreActionEnqueued"] = "HyperCoreActionEnqueued"
    args: HyperCoreActionEnqueuedArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreActionSettledEvent(_HyperCoreEventBase):
    name: Literal["HyperCoreActionSettled"] = "HyperCoreActionSettled"
    args: HyperCoreActionSettledArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreReportedActionSettledEvent(_HyperCoreEventBase):
    name: Literal["HyperCoreReportedActionSettled"] = "HyperCoreReportedActionSettled"
    args: HyperCoreReportedActionSettledArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreReportSubmittedEvent(_HyperCoreEventBase):
    name: Literal["ReportSubmitted"] = "ReportSubmitted"
    args: HyperCoreReportSubmittedArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreObserverSetProposedEvent(_HyperCoreEventBase):
    name: Literal["ObserverSetProposed"] = "ObserverSetProposed"
    args: HyperCoreObserverSetProposedArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreObserverSetChangedEvent(_HyperCoreEventBase):
    name: Literal["ObserverSetChanged"] = "ObserverSetChanged"
    args: HyperCoreObserverSetChangedArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreRecoveryProposedEvent(_HyperCoreEventBase):
    name: Literal["RecoveryProposed"] = "RecoveryProposed"
    args: HyperCoreRecoveryProposedArgs


@dataclass(frozen=True, slots=True, kw_only=True)
class HyperCoreRecoveryExecutedEvent(_HyperCoreEventBase):
    name: Literal["RecoveryExecuted"] = "RecoveryExecuted"
    args: HyperCoreRecoveryExecutedArgs


#: One decoded HyperCore event; ``event.name == "..."`` narrows it, and its
#: ``args``, to that event's class.
HyperCoreEvent: TypeAlias = (
    HyperCoreDepositFuseEnterEvent
    | HyperCoreMarginFuseEnterEvent
    | HyperCoreSendFuseSpotSendEvent
    | HyperCoreSendFuseSendAssetEvent
    | HyperCoreOrderFuseEnterEvent
    | HyperCoreCancelFuseCancelByOidEvent
    | HyperCoreCancelFuseCancelByCloidEvent
    | HyperCoreBuilderFeeFuseEnterEvent
    | HyperCoreActionEnqueuedEvent
    | HyperCoreActionSettledEvent
    | HyperCoreReportedActionSettledEvent
    | HyperCoreReportSubmittedEvent
    | HyperCoreObserverSetProposedEvent
    | HyperCoreObserverSetChangedEvent
    | HyperCoreRecoveryProposedEvent
    | HyperCoreRecoveryExecutedEvent
)

#: Event name -> its event class (``args`` annotation: its TypedDict).
HYPERCORE_EVENT_CLASSES: dict[str, type[HyperCoreEvent]] = {
    "HyperCoreDepositFuseEnter": HyperCoreDepositFuseEnterEvent,
    "HyperCoreMarginFuseEnter": HyperCoreMarginFuseEnterEvent,
    "HyperCoreSendFuseSpotSend": HyperCoreSendFuseSpotSendEvent,
    "HyperCoreSendFuseSendAsset": HyperCoreSendFuseSendAssetEvent,
    "HyperCoreOrderFuseEnter": HyperCoreOrderFuseEnterEvent,
    "HyperCoreCancelFuseCancelByOid": HyperCoreCancelFuseCancelByOidEvent,
    "HyperCoreCancelFuseCancelByCloid": HyperCoreCancelFuseCancelByCloidEvent,
    "HyperCoreBuilderFeeFuseEnter": HyperCoreBuilderFeeFuseEnterEvent,
    "HyperCoreActionEnqueued": HyperCoreActionEnqueuedEvent,
    "HyperCoreActionSettled": HyperCoreActionSettledEvent,
    "HyperCoreReportedActionSettled": HyperCoreReportedActionSettledEvent,
    "ReportSubmitted": HyperCoreReportSubmittedEvent,
    "ObserverSetProposed": HyperCoreObserverSetProposedEvent,
    "ObserverSetChanged": HyperCoreObserverSetChangedEvent,
    "RecoveryProposed": HyperCoreRecoveryProposedEvent,
    "RecoveryExecuted": HyperCoreRecoveryExecutedEvent,
}


def hypercore_event_name(topic: bytes | str) -> str | None:
    """The HyperCore event behind ``topic0``; None for any other topic and for
    a value that cannot be a topic."""
    return event_name(topic, HYPERCORE_EVENTS)


def decode_hypercore_event(log: RawLog) -> HyperCoreEvent | None:
    """Decode one log — a web3 receipt log or the raw JSON-RPC / indexer
    mapping (hex text topics, data, hashes and quantities). None when its
    topic is not a HyperCore event; once the topic is recognized, any
    malformed part (data, topic count, identity) raises
    ``HyperCoreEventDecodeError``."""
    return decode_event_log(
        log, HYPERCORE_EVENTS, HYPERCORE_EVENT_CLASSES, HyperCoreEventDecodeError
    )


def hypercore_events(receipt: TxReceipt) -> list[HyperCoreEvent]:
    """Every HyperCore event in ``receipt``, in log order."""
    return [
        event
        for log in receipt["logs"]
        if (event := decode_hypercore_event(log)) is not None
    ]


def find_hypercore_events(receipt: TxReceipt, name: str) -> list[HyperCoreEvent]:
    """The HyperCore events named ``name`` in ``receipt``, in log order."""
    if name not in {spec.name for spec in HYPERCORE_EVENT_SPECS}:
        raise ValueError(f"unknown HyperCore event {name!r}")
    return [event for event in hypercore_events(receipt) if event.name == name]

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
from typing import Any

from eth_abi import decode
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import LogReceipt, TxReceipt


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


@dataclass(frozen=True, slots=True)
class HyperCoreEvent:
    """One decoded HyperCore log."""

    name: str
    emitter: ChecksumAddress
    args: dict[str, Any]
    log_index: int | None = None

    @property
    def fuse(self) -> ChecksumAddress | None:
        """The fuse that emitted a fuse event (its ``version``), else None."""
        if self.name not in HYPERCORE_FUSE_EVENTS:
            return None
        return Web3.to_checksum_address(self.args["version"])


def hypercore_event_name(topic: bytes | str) -> str | None:
    """The HyperCore event behind ``topic0``, or None for any other log."""
    raw = (
        bytes.fromhex(topic[2:] if topic.startswith("0x") else topic)
        if isinstance(topic, str)
        else bytes(topic)
    )
    spec = HYPERCORE_EVENTS.get(raw)
    return spec.name if spec else None


def decode_hypercore_event(log: LogReceipt) -> HyperCoreEvent | None:
    """Decode one receipt log; None when it is not a HyperCore event."""
    topics = log.get("topics") or []
    if not topics:
        return None
    spec = HYPERCORE_EVENTS.get(bytes(topics[0]))
    if spec is None:
        return None
    values = decode([abi_type for abi_type, _ in spec.params], bytes(log["data"]))
    args = {
        name: Web3.to_checksum_address(value) if abi_type == "address" else value
        for (abi_type, name), value in zip(spec.params, values, strict=True)
    }
    return HyperCoreEvent(
        name=spec.name,
        emitter=Web3.to_checksum_address(log["address"]),
        args=args,
        log_index=log.get("logIndex"),
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

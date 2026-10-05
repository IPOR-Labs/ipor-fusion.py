"""Topic-keyed decoder for the events of the Chainlink CCIP crosschain
contracts: ``CcipCrosschainExecutor``, ``CcipCrosschainDispatcher``,
``CcipCrosschainFactory``, their libraries (``contracts/crosschain/ccip/lib``)
and ``CcipAppBase``.

The registry mirrors the contracts source the SDK pins (see
:data:`CCIP_EVENTS`). The deployed pilot-v2 and v3 factory pairs share every
event; v3 adds the two ``DispatcherDeploymentGasLimit*`` factory events, which
a v2 factory simply never emits.

No CCIP event has an ``indexed`` parameter: topic0 is the only topic and every
value is ABI-encoded in the log data. Enum parameters (``CcipMsgType``) are
``uint8`` on the wire; the struct parameter ``CcipRouteConfig`` is the ABI
tuple in :data:`_ROUTE_CONFIG` and decodes to a tuple of its members.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from types import MappingProxyType
from typing import Any

from eth_abi import decode
from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3 import Web3

from ipor_fusion.crosschain.logs import log_address, log_bytes, log_topic0


@dataclass(frozen=True, slots=True)
class CcipEventSpec:
    """One event signature: its name and its parameter names and ABI types in
    declaration order."""

    name: str
    fields: tuple[str, ...]
    types: tuple[str, ...]

    @property
    def signature(self) -> str:
        return f"{self.name}({','.join(self.types)})"

    @property
    def topic(self) -> bytes:
        """topic0 of the event: the keccak of :attr:`signature`."""
        return keccak(text=self.signature)


@dataclass(frozen=True, slots=True)
class CcipEvent:
    """One decoded log: the event name, the emitting contract and the parameter
    values by name. ``bytes32`` values are ``bytes``, addresses checksum
    strings, numbers ``int``, flags ``bool``; a struct is a tuple of its
    members."""

    name: str
    address: ChecksumAddress
    values: Mapping[str, object]


#: ``ICcipCrosschain.CcipRouteConfig`` as an ABI tuple.
_ROUTE_CONFIG = "(uint64,address,address,uint96,uint96,uint256,bool)"


def _event(name: str, *params: str) -> CcipEventSpec:
    """A spec from Solidity-style ``"type name"`` parameters in declaration order."""
    types, fields = zip(*(param.rsplit(" ", 1) for param in params), strict=True)
    return CcipEventSpec(name, tuple(fields), tuple(types))


#: Every event, one spec per signature, sorted by signature. Mirrored from
#: ``contracts/crosschain/ccip/**/*.sol`` at the contracts revision
#: ``84c1923`` (the v3 factory pair's source) and held to it by
#: ``tests/test_crosschain_ccip_events.py``.
CCIP_EVENTS: tuple[CcipEventSpec, ...] = (
    _event("AssetClaimed", "address manager", "uint256 amount"),
    _event(
        "AssetConfigExecuted",
        "bytes32 assetId",
        "address token",
        "uint8 sharedDecimals",
    ),
    _event(
        "AssetConfigScheduled",
        "bytes32 assetId",
        "address token",
        "uint8 sharedDecimals",
        "uint256 executableAt",
    ),
    _event(
        "AssetSent",
        "uint256 chainId",
        "bytes32 operationId",
        "bytes32 messageId",
        "uint256 amount",
    ),
    _event(
        "AssetSettled",
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 received",
    ),
    _event("AttestationAnchorRebased", "uint256 chainId", "uint256 principalSD"),
    _event("AttestationAnchorUpdated", "uint256 chainId", "uint256 principalSD"),
    _event(
        "BalanceApproved",
        "uint256 proposalId",
        "uint256 chainId",
        "uint256 balance",
    ),
    _event(
        "BalanceProposed",
        "uint256 proposalId",
        "uint256 chainId",
        "uint256 balance",
        "uint64 stateVersion",
        "bytes32 trackedPositionSetHash",
    ),
    _event("BalanceRejectedAndBlocked", "uint256 proposalId", "uint256 chainId"),
    _event(
        "CcipMessageReceived",
        "bytes32 messageId",
        "uint256 srcChainId",
        "address peer",
    ),
    _event(
        "CcipMessageSent",
        "bytes32 messageId",
        "uint256 dstChainId",
        "bool withToken",
        "uint256 fee",
    ),
    _event("CcipRoutePolicySynced", "address app", "uint256 chainId"),
    _event("CcipRoutePolicyUpdated", "uint256 chainId", f"{_ROUTE_CONFIG} route"),
    _event(
        "CcipRouteRegistered",
        "uint256 chainId",
        "uint64 selector",
        "address peer",
    ),
    _event("ChainUnblockedByApproval", "uint256 chainId", "uint256 proposalId"),
    _event(
        "CommandAbandoned",
        "uint256 chainId",
        "bytes32 commandId",
        "uint64 sequence",
        "bool sequenceConsumed",
    ),
    _event(
        "CommandAcknowledged",
        "uint256 srcChainId",
        "bytes32 commandId",
        "uint64 sequence",
        "uint64 stateVersion",
        "int256 valueDelta",
        "uint8 resolution",
        "uint64 commandConfigEpoch",
    ),
    _event("CommandCancelRequested", "uint256 chainId", "bytes32 commandId"),
    _event("CommandCancelled", "bytes32 commandId", "uint64 sequence"),
    _event("CommandFailed", "bytes32 commandId", "uint64 sequence"),
    _event(
        "CommandFailed",
        "uint256 chainId",
        "bytes32 commandId",
        "uint64 sequence",
    ),
    _event(
        "CommandLaneRealigned",
        "uint256 chainId",
        "uint64 previousSequence",
        "uint64 newSequence",
        "uint64 previousEpoch",
        "uint64 newEpoch",
    ),
    _event(
        "CommandReceiptAdopted",
        "uint256 chainId",
        "bytes32 commandId",
        "uint64 sequence",
        "uint64 dispatcherConfigEpoch",
        "bytes32 supersededCommandId",
    ),
    _event(
        "CommandReceiptIgnored",
        "uint256 chainId",
        "bytes32 commandId",
        "uint8 messageType",
        "uint8 reason",
    ),
    _event("CommandRetryRequested", "uint256 chainId", "bytes32 commandId"),
    _event(
        "CommandSent",
        "uint256 dstChainId",
        "bytes32 commandId",
        "uint64 sequence",
        "uint8 action",
        "bool advancesConfig",
        "uint64 commandConfigEpoch",
    ),
    _event(
        "CommandSucceeded",
        "bytes32 commandId",
        "uint64 sequence",
        "int256 valueDelta",
    ),
    _event("ConfigCancelled", "bytes32 commitment"),
    _event(
        "ConfigEpochResynced",
        "uint256 chainId",
        "uint64 expectedEpoch",
        "uint64 dispatcherEpoch",
    ),
    _event("ConfigScheduled", "bytes32 commitment", "uint256 executableAt"),
    _event(
        "CreationCodesConfigured",
        "bytes32 executorCodeHash",
        "bytes32 dispatcherCodeHash",
    ),
    _event("CreationRestrictionUpdated", "bool restricted"),
    _event("CreatorAllowanceUpdated", "address creator", "bool allowed"),
    _event(
        "DeploymentCancelled",
        "address executor",
        "uint256 dstChainId",
        "bytes32 ticketHash",
    ),
    _event(
        "DeploymentNack",
        "address executor",
        "uint256 dstChainId",
        "bytes32 ticketHash",
        "uint8 reason",
    ),
    _event("DispatcherDeployed", "address dispatcher", "uint256 srcChainId"),
    _event(
        "DispatcherDeploymentGasLimitExecuted", "uint256 chainId", "uint96 gasLimit"
    ),
    _event(
        "DispatcherDeploymentGasLimitScheduled",
        "uint256 chainId",
        "uint96 gasLimit",
        "uint256 executableAt",
    ),
    _event("DispatcherReady", "uint256 chainId"),
    _event(
        "DispatcherRequested",
        "address executor",
        "uint256 dstChainId",
        "bytes32 messageId",
    ),
    _event(
        "ExecutorCreated",
        "address manager",
        "address executor",
        "bytes32 salt",
    ),
    _event(
        "FactoryRouteScheduled",
        "uint256 chainId",
        f"{_ROUTE_CONFIG} route",
        "uint256 executableAt",
    ),
    _event(
        "InboundMessageIgnored",
        "uint256 chainId",
        "uint8 messageType",
        "bytes32 reason",
    ),
    _event(
        "InboundSettled",
        "bytes32 operationId",
        "uint256 amount",
        "uint64 stateVersion",
    ),
    _event(
        "LateReturnFinalized",
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 sentSD",
        "uint256 received",
    ),
    _event("NativeSwept", "address to", "uint256 amount"),
    _event(
        "OutboundForceResolved",
        "bytes32 operationId",
        "uint256 chainId",
        "bool creditedToSettled",
    ),
    _event(
        "ProposalInvalidated",
        "uint256 chainId",
        "uint256 proposalId",
        "uint64 newEpoch",
    ),
    _event(
        "RedeemedFromRequest",
        "address vault",
        "uint256 sharesRedeemed",
        "uint256 assetsDeltaSD",
    ),
    _event(
        "RemoteBalanceUpdated",
        "uint256 chainId",
        "uint256 balance",
        "uint64 stateVersion",
    ),
    _event("ResponseDispatched", "uint64 responseId", "bytes32 messageId"),
    _event(
        "ResponseQueued",
        "uint64 responseId",
        "uint8 messageType",
        "uint256 tokenAmount",
    ),
    _event("ResponseSkipped", "uint64 responseId", "uint256 tokenAmount"),
    _event(
        "ReturnBelowMinimumCredited",
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 received",
        "uint256 minimum",
    ),
    _event("ReturnCancelled", "uint256 chainId", "bytes32 operationId"),
    _event(
        "ReturnDebitExceededAccountingBound",
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 sentSD",
        "uint256 maxAccounted",
    ),
    _event(
        "ReturnFinalized",
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 sentSD",
        "uint256 received",
    ),
    _event(
        "ReturnQueued",
        "bytes32 operationId",
        "uint256 amount",
        "uint64 responseId",
    ),
    _event(
        "ReturnRejected",
        "bytes32 operationId",
        "uint8 reason",
        "uint64 responseId",
    ),
    _event(
        "ReturnRejected",
        "uint256 chainId",
        "bytes32 operationId",
        "uint8 reason",
    ),
    _event(
        "ReturnRequested",
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 amount",
    ),
    _event("SettledBalanceForceSet", "uint256 chainId", "uint256 newSettled"),
    _event(
        "SettledCreditClamped",
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 reported",
        "uint256 credited",
    ),
    _event("SharesRequested", "address vault", "uint256 pendingShares"),
    _event(
        "StaleRemoteBalanceIgnored",
        "uint256 chainId",
        "uint64 currentVersion",
        "uint64 receivedVersion",
    ),
    _event(
        "StateFrontierAdvanced",
        "uint256 chainId",
        "uint64 oldVersion",
        "uint64 newVersion",
    ),
    _event("TokenSwept", "address token", "address to", "uint256 amount"),
    _event("VaultAllowed", "address vault", "bool allowed"),
    _event("VaultTracked", "address vault"),
    _event("VaultUntracked", "address vault"),
    _event(
        "VaultsConfigured",
        "uint64 commandConfigEpoch",
        "bool replace",
        "bool allowed",
        "uint256 count",
    ),
)

#: topic0 to spec, in :data:`CCIP_EVENTS` order.
CCIP_EVENT_TOPICS: dict[bytes, CcipEventSpec] = {
    spec.topic: spec for spec in CCIP_EVENTS
}


def _python_value(abi_type: str, value: Any) -> object:
    """Checksum an address, also inside a struct; ``int``, ``bool`` and
    ``bytes32`` values already decode to the Python type."""
    if abi_type == "address":
        return Web3.to_checksum_address(value)
    if abi_type.startswith("("):
        # Flat split: no CCIP event nests a struct inside a struct.
        return tuple(map(_python_value, abi_type[1:-1].split(","), value))
    return value


def decode_ccip_event(log: Mapping) -> CcipEvent | None:
    """The CCIP event behind a raw log dict as ``eth_simulateV1`` returns it
    (hex strings or bytes), or ``None`` when topic0 is not a known signature."""
    topic = log_topic0(log)
    spec = CCIP_EVENT_TOPICS.get(topic) if topic is not None else None
    if spec is None:
        return None
    raw = decode(list(spec.types), log_bytes(log["data"]))
    values = {
        field: _python_value(abi_type, value)
        for field, abi_type, value in zip(spec.fields, spec.types, raw, strict=True)
    }
    return CcipEvent(
        name=spec.name,
        address=log_address(log),
        values=MappingProxyType(values),
    )


def ccip_events(logs: Iterable[Mapping]) -> list[CcipEvent]:
    """Every known CCIP event among ``logs``, in log order; other logs are skipped."""
    return [event for event in map(decode_ccip_event, logs) if event is not None]


def find_ccip_events(logs: Iterable[Mapping], name: str) -> list[CcipEvent]:
    """The events named ``name`` among ``logs``, in log order."""
    return [event for event in ccip_events(logs) if event.name == name]

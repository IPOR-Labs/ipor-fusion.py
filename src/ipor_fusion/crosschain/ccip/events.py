"""Topic-keyed decoder for the events of the Chainlink CCIP crosschain
contracts: ``CcipCrosschainExecutor``, ``CcipCrosschainDispatcher``,
``CcipCrosschainFactory``, their libraries (``contracts/crosschain/ccip/lib``)
and ``CcipAppBase``.

Two contract generations are covered, see :class:`CcipGeneration`. Where a
signature changed between them (``ReturnFinalized``, ``LateReturnFinalized``,
``CommandAcknowledged``, ``CcipRoutePolicyUpdated``) the registry holds one
spec per signature, each tagged with the generations declaring it, so a decoded
log says which ABI it matched. ``executorInterfaceVersion()`` is 7 on both
generations and therefore does not discriminate them; the factory's
``executorCreationCodeHash`` and the presence of the executor's
``accountingEpoch(uint256)`` / ``activeProposalId(uint256)`` getters do.

No CCIP event has an ``indexed`` parameter: topic0 is the only topic and every
value is ABI-encoded in the log data. Enum parameters (``CcipMsgType``) are
``uint8`` on the wire; the struct parameter ``CcipRouteConfig`` is the ABI
tuple in :data:`_ROUTE_CONFIG` and decodes to a tuple of its members.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import Enum
from types import MappingProxyType
from typing import Any

from eth_abi import decode
from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3 import Web3

from ipor_fusion.crosschain.logs import log_address, log_bytes, log_topic0


class CcipGeneration(Enum):
    """A generation of the CCIP crosschain contracts with its own event ABI."""

    #: The pilot deployed on Arbitrum and HyperEVM: contracts recipe base
    #: commit ``1a0c2308``.
    PILOT = "pilot"
    #: The current contracts source: commit ``827ada0``.
    CURRENT = "current"


@dataclass(frozen=True, slots=True)
class CcipEventSpec:
    """One event signature: its name, the generations declaring it, and its
    parameter names and ABI types in declaration order."""

    name: str
    generations: frozenset[CcipGeneration]
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
    """One decoded log: the event name, the generations whose ABI it matched,
    the emitting contract and the parameter values by name. ``bytes32`` values
    are ``bytes``, addresses checksum strings, numbers ``int``, flags ``bool``;
    a struct is a tuple of its members."""

    name: str
    generations: frozenset[CcipGeneration]
    address: ChecksumAddress
    values: Mapping[str, object]


_PILOT = frozenset({CcipGeneration.PILOT})
_CURRENT = frozenset({CcipGeneration.CURRENT})
_BOTH = _PILOT | _CURRENT

#: ``ICcipCrosschain.CcipRouteConfig`` as an ABI tuple, same in both generations.
_ROUTE_CONFIG = "(uint64,address,address,uint96,uint96,uint256,bool)"


def _event(
    name: str, generations: frozenset[CcipGeneration], *params: str
) -> CcipEventSpec:
    """A spec from Solidity-style ``"type name"`` parameters in declaration order."""
    types, fields = zip(*(param.rsplit(" ", 1) for param in params), strict=True)
    return CcipEventSpec(name, generations, tuple(fields), tuple(types))


#: Every event of both generations, one spec per distinct signature, sorted by
#: signature. Mirrored from ``contracts/crosschain/ccip/**/*.sol``: the CURRENT
#: generation by ``tests/test_crosschain_ccip_events.py`` against a checkout at
#: ``827ada0``, the PILOT generation by hand against ``1a0c2308``.
CCIP_EVENTS: tuple[CcipEventSpec, ...] = (
    _event("AssetClaimed", _CURRENT, "address manager", "uint256 amount"),
    _event(
        "AssetConfigExecuted",
        _CURRENT,
        "bytes32 assetId",
        "address token",
        "uint8 sharedDecimals",
    ),
    _event(
        "AssetConfigScheduled",
        _CURRENT,
        "bytes32 assetId",
        "address token",
        "uint8 sharedDecimals",
        "uint256 executableAt",
    ),
    _event(
        "AssetSent",
        _BOTH,
        "uint256 chainId",
        "bytes32 operationId",
        "bytes32 messageId",
        "uint256 amount",
    ),
    _event(
        "AssetSettled",
        _BOTH,
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 received",
    ),
    _event("AttestationAnchorRebased", _BOTH, "uint256 chainId", "uint256 principalSD"),
    _event(
        "AttestationAnchorUpdated", _CURRENT, "uint256 chainId", "uint256 principalSD"
    ),
    _event(
        "BalanceApproved",
        _BOTH,
        "uint256 proposalId",
        "uint256 chainId",
        "uint256 balance",
    ),
    _event(
        "BalanceProposed",
        _BOTH,
        "uint256 proposalId",
        "uint256 chainId",
        "uint256 balance",
        "uint64 stateVersion",
        "bytes32 trackedPositionSetHash",
    ),
    _event("BalanceRejectedAndBlocked", _BOTH, "uint256 proposalId", "uint256 chainId"),
    _event(
        "CcipMessageReceived",
        _BOTH,
        "bytes32 messageId",
        "uint256 srcChainId",
        "address peer",
    ),
    _event(
        "CcipMessageSent",
        _BOTH,
        "bytes32 messageId",
        "uint256 dstChainId",
        "bool withToken",
        "uint256 fee",
    ),
    _event("CcipRoutePolicySynced", _BOTH, "address app", "uint256 chainId"),
    _event(
        "CcipRoutePolicyUpdated", _CURRENT, "uint256 chainId", f"{_ROUTE_CONFIG} route"
    ),
    _event(
        "CcipRoutePolicyUpdated",
        _PILOT,
        "uint256 chainId",
        "bytes32 oldPolicyHash",
        "bytes32 newPolicyHash",
    ),
    _event(
        "CcipRouteRegistered",
        _BOTH,
        "uint256 chainId",
        "uint64 selector",
        "address peer",
    ),
    _event(
        "ChainUnblockedByApproval", _CURRENT, "uint256 chainId", "uint256 proposalId"
    ),
    _event(
        "CommandAbandoned",
        _BOTH,
        "uint256 chainId",
        "bytes32 commandId",
        "uint64 sequence",
        "bool sequenceConsumed",
    ),
    _event(
        "CommandAcknowledged",
        _PILOT,
        "uint256 chainId",
        "bytes32 commandId",
        "uint64 sequence",
    ),
    _event(
        "CommandAcknowledged",
        _CURRENT,
        "uint256 srcChainId",
        "bytes32 commandId",
        "uint64 sequence",
        "uint64 stateVersion",
        "int256 valueDelta",
        "uint8 resolution",
        "uint64 commandConfigEpoch",
    ),
    _event("CommandCancelRequested", _BOTH, "uint256 chainId", "bytes32 commandId"),
    _event("CommandCancelled", _BOTH, "bytes32 commandId", "uint64 sequence"),
    _event("CommandFailed", _BOTH, "bytes32 commandId", "uint64 sequence"),
    _event(
        "CommandFailed",
        _BOTH,
        "uint256 chainId",
        "bytes32 commandId",
        "uint64 sequence",
    ),
    _event(
        "CommandLaneRealigned",
        _BOTH,
        "uint256 chainId",
        "uint64 previousSequence",
        "uint64 newSequence",
        "uint64 previousEpoch",
        "uint64 newEpoch",
    ),
    _event(
        "CommandReceiptAdopted",
        _BOTH,
        "uint256 chainId",
        "bytes32 commandId",
        "uint64 sequence",
        "uint64 dispatcherConfigEpoch",
        "bytes32 supersededCommandId",
    ),
    _event(
        "CommandReceiptIgnored",
        _BOTH,
        "uint256 chainId",
        "bytes32 commandId",
        "uint8 messageType",
        "uint8 reason",
    ),
    _event("CommandRetryRequested", _CURRENT, "uint256 chainId", "bytes32 commandId"),
    _event(
        "CommandSent",
        _CURRENT,
        "uint256 dstChainId",
        "bytes32 commandId",
        "uint64 sequence",
        "uint8 action",
        "bool advancesConfig",
        "uint64 commandConfigEpoch",
    ),
    _event(
        "CommandSucceeded",
        _BOTH,
        "bytes32 commandId",
        "uint64 sequence",
        "int256 valueDelta",
    ),
    _event("ConfigCancelled", _BOTH, "bytes32 commitment"),
    _event(
        "ConfigEpochResynced",
        _BOTH,
        "uint256 chainId",
        "uint64 expectedEpoch",
        "uint64 dispatcherEpoch",
    ),
    _event("ConfigScheduled", _BOTH, "bytes32 commitment", "uint256 executableAt"),
    _event(
        "CreationCodesConfigured",
        _CURRENT,
        "bytes32 executorCodeHash",
        "bytes32 dispatcherCodeHash",
    ),
    _event("CreationRestrictionUpdated", _BOTH, "bool restricted"),
    _event("CreatorAllowanceUpdated", _BOTH, "address creator", "bool allowed"),
    _event(
        "DeploymentCancelled",
        _BOTH,
        "address executor",
        "uint256 dstChainId",
        "bytes32 ticketHash",
    ),
    _event(
        "DeploymentNack",
        _BOTH,
        "address executor",
        "uint256 dstChainId",
        "bytes32 ticketHash",
        "uint8 reason",
    ),
    _event("DispatcherDeployed", _BOTH, "address dispatcher", "uint256 srcChainId"),
    _event("DispatcherReady", _BOTH, "uint256 chainId"),
    _event(
        "DispatcherRequested",
        _BOTH,
        "address executor",
        "uint256 dstChainId",
        "bytes32 messageId",
    ),
    _event(
        "ExecutorCreated",
        _BOTH,
        "address manager",
        "address executor",
        "bytes32 salt",
    ),
    _event(
        "FactoryRouteScheduled",
        _CURRENT,
        "uint256 chainId",
        f"{_ROUTE_CONFIG} route",
        "uint256 executableAt",
    ),
    _event(
        "InboundMessageIgnored",
        _BOTH,
        "uint256 chainId",
        "uint8 messageType",
        "bytes32 reason",
    ),
    _event(
        "InboundSettled",
        _BOTH,
        "bytes32 operationId",
        "uint256 amount",
        "uint64 stateVersion",
    ),
    _event(
        "LateReturnFinalized",
        _PILOT,
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 received",
    ),
    _event(
        "LateReturnFinalized",
        _CURRENT,
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 sentSD",
        "uint256 received",
    ),
    _event("NativeSwept", _BOTH, "address to", "uint256 amount"),
    _event(
        "OutboundForceResolved",
        _BOTH,
        "bytes32 operationId",
        "uint256 chainId",
        "bool creditedToSettled",
    ),
    _event(
        "ProposalInvalidated",
        _CURRENT,
        "uint256 chainId",
        "uint256 proposalId",
        "uint64 newEpoch",
    ),
    _event(
        "RedeemedFromRequest",
        _CURRENT,
        "address vault",
        "uint256 sharesRedeemed",
        "uint256 assetsDeltaSD",
    ),
    _event(
        "RemoteBalanceUpdated",
        _BOTH,
        "uint256 chainId",
        "uint256 balance",
        "uint64 stateVersion",
    ),
    _event(
        "RemoteStateVersionGap",
        _PILOT,
        "uint256 chainId",
        "uint64 expected",
        "uint64 received",
    ),
    _event("ResponseDispatched", _BOTH, "uint64 responseId", "bytes32 messageId"),
    _event(
        "ResponseQueued",
        _BOTH,
        "uint64 responseId",
        "uint8 messageType",
        "uint256 tokenAmount",
    ),
    _event("ResponseSkipped", _BOTH, "uint64 responseId", "uint256 tokenAmount"),
    _event(
        "ReturnBelowMinimumCredited",
        _BOTH,
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 received",
        "uint256 minimum",
    ),
    _event("ReturnCancelled", _BOTH, "uint256 chainId", "bytes32 operationId"),
    _event(
        "ReturnDebitExceededAccountingBound",
        _CURRENT,
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 sentSD",
        "uint256 maxAccounted",
    ),
    _event(
        "ReturnFinalized",
        _PILOT,
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 received",
    ),
    _event(
        "ReturnFinalized",
        _CURRENT,
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 sentSD",
        "uint256 received",
    ),
    _event(
        "ReturnQueued",
        _BOTH,
        "bytes32 operationId",
        "uint256 amount",
        "uint64 responseId",
    ),
    _event(
        "ReturnRejected",
        _BOTH,
        "bytes32 operationId",
        "uint8 reason",
        "uint64 responseId",
    ),
    _event(
        "ReturnRejected",
        _BOTH,
        "uint256 chainId",
        "bytes32 operationId",
        "uint8 reason",
    ),
    _event(
        "ReturnRequested",
        _BOTH,
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 amount",
    ),
    _event("SettledBalanceForceSet", _BOTH, "uint256 chainId", "uint256 newSettled"),
    _event(
        "SettledCreditClamped",
        _BOTH,
        "uint256 chainId",
        "bytes32 operationId",
        "uint256 reported",
        "uint256 credited",
    ),
    _event("SharesRequested", _CURRENT, "address vault", "uint256 pendingShares"),
    _event(
        "StaleRemoteBalanceIgnored",
        _BOTH,
        "uint256 chainId",
        "uint64 currentVersion",
        "uint64 receivedVersion",
    ),
    _event(
        "StateFrontierAdvanced",
        _BOTH,
        "uint256 chainId",
        "uint64 oldVersion",
        "uint64 newVersion",
    ),
    _event("TokenSwept", _BOTH, "address token", "address to", "uint256 amount"),
    _event("VaultAllowed", _BOTH, "address vault", "bool allowed"),
    _event("VaultTracked", _CURRENT, "address vault"),
    _event("VaultUntracked", _CURRENT, "address vault"),
    _event(
        "VaultsConfigured",
        _CURRENT,
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
        generations=spec.generations,
        address=log_address(log),
        values=MappingProxyType(values),
    )


def ccip_events(logs: Iterable[Mapping]) -> list[CcipEvent]:
    """Every known CCIP event among ``logs``, in log order; other logs are skipped."""
    return [event for event in map(decode_ccip_event, logs) if event is not None]


def find_ccip_events(
    logs: Iterable[Mapping],
    name: str,
    *,
    generation: CcipGeneration | None = None,
) -> list[CcipEvent]:
    """The events named ``name`` among ``logs``, optionally only those whose
    signature belongs to ``generation``."""
    return [
        event
        for event in ccip_events(logs)
        if event.name == name
        and (generation is None or generation in event.generations)
    ]

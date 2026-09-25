"""The transport seam: how outbound crosschain messages are found in
simulation logs and replayed as inbound calls on the destination chain.

The contracts abstract the bridge behind executor/dispatcher pairs; the SDK
mirrors that with ``CrosschainTransport``. An implementation knows, per chain,
which contract emits the outbound event (LayerZero ``EndpointV2`` or a CCIP
``OnRamp``), how to decode the message, and which contract the destination
receiver expects as ``msg.sender`` on delivery (the endpoint or the Router).
``eth_simulateV1`` accepts any ``from``, so a delivery is just a call from that
address; no bridge, relayer or mock bytecode is involved and the real
executor, dispatcher and factory code runs on both sides.

Token legs (Stargate taxi, CCIP token transfer) also credit the receiver by an
impersonated ``transfer`` from a ``token_source`` that holds the asset on the
destination chain, in place of the pool release or CCTP mint the bridge would
do.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import _encode_calldata
from ipor_fusion.crosschain.contracts import CrosschainDispatcher, CrosschainExecutor
from ipor_fusion.crosschain.messages import CrosschainTransportKind
from ipor_fusion.types import ChainId


@dataclass(frozen=True, slots=True)
class OutboundMessage:
    """One crosschain message found in a source chain's simulation logs.

    ``payload`` is the application envelope (``CrosschainMessages`` or
    ``CcipMessages`` bytes); ``token_amount`` is the asset credit the delivery
    carries, in the destination's local decimals (0 for a plain message).
    ``raw`` keeps the decoded transport frame for inspection.
    """

    transport_kind: CrosschainTransportKind
    message_id: bytes
    src_chain_id: ChainId
    dst_chain_id: ChainId
    sender: ChecksumAddress
    receiver: ChecksumAddress
    payload: bytes
    token_amount: int = 0
    raw: Any = None


@dataclass(frozen=True, slots=True)
class DeliveryCall:
    """One call to queue on the destination chain to deliver a message."""

    to: ChecksumAddress
    data: bytes
    from_: ChecksumAddress
    label: str


class CrosschainTransport(ABC):
    """A bridge as seen by the simulation relay and the wrappers."""

    transport_kind: ClassVar[CrosschainTransportKind]

    @property
    @abstractmethod
    def chain_ids(self) -> frozenset[ChainId]:
        """The chains this transport is configured for."""

    @abstractmethod
    def outbound_messages(
        self, src_chain_id: ChainId, logs: Iterable[Mapping]
    ) -> list[OutboundMessage]:
        """Every message this transport sent from ``src_chain_id`` in ``logs``
        (raw log dicts, as ``SimulatedCallResult.logs`` carries them)."""

    @abstractmethod
    def delivery_calls(self, message: OutboundMessage) -> list[DeliveryCall]:
        """The calls that deliver ``message`` on its destination, in order."""

    @abstractmethod
    def executor(
        self, ctx: Web3Context, address: ChecksumAddress
    ) -> CrosschainExecutor:
        """Wrap an executor of this transport."""

    @abstractmethod
    def dispatcher(
        self, ctx: Web3Context, address: ChecksumAddress
    ) -> CrosschainDispatcher:
        """Wrap a dispatcher of this transport."""


def transfer_call(
    token: ChecksumAddress, source: ChecksumAddress, to: ChecksumAddress, amount: int
) -> DeliveryCall:
    return DeliveryCall(
        to=token,
        data=_encode_calldata("transfer(address,uint256)", to, amount),
        from_=source,
        label=f"token_credit:{to}",
    )


def checksum(address: str) -> ChecksumAddress:
    """Convenience for building chain configs from lowercase literals."""
    return Web3.to_checksum_address(address)

"""The Stargate V2 / LayerZero V2 transport: ``PacketSent`` in, ``lzReceive``
and ``lzCompose`` from the endpoint out."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from eth_typing import ChecksumAddress

from ipor_fusion.core.context import Web3Context
from ipor_fusion.crosschain.logs import log_address, log_topic0
from ipor_fusion.crosschain.messages import CrosschainTransportKind
from ipor_fusion.crosschain.stargate.contracts import (
    StargateCrosschainDispatcher,
    StargateCrosschainExecutor,
)
from ipor_fusion.crosschain.stargate.layerzero import (
    PACKET_SENT_TOPIC,
    Packet,
    TaxiMessage,
    encode_oft_compose_msg,
    lz_compose_calldata,
    lz_receive_calldata,
)
from ipor_fusion.crosschain.transport import (
    CrosschainTransport,
    DeliveryCall,
    OutboundMessage,
    transfer_call,
)
from ipor_fusion.types import ChainId


@dataclass(frozen=True, slots=True)
class StargateChain:
    """Per-chain LayerZero/Stargate wiring for one bridged asset.

    ``token_messaging`` is the Stargate V2 ``TokenMessaging`` OApp, the actual
    LayerZero sender of every taxi (asset) packet; ``stargate_pool`` is the
    asset's pool, which the receiver sees as ``lzCompose``'s ``from`` and which
    is also the default ``token_source``. ``asset_id`` is the pool's id in
    ``TokenMessaging``. Asset id and decimal configuration are explicit because
    they differ between assets and chains.
    """

    chain_id: ChainId
    eid: int
    endpoint: ChecksumAddress
    token_messaging: ChecksumAddress
    stargate_pool: ChecksumAddress
    token: ChecksumAddress
    asset_id: int
    local_decimals: int
    shared_decimals: int
    token_source: ChecksumAddress | None = None

    def __post_init__(self) -> None:
        if not 1 <= self.asset_id <= 0xFFFF:
            raise ValueError(
                f"asset_id must be between 1 and 65535, got {self.asset_id}"
            )
        for name, value in (
            ("local_decimals", self.local_decimals),
            ("shared_decimals", self.shared_decimals),
        ):
            if not 0 <= value <= 255:
                raise ValueError(f"{name} must fit uint8, got {value}")
        if self.local_decimals < self.shared_decimals:
            raise ValueError(
                f"local_decimals {self.local_decimals} must be greater than or "
                f"equal to shared_decimals {self.shared_decimals}"
            )

    @property
    def credit_source(self) -> ChecksumAddress:
        return self.token_source or self.stargate_pool

    def to_local_decimals(self, amount_sd: int) -> int:
        return amount_sd * 10 ** (self.local_decimals - self.shared_decimals)


class StargateTransport(CrosschainTransport):
    """The Stargate V2 / LayerZero V2 transport (``StargateCrosschain*``)."""

    transport_kind = CrosschainTransportKind.STARGATE_LAYERZERO

    def __init__(self, chains: Iterable[StargateChain]) -> None:
        self._chains = {c.chain_id: c for c in chains}
        shared_decimals = {c.shared_decimals for c in self._chains.values()}
        if len(shared_decimals) > 1:
            configured = ", ".join(
                f"{chain.chain_id}: {chain.shared_decimals}"
                for chain in self._chains.values()
            )
            raise ValueError(
                f"Stargate chains must use the same shared_decimals ({configured})"
            )
        self._by_eid = {c.eid: c for c in self._chains.values()}

    @property
    def chain_ids(self) -> frozenset[ChainId]:
        return frozenset(self._chains)

    def chain(self, chain_id: ChainId) -> StargateChain:
        return self._chains[chain_id]

    def executor(
        self, ctx: Web3Context, address: ChecksumAddress
    ) -> StargateCrosschainExecutor:
        return StargateCrosschainExecutor(ctx, address)

    def dispatcher(
        self, ctx: Web3Context, address: ChecksumAddress
    ) -> StargateCrosschainDispatcher:
        return StargateCrosschainDispatcher(ctx, address)

    def outbound_messages(
        self, src_chain_id: ChainId, logs: Iterable[Mapping]
    ) -> list[OutboundMessage]:
        src = self._chains[src_chain_id]
        messages = []
        for log in logs:
            if log_topic0(log) != PACKET_SENT_TOPIC or log_address(log) != src.endpoint:
                continue
            messages.append(self._from_packet(src, Packet.from_log(log)))
        return messages

    def _from_packet(self, src: StargateChain, packet: Packet) -> OutboundMessage:
        dst = self._by_eid.get(packet.dst_eid)
        if dst is None:
            raise ValueError(f"packet to unconfigured LayerZero eid {packet.dst_eid}")
        if packet.src_eid != src.eid:
            raise ValueError(f"packet src eid {packet.src_eid} != {src.eid}")
        if packet.sender != src.token_messaging:
            # A plain OApp message (executor, dispatcher or factory lane).
            return OutboundMessage(
                transport_kind=self.transport_kind,
                message_id=packet.guid,
                src_chain_id=src.chain_id,
                dst_chain_id=dst.chain_id,
                sender=packet.sender,
                receiver=packet.receiver,
                payload=packet.message,
                raw=packet,
            )
        taxi = TaxiMessage.decode(packet.message)
        if taxi.asset_id != src.asset_id:
            raise ValueError(
                f"taxi asset id {taxi.asset_id} != configured {src.asset_id}"
            )
        return OutboundMessage(
            transport_kind=self.transport_kind,
            message_id=packet.guid,
            src_chain_id=src.chain_id,
            dst_chain_id=dst.chain_id,
            sender=taxi.compose_from or packet.sender,
            receiver=taxi.receiver,
            payload=taxi.compose_payload,
            token_amount=dst.to_local_decimals(taxi.amount_sd),
            raw=(packet, taxi),
        )

    def delivery_calls(self, message: OutboundMessage) -> list[DeliveryCall]:
        dst = self._chains[message.dst_chain_id]
        packet = message.raw[0] if isinstance(message.raw, tuple) else message.raw
        if not isinstance(message.raw, tuple):
            data = lz_receive_calldata(
                src_eid=packet.src_eid,
                sender=packet.sender,
                nonce=packet.nonce,
                guid=packet.guid,
                message=packet.message,
            )
            return [
                DeliveryCall(
                    to=message.receiver,
                    data=data,
                    from_=dst.endpoint,
                    label=f"lz_receive:{message.message_id.hex()[:8]}",
                )
            ]
        taxi: TaxiMessage = message.raw[1]
        compose = encode_oft_compose_msg(
            packet.nonce, packet.src_eid, message.token_amount, taxi.compose_msg
        )
        return [
            transfer_call(
                dst.token, dst.credit_source, message.receiver, message.token_amount
            ),
            DeliveryCall(
                to=message.receiver,
                data=lz_compose_calldata(
                    from_=dst.stargate_pool, guid=packet.guid, message=compose
                ),
                from_=dst.endpoint,
                label=f"lz_compose:{message.message_id.hex()[:8]}",
            ),
        ]

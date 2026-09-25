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
from ipor_fusion.fuses.crosschain.base import (
    CrosschainCommandFuse,
    CrosschainSupplyFuse,
)
from ipor_fusion.fuses.crosschain.stargate import (
    StargateCrosschainCommandFuse,
    StargateCrosschainSupplyFuse,
)
from ipor_fusion.types import ChainId


@dataclass(frozen=True, slots=True)
class StargateChain:
    """Per-chain LayerZero/Stargate wiring for one bridged asset.

    ``token_messaging`` is the Stargate V2 ``TokenMessaging`` OApp, the actual
    LayerZero sender of every taxi (asset) packet; ``stargate_pool`` is the
    asset's pool, which the receiver sees as ``lzCompose``'s ``from`` and which
    is also the default ``token_source``. ``asset_id`` is the pool's id in
    ``TokenMessaging`` (1 for USDC).
    """

    chain_id: ChainId
    eid: int
    endpoint: ChecksumAddress
    token_messaging: ChecksumAddress
    stargate_pool: ChecksumAddress
    token: ChecksumAddress
    asset_id: int = 1
    local_decimals: int = 6
    shared_decimals: int = 6
    token_source: ChecksumAddress | None = None

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

    def supply_fuse(self, address: ChecksumAddress) -> CrosschainSupplyFuse:
        return StargateCrosschainSupplyFuse(address)

    def command_fuse(self, address: ChecksumAddress) -> CrosschainCommandFuse:
        return StargateCrosschainCommandFuse(address)

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

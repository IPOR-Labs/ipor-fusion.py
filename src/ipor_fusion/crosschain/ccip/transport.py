"""The Chainlink CCIP transport: ``CCIPMessageSent`` in, ``ccipReceive`` from
the Router out."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass

from eth_typing import ChecksumAddress

from ipor_fusion.core.context import Web3Context
from ipor_fusion.crosschain.ccip.codec import (
    CCIP_MESSAGE_SENT_TOPIC,
    Any2EVMMessage,
    CcipMessageSent,
    EVMTokenAmount,
    ccip_receive_calldata,
    evm_address,
)
from ipor_fusion.crosschain.ccip.contracts import (
    CcipCrosschainDispatcher,
    CcipCrosschainExecutor,
)
from ipor_fusion.crosschain.logs import log_topic0
from ipor_fusion.crosschain.messages import CrosschainTransportKind
from ipor_fusion.crosschain.transport import (
    CrosschainTransport,
    DeliveryCall,
    OutboundMessage,
    transfer_call,
)
from ipor_fusion.types import ChainId


@dataclass(frozen=True, slots=True)
class CcipChain:
    """Per-chain CCIP wiring for one bridged asset. ``token_source`` is the
    address impersonated to credit delivered tokens (the real path mints
    through CCTP, which a simulation cannot drive)."""

    chain_id: ChainId
    chain_selector: int
    router: ChecksumAddress
    token: ChecksumAddress
    token_source: ChecksumAddress


class CcipTransport(CrosschainTransport):
    """The Chainlink CCIP transport (``CcipCrosschain*``)."""

    transport_kind = CrosschainTransportKind.CHAINLINK_CCIP

    def __init__(self, chains: Iterable[CcipChain]) -> None:
        self._chains = {c.chain_id: c for c in chains}
        self._by_selector = {c.chain_selector: c for c in self._chains.values()}

    @property
    def chain_ids(self) -> frozenset[ChainId]:
        return frozenset(self._chains)

    def chain(self, chain_id: ChainId) -> CcipChain:
        return self._chains[chain_id]

    def executor(
        self, ctx: Web3Context, address: ChecksumAddress
    ) -> CcipCrosschainExecutor:
        return CcipCrosschainExecutor(ctx, address)

    def dispatcher(
        self, ctx: Web3Context, address: ChecksumAddress
    ) -> CcipCrosschainDispatcher:
        return CcipCrosschainDispatcher(ctx, address)

    def outbound_messages(
        self, src_chain_id: ChainId, logs: Iterable[Mapping]
    ) -> list[OutboundMessage]:
        src = self._chains[src_chain_id]
        messages = []
        for log in logs:
            if log_topic0(log) != CCIP_MESSAGE_SENT_TOPIC:
                continue
            messages.append(self._from_event(src, CcipMessageSent.from_log(log)))
        return messages

    def _from_event(self, src: CcipChain, sent: CcipMessageSent) -> OutboundMessage:
        message = sent.message
        if message.source_chain_selector != src.chain_selector:
            raise ValueError(
                f"CCIP source selector {message.source_chain_selector} != "
                f"configured {src.chain_selector}"
            )
        dst = self._by_selector.get(message.dest_chain_selector)
        if dst is None:
            raise ValueError(
                f"CCIP message to unconfigured selector {message.dest_chain_selector}"
            )
        token_amount = 0
        if message.token_transfer:
            transfer = message.token_transfer[0]
            dest_token = evm_address(transfer.dest_token_address)
            if dest_token != dst.token:
                raise ValueError(f"CCIP transfer of {dest_token}, expected {dst.token}")
            token_amount = transfer.amount
        return OutboundMessage(
            transport_kind=self.transport_kind,
            message_id=sent.message_id,
            src_chain_id=src.chain_id,
            dst_chain_id=dst.chain_id,
            sender=message.sender_address,
            receiver=message.receiver_address,
            payload=message.data,
            token_amount=token_amount,
            raw=message,
        )

    def delivery_calls(self, message: OutboundMessage) -> list[DeliveryCall]:
        src = self._chains[message.src_chain_id]
        dst = self._chains[message.dst_chain_id]
        calls = []
        token_amounts: tuple[EVMTokenAmount, ...] = ()
        if message.token_amount:
            calls.append(
                transfer_call(
                    dst.token, dst.token_source, message.receiver, message.token_amount
                )
            )
            token_amounts = (EVMTokenAmount(dst.token, message.token_amount),)
        calls.append(
            DeliveryCall(
                to=message.receiver,
                data=ccip_receive_calldata(
                    Any2EVMMessage(
                        message_id=message.message_id,
                        source_chain_selector=src.chain_selector,
                        sender=message.sender,
                        data=message.payload,
                        dest_token_amounts=token_amounts,
                    )
                ),
                from_=dst.router,
                label=f"ccip_receive:{message.message_id.hex()[:8]}",
            )
        )
        return calls

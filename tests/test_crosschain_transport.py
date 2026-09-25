"""Offline tests for the transport seam and the relay simulator: synthetic
``PacketSent`` / ``CCIPMessageSent`` logs in, delivery calls out, and a scripted
provider driving ``CrosschainSimulator.relay`` across two chains."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from _crosschain import (
    BASE,
    BASE_CCIP_ROUTER,
    BASE_CHAIN_SELECTOR,
    BASE_EID,
    BASE_STARGATE_USDC_POOL,
    BASE_TOKEN_MESSAGING,
    BASE_USDC,
    ETHEREUM,
    ETHEREUM_CHAIN_SELECTOR,
    ETHEREUM_EID,
    ETHEREUM_STARGATE_USDC_POOL,
    ETHEREUM_TOKEN_MESSAGING,
    ETHEREUM_USDC,
    LAYERZERO_ENDPOINT,
    STARGATE_EXECUTOR,
    ccip_transport,
    stargate_transport,
)
from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector, keccak
from test_crosschain_encoding import (
    _packet_bytes,
    ccip_message_sent_log,
    encode_message_v1,
)
from web3 import Web3

from ipor_fusion import (
    CrosschainSimulator,
    CrosschainTransport,
    DeliveryCall,
    OutboundMessage,
    StargateChain,
    StargateTransport,
)
from ipor_fusion.core.contract import Call
from ipor_fusion.crosschain import (
    CcipCrosschainDispatcher,
    CcipCrosschainExecutor,
    CrosschainTransportKind,
    Packet,
    StargateCrosschainDispatcher,
    StargateCrosschainExecutor,
)
from ipor_fusion.crosschain.stargate.layerzero import (
    PACKET_SENT_TOPIC,
    address_to_bytes32,
)
from ipor_fusion.fuses.base import ZERO_ADDRESS
from ipor_fusion.fuses.crosschain import (
    CcipCrosschainCommandFuse,
    CcipCrosschainSupplyFuse,
    StargateCrosschainCommandFuse,
    StargateCrosschainSupplyFuse,
)
from ipor_fusion.types import ChainId

TRANSFER_SELECTOR = function_signature_to_4byte_selector("transfer(address,uint256)")
GUID = b"\xab" * 32


def packet_sent_log(endpoint: str, packet: bytes) -> dict:
    return {
        "address": endpoint,
        "topics": ["0x" + PACKET_SENT_TOPIC.hex()],
        "data": "0x"
        + encode(["bytes", "bytes", "address"], [packet, b"", ZERO_ADDRESS]).hex(),
    }


def _envelope(msg_type: int) -> bytes:
    return encode(["uint8", "uint8", "bytes"], [3, msg_type, b"\x11" * 32])


def _taxi(amount_sd: int, envelope: bytes) -> bytes:
    return (
        b"\x01"
        + (1).to_bytes(2, "big")
        + address_to_bytes32(STARGATE_EXECUTOR)
        + amount_sd.to_bytes(8, "big")
        + address_to_bytes32(STARGATE_EXECUTOR)
        + envelope
    )


class TestStargateTransport:
    def test_plain_message_and_delivery(self):
        transport = stargate_transport()
        message = _envelope(9)
        packet = _packet_bytes(
            nonce=4,
            src_eid=ETHEREUM_EID,
            sender=STARGATE_EXECUTOR,
            dst_eid=BASE_EID,
            receiver=STARGATE_EXECUTOR,
            guid=GUID,
            message=message,
        )
        noise = [
            {
                "address": ETHEREUM_USDC,
                "topics": ["0x" + PACKET_SENT_TOPIC.hex()],
                "data": "0x",
            },
            {
                "address": LAYERZERO_ENDPOINT,
                "topics": ["0x" + keccak(text="Other()").hex()],
                "data": "0x",
            },
            {"address": LAYERZERO_ENDPOINT, "topics": [], "data": "0x"},
        ]
        (outbound,) = transport.outbound_messages(
            ETHEREUM, [*noise, packet_sent_log(LAYERZERO_ENDPOINT, packet)]
        )
        assert outbound == OutboundMessage(
            CrosschainTransportKind.STARGATE_LAYERZERO,
            GUID,
            ETHEREUM,
            BASE,
            STARGATE_EXECUTOR,
            STARGATE_EXECUTOR,
            message,
            0,
            Packet.decode(packet),
        )
        (delivery,) = transport.delivery_calls(outbound)
        assert delivery.to == STARGATE_EXECUTOR
        assert delivery.from_ == LAYERZERO_ENDPOINT
        assert delivery.label.startswith("lz_receive:")
        origin, guid, delivered, _, _ = decode(
            ["(uint32,bytes32,uint64)", "bytes32", "bytes", "address", "bytes"],
            delivery.data[4:],
        )
        assert origin == (ETHEREUM_EID, address_to_bytes32(STARGATE_EXECUTOR), 4)
        assert (guid, delivered) == (GUID, message)

    def test_taxi_message_credits_tokens_then_composes(self):
        transport = stargate_transport()
        envelope = _envelope(5)
        packet = _packet_bytes(
            nonce=7,
            src_eid=BASE_EID,
            sender=BASE_TOKEN_MESSAGING,
            dst_eid=ETHEREUM_EID,
            receiver=ETHEREUM_TOKEN_MESSAGING,
            guid=GUID,
            message=_taxi(999_414, envelope),
        )
        (outbound,) = transport.outbound_messages(
            BASE, [packet_sent_log(LAYERZERO_ENDPOINT, packet)]
        )
        assert outbound.dst_chain_id == ETHEREUM
        assert outbound.receiver == STARGATE_EXECUTOR
        assert outbound.sender == STARGATE_EXECUTOR
        assert outbound.payload == envelope
        assert outbound.token_amount == 999_414
        credit, compose = transport.delivery_calls(outbound)
        assert credit == DeliveryCall(
            ETHEREUM_USDC,
            TRANSFER_SELECTOR
            + encode(["address", "uint256"], [STARGATE_EXECUTOR, 999_414]),
            ETHEREUM_STARGATE_USDC_POOL,
            f"token_credit:{STARGATE_EXECUTOR}",
        )
        assert compose.to == STARGATE_EXECUTOR
        assert compose.from_ == LAYERZERO_ENDPOINT
        from_, guid, message, _, _ = decode(
            ["address", "bytes32", "bytes", "address", "bytes"], compose.data[4:]
        )
        assert from_ == ETHEREUM_STARGATE_USDC_POOL.lower()
        assert guid == GUID
        assert message[:8] == (7).to_bytes(8, "big")
        assert message[8:12] == BASE_EID.to_bytes(4, "big")
        assert int.from_bytes(message[12:44], "big") == 999_414
        assert message[44:76] == address_to_bytes32(STARGATE_EXECUTOR)
        assert message[76:] == envelope

    def test_decimal_conversion_and_token_source_override(self):
        chain = stargate_transport().chain(BASE)
        assert chain.credit_source == BASE_STARGATE_USDC_POOL
        wide = StargateTransport(
            [
                StargateChain_(chain_id=ETHEREUM, eid=ETHEREUM_EID),
                StargateChain_(
                    chain_id=BASE,
                    eid=BASE_EID,
                    local_decimals=18,
                    token_source=STARGATE_EXECUTOR,
                ),
            ]
        )
        packet = _packet_bytes(
            nonce=1,
            src_eid=ETHEREUM_EID,
            sender=ETHEREUM_TOKEN_MESSAGING,
            dst_eid=BASE_EID,
            receiver=BASE_TOKEN_MESSAGING,
            guid=GUID,
            message=_taxi(5, _envelope(5)),
        )
        (outbound,) = wide.outbound_messages(
            ETHEREUM, [packet_sent_log(LAYERZERO_ENDPOINT, packet)]
        )
        assert outbound.token_amount == 5 * 10**12
        assert wide.delivery_calls(outbound)[0].from_ == STARGATE_EXECUTOR

    def test_rejects_unconfigured_or_inconsistent_packets(self):
        transport = stargate_transport()
        unknown_dst = _packet_bytes(
            nonce=1,
            src_eid=ETHEREUM_EID,
            sender=STARGATE_EXECUTOR,
            dst_eid=30999,
            receiver=STARGATE_EXECUTOR,
            guid=GUID,
            message=b"",
        )
        with pytest.raises(ValueError, match="unconfigured LayerZero eid"):
            transport.outbound_messages(
                ETHEREUM, [packet_sent_log(LAYERZERO_ENDPOINT, unknown_dst)]
            )
        wrong_src = _packet_bytes(
            nonce=1,
            src_eid=BASE_EID,
            sender=STARGATE_EXECUTOR,
            dst_eid=BASE_EID,
            receiver=STARGATE_EXECUTOR,
            guid=GUID,
            message=b"",
        )
        with pytest.raises(ValueError, match="src eid"):
            transport.outbound_messages(
                ETHEREUM, [packet_sent_log(LAYERZERO_ENDPOINT, wrong_src)]
            )
        wrong_asset = _packet_bytes(
            nonce=1,
            src_eid=ETHEREUM_EID,
            sender=ETHEREUM_TOKEN_MESSAGING,
            dst_eid=BASE_EID,
            receiver=BASE_TOKEN_MESSAGING,
            guid=GUID,
            message=b"\x01" + (2).to_bytes(2, "big") + _taxi(1, b"")[3:],
        )
        with pytest.raises(ValueError, match="taxi asset id"):
            transport.outbound_messages(
                ETHEREUM, [packet_sent_log(LAYERZERO_ENDPOINT, wrong_asset)]
            )

    def test_wrappers_and_chain_ids(self):
        transport = stargate_transport()
        ctx = MagicMock()
        assert isinstance(
            transport.executor(ctx, STARGATE_EXECUTOR), StargateCrosschainExecutor
        )
        assert isinstance(
            transport.dispatcher(ctx, STARGATE_EXECUTOR), StargateCrosschainDispatcher
        )
        assert isinstance(
            transport.supply_fuse(STARGATE_EXECUTOR), StargateCrosschainSupplyFuse
        )
        assert isinstance(
            transport.command_fuse(STARGATE_EXECUTOR), StargateCrosschainCommandFuse
        )
        assert {ETHEREUM, BASE} <= transport.chain_ids
        assert transport.transport_kind == CrosschainTransportKind.STARGATE_LAYERZERO


def StargateChain_(**overrides):  # noqa: N802 - test helper that fills the boring fields
    defaults = dict(
        endpoint=LAYERZERO_ENDPOINT,
        token_messaging=ETHEREUM_TOKEN_MESSAGING
        if overrides.get("chain_id") == ETHEREUM
        else BASE_TOKEN_MESSAGING,
        stargate_pool=ETHEREUM_STARGATE_USDC_POOL
        if overrides.get("chain_id") == ETHEREUM
        else BASE_STARGATE_USDC_POOL,
        token=ETHEREUM_USDC if overrides.get("chain_id") == ETHEREUM else BASE_USDC,
    )
    return StargateChain(**{**defaults, **overrides})


class TestCcipTransport:
    def test_message_with_token_transfer(self):
        transport = ccip_transport()
        payload = encode(["uint8", "uint8", "bytes"], [1, 1, b"\x77" * 32])
        encoded = encode_message_v1(
            source_selector=ETHEREUM_CHAIN_SELECTOR,
            dest_selector=BASE_CHAIN_SELECTOR,
            sender=STARGATE_EXECUTOR,
            receiver=STARGATE_EXECUTOR,
            data=payload,
            transfer=(100_000, BASE_USDC),
        )
        message_id = keccak(encoded)
        log = ccip_message_sent_log(
            encoded,
            dest_selector=BASE_CHAIN_SELECTOR,
            sender=STARGATE_EXECUTOR,
            message_id=message_id,
        )
        (outbound,) = transport.outbound_messages(
            ETHEREUM, [{"address": ETHEREUM_USDC, "topics": [], "data": "0x"}, log]
        )
        assert outbound.message_id == message_id
        assert (outbound.src_chain_id, outbound.dst_chain_id) == (ETHEREUM, BASE)
        assert outbound.receiver == STARGATE_EXECUTOR
        assert outbound.token_amount == 100_000
        assert outbound.payload == payload
        credit, receive = transport.delivery_calls(outbound)
        assert credit.from_ == BASE_STARGATE_USDC_POOL
        assert credit.to == BASE_USDC
        assert receive.to == STARGATE_EXECUTOR
        assert receive.from_ == BASE_CCIP_ROUTER
        (delivered,) = decode(
            ["(bytes32,uint64,bytes,bytes,(address,uint256)[])"], receive.data[4:]
        )
        assert delivered == (
            message_id,
            ETHEREUM_CHAIN_SELECTOR,
            encode(["address"], [STARGATE_EXECUTOR]),
            payload,
            ((BASE_USDC.lower(), 100_000),),
        )

    def test_plain_message_has_single_delivery_call(self):
        transport = ccip_transport()
        encoded = encode_message_v1(
            source_selector=BASE_CHAIN_SELECTOR,
            dest_selector=ETHEREUM_CHAIN_SELECTOR,
            sender=STARGATE_EXECUTOR,
            receiver=STARGATE_EXECUTOR,
            data=b"ack",
        )
        log = ccip_message_sent_log(
            encoded,
            dest_selector=ETHEREUM_CHAIN_SELECTOR,
            sender=STARGATE_EXECUTOR,
            message_id=keccak(encoded),
        )
        (outbound,) = transport.outbound_messages(BASE, [log])
        assert outbound.token_amount == 0
        (receive,) = transport.delivery_calls(outbound)
        assert (
            receive.from_ == BASE_CCIP_ROUTER
            if False
            else receive.from_ == transport.chain(ETHEREUM).router
        )

    def test_rejects_wrong_selector_or_token(self):
        transport = ccip_transport()
        foreign = encode_message_v1(
            source_selector=1,
            dest_selector=BASE_CHAIN_SELECTOR,
            sender=STARGATE_EXECUTOR,
            receiver=STARGATE_EXECUTOR,
            data=b"",
        )
        with pytest.raises(ValueError, match="source selector"):
            transport.outbound_messages(
                ETHEREUM,
                [
                    ccip_message_sent_log(
                        foreign,
                        dest_selector=BASE_CHAIN_SELECTOR,
                        sender=STARGATE_EXECUTOR,
                        message_id=keccak(foreign),
                    )
                ],
            )
        unknown = encode_message_v1(
            source_selector=ETHEREUM_CHAIN_SELECTOR,
            dest_selector=7,
            sender=STARGATE_EXECUTOR,
            receiver=STARGATE_EXECUTOR,
            data=b"",
        )
        with pytest.raises(ValueError, match="unconfigured selector"):
            transport.outbound_messages(
                ETHEREUM,
                [
                    ccip_message_sent_log(
                        unknown,
                        dest_selector=7,
                        sender=STARGATE_EXECUTOR,
                        message_id=keccak(unknown),
                    )
                ],
            )
        wrong_token = encode_message_v1(
            source_selector=ETHEREUM_CHAIN_SELECTOR,
            dest_selector=BASE_CHAIN_SELECTOR,
            sender=STARGATE_EXECUTOR,
            receiver=STARGATE_EXECUTOR,
            data=b"",
            transfer=(1, ETHEREUM_USDC),
        )
        with pytest.raises(ValueError, match="CCIP transfer of"):
            transport.outbound_messages(
                ETHEREUM,
                [
                    ccip_message_sent_log(
                        wrong_token,
                        dest_selector=BASE_CHAIN_SELECTOR,
                        sender=STARGATE_EXECUTOR,
                        message_id=keccak(wrong_token),
                    )
                ],
            )

    def test_wrappers(self):
        transport = ccip_transport()
        ctx = MagicMock()
        assert isinstance(
            transport.executor(ctx, STARGATE_EXECUTOR), CcipCrosschainExecutor
        )
        assert isinstance(
            transport.dispatcher(ctx, STARGATE_EXECUTOR), CcipCrosschainDispatcher
        )
        assert isinstance(
            transport.supply_fuse(STARGATE_EXECUTOR), CcipCrosschainSupplyFuse
        )
        assert isinstance(
            transport.command_fuse(STARGATE_EXECUTOR), CcipCrosschainCommandFuse
        )
        assert transport.transport_kind == CrosschainTransportKind.CHAINLINK_CCIP


# ── Relay simulator ────────────────────────────────────────────────────────

CHAIN_A = ChainId(1)
CHAIN_B = ChainId(2)
RECEIVER = Web3.to_checksum_address("0x4444444444444444444444444444444444444444")
SENDER = Web3.to_checksum_address("0x5555555555555555555555555555555555555555")


class ScriptedTransport(CrosschainTransport):
    """Messages are logs whose data is ``dst_chain_id (1 byte) || message_id (32)``."""

    transport_kind = CrosschainTransportKind.STARGATE_LAYERZERO

    @property
    def chain_ids(self):
        return frozenset({CHAIN_A, CHAIN_B})

    def outbound_messages(self, src_chain_id, logs):
        out = []
        for log in logs:
            raw = bytes.fromhex(log["data"][2:])
            out.append(
                OutboundMessage(
                    self.transport_kind,
                    raw[1:33],
                    src_chain_id,
                    ChainId(raw[0]),
                    SENDER,
                    RECEIVER,
                    raw[33:],
                )
            )
        return out

    def delivery_calls(self, message):
        return [
            DeliveryCall(
                RECEIVER,
                b"\xde\xad" + message.message_id,
                SENDER,
                f"deliver:{message.message_id[:1].hex()}",
            )
        ]

    def executor(self, ctx, address):
        raise NotImplementedError

    def dispatcher(self, ctx, address):
        raise NotImplementedError

    def supply_fuse(self, address):
        raise NotImplementedError

    def command_fuse(self, address):
        raise NotImplementedError


def _message_log(dst: int, message_id: bytes) -> dict:
    return {
        "address": RECEIVER,
        "topics": [],
        "data": "0x" + bytes([dst]).hex() + message_id.hex(),
    }


class ScriptedProvider:
    """Answers eth_simulateV1 with canned logs per (chain, call index) and records payloads."""

    def __init__(self, chain_id: int, script: dict[int, list[dict]]):
        self.chain_id = chain_id
        self.script = script
        self.payloads: list = []

    def make_request(self, method, params):
        assert method == "eth_simulateV1"
        self.payloads.append(params)
        calls = [c for block in params[0]["blockStateCalls"] for c in block["calls"]]
        results = [
            {
                "status": "0x1",
                "returnData": "0x" + (32).to_bytes(32, "big").hex(),
                "gasUsed": "0x5208",
                "logs": self.script.get(i, []),
            }
            for i in range(len(calls))
        ]
        return {"result": [{"calls": results}]}


def _web3(chain_id: int, script: dict[int, list[dict]]):
    web3 = MagicMock()
    web3.provider = ScriptedProvider(chain_id, script)
    web3.eth.get_block.return_value = {"timestamp": 1_790_000_000}
    return web3


class TestCrosschainSimulator:
    def test_relay_round_trips_and_deduplicates(self):
        m1, m2 = b"\x01" * 32, b"\x02" * 32
        # Chain A: its first call emits m1 -> B. Chain B: delivering m1 (its first call) emits m2 -> A.
        web3_a = _web3(CHAIN_A, {0: [_message_log(CHAIN_B, m1)]})
        web3_b = _web3(CHAIN_B, {0: [_message_log(CHAIN_A, m2)]})
        sim = CrosschainSimulator(ScriptedTransport())
        chain_a = sim.add_chain(
            CHAIN_A, web3_a, block=100, vault=RECEIVER, alpha=SENDER
        )
        sim.add_chain(CHAIN_B, web3_b, block=200)
        sim.fund_native(CHAIN_B, RECEIVER, 10**18)
        chain_a.add_call(Call(to=RECEIVER, data=b"\x00"), from_=SENDER, label="kick")
        sim.observe(
            CHAIN_A, "probe", Call(to=RECEIVER, data=b"\x01", output_types=["uint256"])
        )

        results = sim.relay()

        assert set(results) == {CHAIN_A, CHAIN_B}
        assert [m.message_id for m in sim.delivered] == [m1, m2]
        assert results[CHAIN_A].get("probe") == 32
        labels_b = [c.label for c in results[CHAIN_B].calls]
        assert labels_b == ["deliver:01"]
        labels_a = [c.label for c in results[CHAIN_A].calls]
        assert labels_a == ["kick", "probe", "deliver:02"]
        # Chain A ran twice (kick, then with the delivery); chain B once. Every
        # replay re-sent the full call list and the native override reached B.
        assert len(web3_a.provider.payloads) == 2
        assert len(web3_b.provider.payloads) == 1
        overrides = web3_b.provider.payloads[0][0]["blockStateCalls"][0][
            "stateOverrides"
        ]
        assert overrides[RECEIVER] == {"balance": hex(10**18)}
        assert sim.results is results or dict(sim.results) == results
        assert (
            sim.transport.transport_kind == CrosschainTransportKind.STARGATE_LAYERZERO
        )

    def test_relay_gives_up_on_endless_traffic(self):
        counter = iter(range(1, 100))

        class Endless(ScriptedProvider):
            def make_request(self, method, params):
                n = next(counter)
                self.script = {
                    0: [
                        _message_log(
                            CHAIN_B if self.chain_id == CHAIN_A else CHAIN_A,
                            bytes([n]) * 32,
                        )
                    ]
                }
                return super().make_request(method, params)

        web3_a, web3_b = _web3(CHAIN_A, {}), _web3(CHAIN_B, {})
        web3_a.provider = Endless(CHAIN_A, {})
        web3_b.provider = Endless(CHAIN_B, {})
        sim = CrosschainSimulator(ScriptedTransport())
        sim.add_chain(CHAIN_A, web3_a).add_call(
            Call(to=RECEIVER, data=b""), from_=SENDER
        )
        sim.add_chain(CHAIN_B, web3_b)
        with pytest.raises(RuntimeError, match="did not settle within 3 rounds"):
            sim.relay(max_rounds=3)

    def test_message_to_missing_chain_is_an_error(self):
        web3_a = _web3(CHAIN_A, {0: [_message_log(CHAIN_B, b"\x09" * 32)]})
        sim = CrosschainSimulator(ScriptedTransport())
        sim.add_chain(CHAIN_A, web3_a).add_call(
            Call(to=RECEIVER, data=b""), from_=SENDER
        )
        with pytest.raises(RuntimeError, match="was not added to the simulator"):
            sim.relay()

    def test_add_chain_validation_and_empty_relay(self):
        sim = CrosschainSimulator(ScriptedTransport())
        with pytest.raises(ValueError, match="not configured on the transport"):
            sim.add_chain(ChainId(3), _web3(3, {}))
        sim.add_chain(CHAIN_A, _web3(CHAIN_A, {}))
        with pytest.raises(ValueError, match="already added"):
            sim.add_chain(CHAIN_A, _web3(CHAIN_A, {}))
        assert sim.chain(CHAIN_A).has_calls is False
        assert sim.relay() == {}

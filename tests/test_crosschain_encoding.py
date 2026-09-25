"""Offline tests for the crosschain fuses, substrates and wire codecs.

Live fixtures: the substrates granted on the mainnet POC vault (Ethereum
0x0Aa75BfD…, market keccak256("IPOR_FUSION_CROSSCHAIN_USDC_POC_V1"),
read on 2026-09-24) and the well-known LayerZero option blobs.
"""

from __future__ import annotations

import pytest
from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector, keccak
from web3 import Web3

from ipor_fusion.crosschain import (
    EMPTY_COMMAND,
    Any2EVMMessage,
    BusinessAction,
    CcipMessageSent,
    CcipMsgType,
    CcipRouteConfig,
    Command,
    EnforcedOptionParam,
    EVMTokenAmount,
    MessageV1,
    MsgType,
    OptionsBuilder,
    Packet,
    TaxiMessage,
    ccip_receive_calldata,
    decode_ccip_envelope,
    decode_command,
    decode_envelope,
    encode_enforced_options,
    encode_oft_compose_msg,
    lz_compose_calldata,
    lz_receive_calldata,
)
from ipor_fusion.crosschain.ccip.codec import CCIP_MESSAGE_SENT_TOPIC
from ipor_fusion.crosschain.stargate.layerzero import (
    address_to_bytes32,
    bytes32_to_address,
)
from ipor_fusion.fuses.base import ZERO_ADDRESS
from ipor_fusion.fuses.crosschain import (
    CcipCommandType,
    CcipCrosschainCommandFuse,
    CcipCrosschainSupplyFuse,
    CcipSendParams,
    CrosschainClaimFuse,
    CrosschainCommandFuse,
    CrosschainSubstrate,
    CrosschainSubstrateLib,
    CrosschainSubstrateType,
    CrosschainSupplyFuse,
    StargateCrosschainCommandFuse,
    StargateCrosschainCommandType,
    StargateCrosschainSupplyFuse,
    StargateSendParams,
    crosschain_market_id,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.substrates import decode_substrate

FUSE = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
EXECUTOR = Web3.to_checksum_address("0x1d5c9d44f8d556ec7f557ae992401cc770937e6e")
REMOTE_VAULT_BASE = Web3.to_checksum_address(
    "0x7411cb6ca68dfedf5a8d09146ee277b11ddfb578"
)
REMOTE_VAULT_ARB = Web3.to_checksum_address(
    "0x174bfa12935ac416caa7d27397bd4f3b15175980"
)
USDC = Web3.to_checksum_address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
WITHDRAW_MANAGER = Web3.to_checksum_address(
    "0x2222222222222222222222222222222222222222"
)
RECEIVER = Web3.to_checksum_address("0x3333333333333333333333333333333333333333")

# Live grants on the POC vault: one EXECUTOR and two chain-bound REMOTE_VAULTs.
LIVE_EXECUTOR_SUBSTRATE = (
    "0x0100000000000000000000001d5c9d44f8d556ec7f557ae992401cc770937e6e"
)
LIVE_REMOTE_VAULT_BASE = (
    "0x0200000000000000000021057411cb6ca68dfedf5a8d09146ee277b11ddfb578"
)
LIVE_REMOTE_VAULT_ARB = (
    "0x02000000000000000000a4b1174bfa12935ac416caa7d27397bd4f3b15175980"
)
POC_MARKET_ID = crosschain_market_id("IPOR_FUSION_CROSSCHAIN_USDC_POC_V1")


def _decoded(action, signature: str) -> tuple:
    assert action.data[:4] == function_signature_to_4byte_selector(signature)
    tuple_type = signature[signature.index("(") + 1 : -1]
    (values,) = decode([tuple_type], action.data[4:])
    return values


# ── Command ────────────────────────────────────────────────────────────────


class TestCommand:
    def test_deposit_matches_solidity_action_tuple(self):
        cmd = Command.deposit(REMOTE_VAULT_BASE, 999_414, 1)
        assert cmd.action == BusinessAction.DEPOSIT
        assert cmd.action_data == encode(
            ["address", "uint256", "uint256"], [REMOTE_VAULT_BASE, 999_414, 1]
        )
        assert cmd.as_tuple() == (bytes(32), 0, 0, 1, cmd.action_data)
        assert cmd.target_vault == REMOTE_VAULT_BASE

    def test_redeem_and_request_and_redeem_from_request(self):
        redeem = Command.redeem(REMOTE_VAULT_BASE, 299_734_249, 2_990_000)
        assert redeem.decode_action_data() == (
            REMOTE_VAULT_BASE.lower(),
            299_734_249,
            2_990_000,
        )
        request = Command.request_shares(WITHDRAW_MANAGER, REMOTE_VAULT_ARB, 5)
        assert request.target_vault == REMOTE_VAULT_ARB
        claim = Command.redeem_from_request(
            WITHDRAW_MANAGER, REMOTE_VAULT_ARB, 5, 4, RECEIVER
        )
        assert claim.action == BusinessAction.REDEEM_FROM_REQUEST
        assert claim.decode_action_data()[-1] == RECEIVER.lower()
        assert claim.target_vault == REMOTE_VAULT_ARB

    def test_round_trip_through_tuple_and_envelope_decoders(self):
        cmd = Command.deposit(REMOTE_VAULT_BASE, 7)
        stamped = Command(
            cmd.action,
            cmd.action_data,
            command_id=b"\x01" * 32,
            sequence=3,
            command_config_epoch=1,
        )
        assert Command.from_tuple(stamped.as_tuple()) == stamped
        inner = encode(["(bytes32,uint64,uint64,uint8,bytes)"], [stamped.as_tuple()])
        assert decode_command(inner) == stamped
        envelope = encode(["uint8", "uint8", "bytes"], [3, int(MsgType.COMMAND), inner])
        assert decode_envelope(envelope) == (MsgType.COMMAND, inner)
        ccip = encode(["uint8", "uint8", "bytes"], [1, int(CcipMsgType.COMMAND), inner])
        assert decode_ccip_envelope(ccip) == (CcipMsgType.COMMAND, inner)

    def test_envelope_version_mismatch_rejected(self):
        with pytest.raises(ValueError, match="codec version"):
            decode_envelope(encode(["uint8", "uint8", "bytes"], [2, 9, b""]))
        with pytest.raises(ValueError, match="CCIP codec version"):
            decode_ccip_envelope(encode(["uint8", "uint8", "bytes"], [3, 5, b""]))

    def test_validation(self):
        with pytest.raises(ValueError, match="command_id must be 32 bytes"):
            Command(BusinessAction.DEPOSIT, b"", command_id=b"\x00")
        with pytest.raises(ValueError, match="vault"):
            Command.deposit(ZERO_ADDRESS, 1)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="carries no action data"):
            EMPTY_COMMAND.decode_action_data()
        assert EMPTY_COMMAND.target_vault is None


# ── Substrates ─────────────────────────────────────────────────────────────


class TestCrosschainSubstrateLib:
    def test_matches_live_grants(self):
        assert CrosschainSubstrateLib.executor_substrate(EXECUTOR) == bytes.fromhex(
            LIVE_EXECUTOR_SUBSTRATE[2:]
        )
        assert CrosschainSubstrateLib.remote_vault_substrate(
            8453, REMOTE_VAULT_BASE
        ) == bytes.fromhex(LIVE_REMOTE_VAULT_BASE[2:])
        assert CrosschainSubstrateLib.remote_vault_substrate(
            42161, REMOTE_VAULT_ARB
        ) == bytes.fromhex(LIVE_REMOTE_VAULT_ARB[2:])

    def test_round_trip(self):
        raw = CrosschainSubstrateLib.remote_vault_substrate(8453, REMOTE_VAULT_BASE)
        assert CrosschainSubstrateLib.bytes32_to_substrate(raw) == CrosschainSubstrate(
            CrosschainSubstrateType.REMOTE_VAULT, 8453, REMOTE_VAULT_BASE
        )
        executor = CrosschainSubstrateLib.bytes32_to_substrate(
            CrosschainSubstrateLib.executor_substrate(EXECUTOR)
        )
        assert executor.substrate_type == CrosschainSubstrateType.EXECUTOR
        assert executor.chain_id == 0

    def test_validation(self):
        with pytest.raises(ValueError, match="88 bits"):
            CrosschainSubstrateLib.remote_vault_substrate(1 << 88, REMOTE_VAULT_BASE)
        with pytest.raises(ValueError, match="chain_id must not be zero"):
            CrosschainSubstrateLib.remote_vault_substrate(0, REMOTE_VAULT_BASE)
        with pytest.raises(ValueError, match="zero address"):
            CrosschainSubstrateLib.executor_substrate(ZERO_ADDRESS)  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="32 bytes"):
            CrosschainSubstrateLib.bytes32_to_substrate(b"\x01")

    def test_decode_substrate_registry(self):
        info = decode_substrate(
            LIVE_EXECUTOR_SUBSTRATE, market_id=IporFusionMarkets.CROSSCHAIN
        )
        assert info.type_label == "EXECUTOR"
        assert info.address == EXECUTOR.lower()
        assert info.extra == {}
        info = decode_substrate(LIVE_REMOTE_VAULT_BASE, market_id=POC_MARKET_ID)
        assert info.type_label == "REMOTE_VAULT"
        assert info.address == REMOTE_VAULT_BASE.lower()
        assert info.extra == {"chain_id": "8453"}
        arb = decode_substrate(LIVE_REMOTE_VAULT_ARB, market_id=54)
        assert arb.extra == {"chain_id": "42161"}
        undefined = decode_substrate("0x00" + "00" * 31, market_id=54)
        assert undefined.type_label == "UNDEFINED"
        assert undefined.address == ""
        unknown = decode_substrate("0x09" + "00" * 31, market_id=54)
        assert unknown.type_label == "type=9"

    def test_poc_market_id_matches_deployment_manifest(self):
        assert POC_MARKET_ID == int(
            "0x3698b2215fdb7b90ebd6e623939afe7a334e14147954f78e1a42ddcbcbc9b576", 16
        )


# ── Fuses ──────────────────────────────────────────────────────────────────


class TestCrosschainClaimFuse:
    def test_enter(self):
        action = CrosschainClaimFuse(FUSE).enter(executor=EXECUTOR, amount=4_999_498)
        assert action.fuse == FUSE
        assert _decoded(action, "enter((address,uint256))") == (
            EXECUTOR.lower(),
            4_999_498,
        )

    def test_validation(self):
        with pytest.raises(ValueError, match="amount"):
            CrosschainClaimFuse(FUSE).enter(executor=EXECUTOR, amount=0)
        with pytest.raises(ValueError, match="executor"):
            CrosschainClaimFuse(FUSE).enter(executor=ZERO_ADDRESS, amount=1)  # type: ignore[arg-type]


class TestStargateCrosschainSupplyFuse:
    def test_enter_mirrors_bridge_script(self):
        options = (
            OptionsBuilder.new_options()
            .add_executor_lz_receive_option(250_000)
            .add_executor_lz_compose_option(0, 1_500_000)
        )
        action = StargateCrosschainSupplyFuse(FUSE).enter(
            executor=EXECUTOR,
            asset=USDC,
            dst_chain_id=8453,
            amount=1_000_000,
            send=StargateSendParams(options=options, min_amount_ld=990_000),
        )
        values = _decoded(
            action, "enter((address,address,uint256,uint256,uint256,bytes))"
        )
        assert values == (
            EXECUTOR.lower(),
            USDC.lower(),
            8453,
            1_000_000,
            990_000,
            bytes(options),
        )

    def test_exit_carries_native_drop(self):
        options = OptionsBuilder.new_options().add_executor_lz_receive_option(2_000_000)
        action = StargateCrosschainSupplyFuse(FUSE).exit(
            executor=EXECUTOR,
            dst_chain_id=42161,
            amount=3_000_000,
            min_return=2_970_000,
            send=StargateSendParams(options=bytes(options), native_drop=10**15),
        )
        values = _decoded(
            action, "exit((address,uint256,uint256,uint256,uint128,bytes))"
        )
        assert values == (
            EXECUTOR.lower(),
            42161,
            3_000_000,
            2_970_000,
            10**15,
            bytes(options),
        )

    def test_validation(self):
        fuse = StargateCrosschainSupplyFuse(FUSE)
        send = StargateSendParams(options=b"")
        with pytest.raises(ValueError, match="amount"):
            fuse.enter(
                executor=EXECUTOR, asset=USDC, dst_chain_id=8453, amount=0, send=send
            )
        with pytest.raises(ValueError, match="chain id"):
            fuse.enter(
                executor=EXECUTOR, asset=USDC, dst_chain_id=0, amount=1, send=send
            )
        with pytest.raises(ValueError, match="native_drop"):
            fuse.exit(
                executor=EXECUTOR,
                dst_chain_id=8453,
                amount=1,
                min_return=0,
                send=StargateSendParams(options=b"", native_drop=-1),
            )
        with pytest.raises(
            TypeError, match="takes StargateSendParams, got CcipSendParams"
        ):
            fuse.enter(
                executor=EXECUTOR,
                asset=USDC,
                dst_chain_id=8453,
                amount=1,
                send=CcipSendParams(max_fee=1, gas_limit=1),
            )


class TestStargateCrosschainCommandFuse:
    SIG = (
        "enter((uint8,address,uint256,uint32,bytes,address[],uint256,"
        "(bytes32,uint64,uint64,uint8,bytes)))"
    )

    def test_register_chain_mirrors_script(self):
        enforced = encode_enforced_options(
            [
                EnforcedOptionParam(
                    30184,
                    1,
                    OptionsBuilder.new_options().add_executor_lz_receive_option(
                        2_000_000
                    ),
                )
            ]
        )
        action = StargateCrosschainCommandFuse(FUSE).enter(
            StargateCrosschainCommandType.REGISTER_CHAIN,
            executor=EXECUTOR,
            chain_id=8453,
            eid=30184,
            enforced_options=enforced,
        )
        values = _decoded(action, self.SIG)
        assert values == (
            1,
            EXECUTOR.lower(),
            8453,
            30184,
            enforced,
            (),
            0,
            (bytes(32), 0, 0, 0, b""),
        )
        (params,) = decode(["(uint32,uint16,bytes)[]"], enforced)
        assert params == (
            (30184, 1, bytes.fromhex("0003010011" + "01" + f"{2_000_000:032x}")),
        )

    def test_send_command_mirrors_deposit_script(self):
        cmd = Command.deposit(REMOTE_VAULT_BASE, 999_414)
        action = StargateCrosschainCommandFuse(FUSE).enter(
            StargateCrosschainCommandType.SEND_COMMAND,
            executor=EXECUTOR,
            chain_id=8453,
            command=cmd,
        )
        values = _decoded(action, self.SIG)
        assert values[0] == 4
        assert values[7] == (bytes(32), 0, 0, 1, cmd.action_data)

    def test_vault_list_and_request_id_operations(self):
        fuse = StargateCrosschainCommandFuse(FUSE)
        action = fuse.enter(
            StargateCrosschainCommandType.CREATE_DISPATCHER,
            executor=EXECUTOR,
            chain_id=8453,
            plasma_vaults=[REMOTE_VAULT_BASE],
        )
        assert _decoded(action, self.SIG)[5] == (REMOTE_VAULT_BASE.lower(),)
        action = fuse.enter(
            StargateCrosschainCommandType.CANCEL_DISPATCHER_REQUEST,
            executor=EXECUTOR,
            request_id=7,
        )
        assert _decoded(action, self.SIG)[6] == 7
        for command_type in (
            StargateCrosschainCommandType.RETRY_COMMAND,
            StargateCrosschainCommandType.CANCEL_COMMAND,
            StargateCrosschainCommandType.UPDATE_PLASMA_VAULTS,
        ):
            assert _decoded(
                fuse.enter(command_type, executor=EXECUTOR, chain_id=1), self.SIG
            )[0] == int(command_type)

    @pytest.mark.parametrize(
        ("command_type", "kwargs", "message"),
        [
            (StargateCrosschainCommandType.UNDEFINED, {}, "UNDEFINED"),
            (StargateCrosschainCommandType.SEND_COMMAND, {}, "requires chain_id"),
            (
                StargateCrosschainCommandType.SEND_COMMAND,
                {"chain_id": 1},
                "business action",
            ),
            (
                StargateCrosschainCommandType.REGISTER_CHAIN,
                {"chain_id": 1},
                "requires eid",
            ),
            (
                StargateCrosschainCommandType.CANCEL_DISPATCHER_REQUEST,
                {},
                "requires request_id",
            ),
            (
                StargateCrosschainCommandType.CREATE_DISPATCHER,
                {"chain_id": 1, "plasma_vaults": [ZERO_ADDRESS]},
                "plasma_vaults\\[0\\]",
            ),
        ],
    )
    def test_validation(self, command_type, kwargs, message):
        with pytest.raises(ValueError, match=message):
            StargateCrosschainCommandFuse(FUSE).enter(
                command_type, executor=EXECUTOR, **kwargs
            )


class TestCcipCrosschainSupplyFuse:
    def test_enter_and_exit(self):
        fuse = CcipCrosschainSupplyFuse(FUSE)
        action = fuse.enter(
            executor=EXECUTOR,
            asset=USDC,
            dst_chain_id=8453,
            amount=100_000,
            send=CcipSendParams(max_fee=10**16, gas_limit=1_000_000),
        )
        assert _decoded(
            action, "enter((address,address,uint256,uint256,uint256,address,uint256))"
        ) == (
            EXECUTOR.lower(),
            USDC.lower(),
            8453,
            100_000,
            10**16,
            ZERO_ADDRESS,
            1_000_000,
        )
        action = fuse.exit(
            executor=EXECUTOR,
            dst_chain_id=8453,
            amount=100_000,
            min_return=99_000,
            send=CcipSendParams(max_fee=10**16, gas_limit=1_200_000, fee_token=USDC),
        )
        assert _decoded(
            action, "exit((address,uint256,uint256,uint256,uint256,address,uint256))"
        ) == (EXECUTOR.lower(), 8453, 100_000, 99_000, 10**16, USDC.lower(), 1_200_000)

    def test_validation(self):
        fuse = CcipCrosschainSupplyFuse(FUSE)
        with pytest.raises(ValueError, match="max_fee"):
            fuse.exit(
                executor=EXECUTOR,
                dst_chain_id=1,
                amount=1,
                min_return=0,
                send=CcipSendParams(max_fee=-1, gas_limit=0),
            )
        with pytest.raises(
            TypeError, match="takes CcipSendParams, got StargateSendParams"
        ):
            fuse.exit(
                executor=EXECUTOR,
                dst_chain_id=1,
                amount=1,
                min_return=0,
                send=StargateSendParams(options=b""),
            )

    def test_send_params_from_route(self):
        route = CcipRouteConfig(
            chain_selector=1,
            peer=EXECUTOR,
            fee_token=USDC,
            message_gas_limit=1_200_000,
            token_gas_limit=1_000_000,
            max_fee=10**16,
            enabled=True,
        )
        assert CcipSendParams.from_route(route, token=True) == CcipSendParams(
            max_fee=10**16, gas_limit=1_000_000, fee_token=USDC
        )
        assert CcipSendParams.from_route(route, token=False).gas_limit == 1_200_000


class TestTransportAgnosticCommandOperations:
    """The base-class operations encode exactly what the per-transport
    ``enter`` variants encode, with the transport specifics in ``send``."""

    STARGATE_SIG = (
        TestStargateCrosschainCommandFuse.SIG
        if False
        else (
            "enter((uint8,address,uint256,uint32,bytes,address[],uint256,"
            "(bytes32,uint64,uint64,uint8,bytes)))"
        )
    )
    CCIP_SIG = (
        "enter((uint8,address,uint256,(uint64,address,address,uint96,uint96,uint256,bool),"
        "address[],bool,bool,(bytes32,uint64,uint64,uint8,bytes),uint256,address,uint256))"
    )

    def test_bases_are_abstract(self):
        for base in (CrosschainSupplyFuse, CrosschainCommandFuse):
            with pytest.raises(TypeError):
                base(FUSE)  # type: ignore[abstract]
        assert issubclass(StargateCrosschainSupplyFuse, CrosschainSupplyFuse)
        assert issubclass(CcipCrosschainSupplyFuse, CrosschainSupplyFuse)
        assert issubclass(StargateCrosschainCommandFuse, CrosschainCommandFuse)
        assert issubclass(CcipCrosschainCommandFuse, CrosschainCommandFuse)

    def test_stargate_command_lane_takes_no_send_params(self):
        fuse = StargateCrosschainCommandFuse(FUSE)
        cmd = Command.deposit(REMOTE_VAULT_BASE, 5)
        assert fuse.send_command(
            executor=EXECUTOR, chain_id=8453, command=cmd
        ) == fuse.enter(
            StargateCrosschainCommandType.SEND_COMMAND,
            executor=EXECUTOR,
            chain_id=8453,
            command=cmd,
        )
        assert fuse.create_dispatcher(
            executor=EXECUTOR, chain_id=8453, plasma_vaults=[REMOTE_VAULT_BASE]
        ) == fuse.enter(
            StargateCrosschainCommandType.CREATE_DISPATCHER,
            executor=EXECUTOR,
            chain_id=8453,
            plasma_vaults=[REMOTE_VAULT_BASE],
        )
        assert _decoded(
            fuse.update_vaults(
                executor=EXECUTOR, chain_id=8453, plasma_vaults=[REMOTE_VAULT_BASE]
            ),
            self.STARGATE_SIG,
        )[0] == int(StargateCrosschainCommandType.UPDATE_PLASMA_VAULTS)
        with pytest.raises(TypeError, match="takes no send params"):
            fuse.send_command(
                executor=EXECUTOR,
                chain_id=8453,
                command=cmd,
                send=StargateSendParams(options=b""),
            )

    def test_ccip_operations_carry_send_params(self):
        fuse = CcipCrosschainCommandFuse(FUSE)
        cmd = Command.redeem(REMOTE_VAULT_BASE, 10)
        send = CcipSendParams(max_fee=10**16, gas_limit=1_200_000)
        values = _decoded(
            fuse.send_command(executor=EXECUTOR, chain_id=8453, command=cmd, send=send),
            self.CCIP_SIG,
        )
        assert values[0] == int(CcipCommandType.SEND_COMMAND)
        assert values[7] == cmd.as_tuple()
        assert values[8:] == (10**16, ZERO_ADDRESS, 1_200_000)
        values = _decoded(
            fuse.create_dispatcher(
                executor=EXECUTOR,
                chain_id=8453,
                plasma_vaults=[REMOTE_VAULT_BASE],
                send=send,
            ),
            self.CCIP_SIG,
        )
        assert values[0] == int(CcipCommandType.CREATE_DISPATCHER)
        assert values[4] == (REMOTE_VAULT_BASE.lower(),)
        assert values[8] == 10**16
        values = _decoded(
            fuse.update_vaults(
                executor=EXECUTOR,
                chain_id=8453,
                plasma_vaults=[REMOTE_VAULT_BASE],
                send=send,
            ),
            self.CCIP_SIG,
        )
        assert values[0] == int(CcipCommandType.UPDATE_VAULTS)
        assert values[4:7] == ((REMOTE_VAULT_BASE.lower(),), True, True)
        assert values[8:] == (10**16, ZERO_ADDRESS, 1_200_000)
        with pytest.raises(TypeError, match="takes CcipSendParams, got NoneType"):
            fuse.send_command(executor=EXECUTOR, chain_id=8453, command=cmd)


class TestCcipCrosschainCommandFuse:
    SIG = (
        "enter((uint8,address,uint256,(uint64,address,address,uint96,uint96,uint256,bool),"
        "address[],bool,bool,(bytes32,uint64,uint64,uint8,bytes),uint256,address,uint256))"
    )
    ROUTE = CcipRouteConfig(
        chain_selector=15971525489660198786,
        peer=EXECUTOR,
        fee_token=ZERO_ADDRESS,  # type: ignore[arg-type]
        message_gas_limit=1_200_000,
        token_gas_limit=1_000_000,
        max_fee=10**16,
        enabled=True,
    )

    def test_register_route(self):
        action = CcipCrosschainCommandFuse(FUSE).enter(
            CcipCommandType.REGISTER_ROUTE,
            executor=EXECUTOR,
            chain_id=8453,
            route=self.ROUTE,
        )
        values = _decoded(action, self.SIG)
        assert values[0] == 1
        assert values[3] == (
            15971525489660198786,
            EXECUTOR.lower(),
            ZERO_ADDRESS,
            1_200_000,
            1_000_000,
            10**16,
            True,
        )
        assert CcipRouteConfig.from_tuple(values[3]) == self.ROUTE

    def test_send_and_update_and_retry(self):
        fuse = CcipCrosschainCommandFuse(FUSE)
        cmd = Command.redeem(REMOTE_VAULT_BASE, 10)
        values = _decoded(
            fuse.enter(
                CcipCommandType.SEND_COMMAND,
                executor=EXECUTOR,
                chain_id=8453,
                command=cmd,
                max_fee=10**16,
                gas_limit=1_200_000,
            ),
            self.SIG,
        )
        assert values[0] == 4
        assert values[7] == cmd.as_tuple()
        assert values[8:] == (10**16, ZERO_ADDRESS, 1_200_000)
        values = _decoded(
            fuse.enter(
                CcipCommandType.UPDATE_VAULTS,
                executor=EXECUTOR,
                chain_id=8453,
                plasma_vaults=[REMOTE_VAULT_BASE],
                vaults_allowed=True,
                vaults_replace=True,
            ),
            self.SIG,
        )
        assert values[4:7] == ((REMOTE_VAULT_BASE.lower(),), True, True)
        stamped = Command(BusinessAction.NONE, b"", command_id=b"\x07" * 32)
        values = _decoded(
            fuse.enter(
                CcipCommandType.RETRY_COMMAND,
                executor=EXECUTOR,
                chain_id=8453,
                command=stamped,
            ),
            self.SIG,
        )
        assert values[7][0] == b"\x07" * 32
        for command_type in (
            CcipCommandType.REFRESH_BALANCE,
            CcipCommandType.CANCEL_RETURN,
        ):
            assert _decoded(
                fuse.enter(command_type, executor=EXECUTOR, chain_id=8453), self.SIG
            )[0] == int(command_type)

    @pytest.mark.parametrize(
        ("command_type", "kwargs", "message"),
        [
            (CcipCommandType.UNDEFINED, {"chain_id": 1}, "UNDEFINED"),
            (CcipCommandType.SEND_COMMAND, {"chain_id": 0}, "requires chain_id"),
            (CcipCommandType.REGISTER_ROUTE, {"chain_id": 1}, "requires route"),
            (CcipCommandType.CANCEL_COMMAND, {"chain_id": 1}, "requires command"),
            (
                CcipCommandType.CREATE_DISPATCHER,
                {"chain_id": 1, "plasma_vaults": [ZERO_ADDRESS]},
                "plasma_vaults\\[0\\]",
            ),
        ],
    )
    def test_validation(self, command_type, kwargs, message):
        with pytest.raises(ValueError, match=message):
            CcipCrosschainCommandFuse(FUSE).enter(
                command_type, executor=EXECUTOR, **kwargs
            )


# ── LayerZero codecs ───────────────────────────────────────────────────────


class TestOptionsBuilder:
    def test_well_known_blobs(self):
        # The canonical LayerZero examples: 200k gas, and 60k gas.
        assert bytes(
            OptionsBuilder.new_options().add_executor_lz_receive_option(200_000)
        ) == bytes.fromhex("00030100110100000000000000000000000000030d40")
        assert bytes(
            OptionsBuilder.new_options().add_executor_lz_receive_option(60_000)
        ) == bytes.fromhex("0003010011010000000000000000000000000000ea60")

    def test_value_compose_and_native_drop_layouts(self):
        blob = bytes(
            OptionsBuilder.new_options()
            .add_executor_lz_receive_option(250_000, 5)
            .add_executor_lz_compose_option(0, 1_500_000)
            .add_executor_native_drop_option(10**15, EXECUTOR)
        )
        expected = (
            "0003"
            + "01"
            + "0021"
            + "01"
            + f"{250_000:032x}"
            + f"{5:032x}"
            + "01"
            + "0013"
            + "03"
            + "0000"
            + f"{1_500_000:032x}"
            + "01"
            + "0031"
            + "02"
            + f"{10**15:032x}"
            + address_to_bytes32(EXECUTOR).hex()
        )
        assert blob == bytes.fromhex(expected)

    def test_validation(self):
        with pytest.raises(ValueError, match="gas"):
            OptionsBuilder.new_options().add_executor_lz_receive_option(1 << 128)
        with pytest.raises(ValueError, match="index"):
            OptionsBuilder.new_options().add_executor_lz_compose_option(1 << 16, 1)
        with pytest.raises(ValueError, match="receiver"):
            OptionsBuilder.new_options().add_executor_native_drop_option(
                1, ZERO_ADDRESS
            )  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="params"):
            encode_enforced_options([])


def _packet_bytes(
    *,
    nonce: int,
    src_eid: int,
    sender: str,
    dst_eid: int,
    receiver: str,
    guid: bytes,
    message: bytes,
) -> bytes:
    return (
        b"\x01"
        + nonce.to_bytes(8, "big")
        + src_eid.to_bytes(4, "big")
        + address_to_bytes32(sender)  # type: ignore[arg-type]
        + dst_eid.to_bytes(4, "big")
        + address_to_bytes32(receiver)  # type: ignore[arg-type]
        + guid
        + message
    )


class TestLayerZeroPacketCodecs:
    def test_packet_decode(self):
        encoded = _packet_bytes(
            nonce=9,
            src_eid=30101,
            sender=EXECUTOR,
            dst_eid=30184,
            receiver=EXECUTOR,
            guid=b"\xab" * 32,
            message=b"payload",
        )
        packet = Packet.decode(encoded)
        assert packet == Packet(
            9, 30101, EXECUTOR, 30184, EXECUTOR, b"\xab" * 32, b"payload"
        )
        log = {
            "data": "0x"
            + encode(["bytes", "bytes", "address"], [encoded, b"", ZERO_ADDRESS]).hex()
        }
        assert Packet.from_log(log) == packet
        with pytest.raises(ValueError, match="version-1"):
            Packet.decode(b"\x02" + encoded[1:])
        with pytest.raises(ValueError, match="left-padded"):
            bytes32_to_address(b"\x01" * 32)

    def test_taxi_decode_and_compose_encode(self):
        envelope = encode(["uint8", "uint8", "bytes"], [3, 5, b"\x11" * 32])
        compose_msg = address_to_bytes32(EXECUTOR) + envelope
        message = (
            b"\x01"
            + (1).to_bytes(2, "big")
            + address_to_bytes32(EXECUTOR)
            + (999_414).to_bytes(8, "big")
            + compose_msg
        )
        taxi = TaxiMessage.decode(message)
        assert taxi.asset_id == 1
        assert taxi.receiver == EXECUTOR
        assert taxi.amount_sd == 999_414
        assert taxi.compose_from == EXECUTOR
        assert taxi.compose_payload == envelope
        bare = TaxiMessage.decode(message[:43])
        assert bare.compose_from is None and bare.compose_payload == b""
        with pytest.raises(ValueError, match="taxi"):
            TaxiMessage.decode(b"\x02" + message[1:])
        compose = encode_oft_compose_msg(9, 30101, 999_414, taxi.compose_msg)
        assert compose[:8] == (9).to_bytes(8, "big")
        assert compose[8:12] == (30101).to_bytes(4, "big")
        assert int.from_bytes(compose[12:44], "big") == 999_414
        assert compose[44:76] == address_to_bytes32(EXECUTOR)
        assert compose[76:] == envelope

    def test_delivery_calldata(self):
        data = lz_receive_calldata(
            src_eid=30101, sender=EXECUTOR, nonce=4, guid=b"\x01" * 32, message=b"\x02"
        )
        assert data[:4] == function_signature_to_4byte_selector(
            "lzReceive((uint32,bytes32,uint64),bytes32,bytes,address,bytes)"
        )
        origin, guid, message, executor, extra = decode(
            ["(uint32,bytes32,uint64)", "bytes32", "bytes", "address", "bytes"],
            data[4:],
        )
        assert origin == (30101, address_to_bytes32(EXECUTOR), 4)
        assert (guid, message, executor, extra) == (
            b"\x01" * 32,
            b"\x02",
            ZERO_ADDRESS,
            b"",
        )
        data = lz_compose_calldata(from_=USDC, guid=b"\x03" * 32, message=b"\x04")
        assert data[:4] == function_signature_to_4byte_selector(
            "lzCompose(address,bytes32,bytes,address,bytes)"
        )
        assert decode(
            ["address", "bytes32", "bytes", "address", "bytes"], data[4:]
        ) == (
            USDC.lower(),
            b"\x03" * 32,
            b"\x04",
            ZERO_ADDRESS,
            b"",
        )


# ── CCIP codecs ────────────────────────────────────────────────────────────


def encode_message_v1(
    *,
    source_selector: int,
    dest_selector: int,
    sender: str,
    receiver: str,
    data: bytes,
    transfer: tuple[int, str] | None = None,
) -> bytes:
    """``MessageV1Codec._encodeMessageV1`` for an EVM lane (test-side encoder)."""

    def prefixed(raw: bytes, size: int) -> bytes:
        return len(raw).to_bytes(size, "big") + raw

    token = b""
    if transfer is not None:
        amount, dest_token = transfer
        token = (
            b"\x01"
            + amount.to_bytes(32, "big")
            + prefixed(b"\x0a" * 20, 1)
            + prefixed(b"\x0b" * 20, 1)
            + prefixed(bytes.fromhex(dest_token[2:]), 1)
            + prefixed(bytes.fromhex(receiver[2:]), 1)
            + prefixed(b"", 2)
        )
    return (
        b"\x01"
        + source_selector.to_bytes(8, "big")
        + dest_selector.to_bytes(8, "big")
        + (5).to_bytes(8, "big")
        + (1_200_000).to_bytes(4, "big")
        + (1_000_000).to_bytes(4, "big")
        + b"\x00" * 4
        + b"\xcc" * 32
        + prefixed(b"\x0c" * 20, 1)
        + prefixed(b"\x0d" * 20, 1)
        + prefixed(encode(["address"], [sender]), 1)
        + prefixed(bytes.fromhex(receiver[2:]), 1)
        + prefixed(b"blob", 2)
        + prefixed(token, 2)
        + prefixed(data, 2)
    )


def ccip_message_sent_log(
    encoded: bytes, *, dest_selector: int, sender: str, message_id: bytes
) -> dict:
    return {
        "address": "0xc3423f3fb30857d9c14717b119884b1b63d250b7",
        "topics": [
            "0x" + CCIP_MESSAGE_SENT_TOPIC.hex(),
            "0x" + dest_selector.to_bytes(32, "big").hex(),
            "0x" + address_to_bytes32(sender).hex(),  # type: ignore[arg-type]
            "0x" + message_id.hex(),
        ],
        "data": "0x"
        + encode(
            [
                "address",
                "uint256",
                "bytes",
                "(address,uint32,uint32,uint256,bytes)[]",
                "bytes[]",
            ],
            [ZERO_ADDRESS, 0, encoded, [], []],
        ).hex(),
    }


class TestCcipCodecs:
    def test_message_v1_decode_with_token_transfer(self):
        payload = encode(["uint8", "uint8", "bytes"], [1, 1, b"\x77" * 32])
        encoded = encode_message_v1(
            source_selector=5009297550715157269,
            dest_selector=15971525489660198786,
            sender=EXECUTOR,
            receiver=EXECUTOR,
            data=payload,
            transfer=(100_000, USDC),
        )
        message = MessageV1.decode(encoded)
        assert message.source_chain_selector == 5009297550715157269
        assert message.dest_chain_selector == 15971525489660198786
        assert message.message_number == 5
        assert message.execution_gas_limit == 1_200_000
        assert message.sender_address == EXECUTOR
        assert message.receiver_address == EXECUTOR
        assert message.dest_blob == b"blob"
        assert message.data == payload
        (transfer,) = message.token_transfer
        assert transfer.amount == 100_000
        assert transfer.dest_token_address == bytes.fromhex(USDC[2:])
        assert transfer.token_receiver == bytes.fromhex(EXECUTOR[2:])
        assert (
            MessageV1.decode(
                encode_message_v1(
                    source_selector=1,
                    dest_selector=2,
                    sender=EXECUTOR,
                    receiver=EXECUTOR,
                    data=b"",
                )
            ).token_transfer
            == ()
        )

    def test_message_v1_rejects_malformed(self):
        good = encode_message_v1(
            source_selector=1,
            dest_selector=2,
            sender=EXECUTOR,
            receiver=EXECUTOR,
            data=b"x",
        )
        with pytest.raises(ValueError, match="message version"):
            MessageV1.decode(b"\x02" + good[1:])
        with pytest.raises(ValueError, match="trailing bytes in CCIP MessageV1"):
            MessageV1.decode(good + b"\x00")
        with pytest.raises(ValueError, match="truncated"):
            MessageV1.decode(good[:-1])
        with_token = encode_message_v1(
            source_selector=1,
            dest_selector=2,
            sender=EXECUTOR,
            receiver=EXECUTOR,
            data=b"",
            transfer=(1, USDC),
        )
        # Corrupt the token transfer version byte (right after its 2-byte length).
        index = with_token.index(b"\x01" + (1).to_bytes(32, "big"))
        bad = with_token[:index] + b"\x02" + with_token[index + 1 :]
        with pytest.raises(ValueError, match="token transfer version"):
            MessageV1.decode(bad)

    def test_message_sent_log(self):
        encoded = encode_message_v1(
            source_selector=1,
            dest_selector=2,
            sender=EXECUTOR,
            receiver=REMOTE_VAULT_BASE,
            data=b"d",
        )
        message_id = keccak(encoded)
        sent = CcipMessageSent.from_log(
            ccip_message_sent_log(
                encoded, dest_selector=2, sender=EXECUTOR, message_id=message_id
            )
        )
        assert sent.message_id == message_id
        assert sent.dest_chain_selector == 2
        assert sent.sender == EXECUTOR
        assert sent.message.receiver_address == REMOTE_VAULT_BASE
        with pytest.raises(ValueError, match="CCIPMessageSent"):
            CcipMessageSent.from_log(
                {"topics": ["0x" + b"\x00".hex() * 32], "data": "0x"}
            )

    def test_ccip_receive_calldata(self):
        data = ccip_receive_calldata(
            Any2EVMMessage(
                message_id=b"\x05" * 32,
                source_chain_selector=5009297550715157269,
                sender=EXECUTOR,
                data=b"\x06",
                dest_token_amounts=(EVMTokenAmount(USDC, 100_000),),
            )
        )
        assert data[:4] == function_signature_to_4byte_selector(
            "ccipReceive((bytes32,uint64,bytes,bytes,(address,uint256)[]))"
        )
        (message,) = decode(
            ["(bytes32,uint64,bytes,bytes,(address,uint256)[])"], data[4:]
        )
        assert message == (
            b"\x05" * 32,
            5009297550715157269,
            encode(["address"], [EXECUTOR]),
            b"\x06",
            ((USDC.lower(), 100_000),),
        )

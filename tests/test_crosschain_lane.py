"""Offline tests for the lanes: the transport-agnostic surface encodes what
the per-transport fuses encode, normalizes reads, and finds its own parts."""

from __future__ import annotations

from dataclasses import astuple
from unittest.mock import MagicMock

import pytest
from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector as selector
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion import (
    CcipLane,
    CrosschainLane,
    LaneFuses,
    LaneObservation,
    StargateLane,
    detect_transport,
    discover_lane_fuses,
    open_lane,
)
from ipor_fusion.crosschain import (
    BalanceObservation,
    CcipCrosschainDispatcher,
    CcipCrosschainExecutor,
    CcipRouteConfig,
    Command,
    CrosschainTransportKind,
    OptionsBuilder,
    StargateCrosschainDispatcher,
    StargateCrosschainExecutor,
)
from ipor_fusion.fuses.base import ZERO_ADDRESS
from ipor_fusion.fuses.crosschain import (
    CcipCrosschainCommandFuse,
    CcipCrosschainSupplyFuse,
    CcipSendParams,
    CrosschainClaimFuse,
    StargateCrosschainCommandFuse,
    StargateCrosschainSupplyFuse,
    StargateSendParams,
)
from ipor_fusion.types import ChainId

EXECUTOR = Web3.to_checksum_address("0x1d5c9d44f8d556ec7f557ae992401cc770937e6e")
VAULT = Web3.to_checksum_address("0x0Aa75BfD30Ae2d4061FF8261453b933Ab5959991")
REMOTE_VAULT = Web3.to_checksum_address("0x7411cb6ca68dfedf5a8d09146ee277b11ddfb578")
USDC = Web3.to_checksum_address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
POOL = Web3.to_checksum_address("0xc026395860Db2d07ee33e05fE50ed7bD583189C7")
FUSES = LaneFuses(
    supply=Web3.to_checksum_address("0x1111111111111111111111111111111111111111"),
    command=Web3.to_checksum_address("0x2222222222222222222222222222222222222222"),
    claim=Web3.to_checksum_address("0x3333333333333333333333333333333333333333"),
)
SPOKE = ChainId(8453)
ROUTE = CcipRouteConfig(
    chain_selector=15971525489660198786,
    peer=EXECUTOR,
    fee_token=ZERO_ADDRESS,  # type: ignore[arg-type]
    message_gas_limit=1_200_000,
    token_gas_limit=1_000_000,
    max_fee=10**16,
    enabled=True,
)
HASH = b"\x0f" * 32
OBSERVATION = BalanceObservation(
    SPOKE, 999_700, 6, 51_723_200, 1_790_235_747, 1_790_237_651, HASH
)


def _stargate_lane(hub_ctx=None, spoke_ctx=None) -> StargateLane:
    return StargateLane(
        executor=StargateCrosschainExecutor(hub_ctx or MagicMock(), EXECUTOR),
        dispatcher=StargateCrosschainDispatcher(spoke_ctx or MagicMock(), EXECUTOR),
        spoke_chain_id=SPOKE,
        fuses=FUSES,
    )


def _ccip_lane(hub_ctx=None, spoke_ctx=None, rate: int = 1) -> CcipLane:
    return CcipLane(
        executor=CcipCrosschainExecutor(hub_ctx or MagicMock(), EXECUTOR),
        dispatcher=CcipCrosschainDispatcher(spoke_ctx or MagicMock(), EXECUTOR),
        spoke_chain_id=SPOKE,
        fuses=FUSES,
        route=ROUTE,
        decimal_conversion_rate=rate,
    )


def test_lane_rejects_a_dispatcher_at_another_address():
    with pytest.raises(ValueError, match="must share one address"):
        StargateLane(
            executor=StargateCrosschainExecutor(MagicMock(), EXECUTOR),
            dispatcher=StargateCrosschainDispatcher(MagicMock(), REMOTE_VAULT),
            spoke_chain_id=SPOKE,
            fuses=FUSES,
        )


class TestActions:
    def test_stargate_actions_match_the_fuses(self):
        lane = _stargate_lane()
        supply = StargateCrosschainSupplyFuse(FUSES.supply)
        assert lane.supply(
            asset=USDC, amount=1_000_000, min_received=990_000
        ) == supply.enter(
            executor=EXECUTOR,
            asset=USDC,
            dst_chain_id=SPOKE,
            amount=1_000_000,
            send=StargateSendParams(
                options=StargateLane.DEFAULT_SUPPLY_OPTIONS, min_amount_ld=990_000
            ),
        )
        custom = StargateSendParams(
            options=OptionsBuilder.new_options().add_executor_lz_receive_option(1),
            native_drop=10**15,
        )
        assert lane.recall(amount=5, min_return=4, send=custom) == supply.exit(
            executor=EXECUTOR, dst_chain_id=SPOKE, amount=5, min_return=4, send=custom
        )
        assert lane.recall(amount=5, min_return=4) == supply.exit(
            executor=EXECUTOR,
            dst_chain_id=SPOKE,
            amount=5,
            min_return=4,
            send=StargateSendParams(options=StargateLane.DEFAULT_RECALL_OPTIONS),
        )
        cmd = Command.deposit(REMOTE_VAULT, 7)
        assert lane.send_command(cmd) == StargateCrosschainCommandFuse(
            FUSES.command
        ).send_command(executor=EXECUTOR, chain_id=SPOKE, command=cmd)
        assert lane.claim(9) == CrosschainClaimFuse(FUSES.claim).enter(
            executor=EXECUTOR, amount=9
        )
        assert lane.executor_address == EXECUTOR
        assert lane.transport_kind == CrosschainTransportKind.STARGATE_LAYERZERO
        with pytest.raises(TypeError, match="StargateLane takes StargateSendParams"):
            lane.supply(
                asset=USDC,
                amount=1,
                min_received=1,
                send=CcipSendParams(max_fee=1, gas_limit=1),
            )

    def test_ccip_actions_match_the_fuses(self):
        lane = _ccip_lane()
        supply = CcipCrosschainSupplyFuse(FUSES.supply)
        token_send = CcipSendParams(max_fee=10**16, gas_limit=1_000_000)
        message_send = CcipSendParams(max_fee=10**16, gas_limit=1_200_000)
        assert lane.supply(
            asset=USDC, amount=100_000, min_received=100_000
        ) == supply.enter(
            executor=EXECUTOR,
            asset=USDC,
            dst_chain_id=SPOKE,
            amount=100_000,
            send=token_send,
        )
        assert lane.recall(amount=5, min_return=4) == supply.exit(
            executor=EXECUTOR,
            dst_chain_id=SPOKE,
            amount=5,
            min_return=4,
            send=message_send,
        )
        cmd = Command.redeem(REMOTE_VAULT, 3)
        assert lane.send_command(cmd) == CcipCrosschainCommandFuse(
            FUSES.command
        ).send_command(
            executor=EXECUTOR, chain_id=SPOKE, command=cmd, send=message_send
        )
        assert lane.default_command_send() == message_send
        with pytest.raises(ValueError, match="min_received 2 exceeds amount 1"):
            lane.supply(asset=USDC, amount=1, min_received=2)
        with pytest.raises(TypeError, match="CcipLane takes CcipSendParams"):
            lane.supply(
                asset=USDC,
                amount=1,
                min_received=1,
                send=StargateSendParams(options=b""),
            )


class TestReads:
    def test_executor_reads_target_the_spoke(self):
        lane = _stargate_lane()
        for call, sig in (
            (lane.settled_remote_balance(), "settledRemoteBalance(uint256)"),
            (lane.outbound_in_flight(), "outboundInFlight(uint256)"),
            (lane.is_dispatcher_ready(), "isDispatcherReady(uint256)"),
            (lane.chain_blocked(), "chainBlocked(uint256)"),
            (lane.remote_state_version(), "acknowledgedRemoteStateVersion(uint256)"),
            (lane.pending_transfer_count(), "pendingTransferCount(uint256)"),
        ):
            assert call.data == selector(sig) + encode(["uint256"], [SPOKE])
        assert lane.idle_ledger().data == selector("idleLedger()")
        assert lane.get_balance().data == selector("getBalance()")
        ccip = _ccip_lane()
        assert ccip.remote_state_version().data == selector(
            "lastRemoteStateVersion(uint256)"
        ) + encode(["uint256"], [SPOKE])

    def test_has_active_command_is_normalized(self):
        ctx = MagicMock()
        ctx.call.return_value = encode(
            ["bool", "bool", "bool", "uint8", "bytes32", "uint64", "uint64"],
            [True, False, False, 1, HASH, 4, 0],
        )
        assert _stargate_lane(hub_ctx=ctx).has_active_command().call() is True
        ctx.call.return_value = encode(["bytes32"], [bytes(32)])
        assert _ccip_lane(hub_ctx=ctx).has_active_command().call() is False
        ctx.call.return_value = encode(["bytes32"], [HASH])
        assert _ccip_lane(hub_ctx=ctx).has_active_command().call() is True

    def test_observation_is_normalized(self):
        ctx = MagicMock()
        ctx.call.return_value = encode(
            ["(uint256,address[],uint256[],uint64,uint64,bytes32)"],
            [(100, [REMOTE_VAULT.lower()], [900], 5, 1, HASH)],
        )
        assert _stargate_lane(spoke_ctx=ctx).observation().call() == LaneObservation(
            100, 1000, 5, 1, HASH
        )
        ctx.call.return_value = encode(
            ["(uint256,uint256,uint256,uint64,uint64,bytes32)"],
            [(1000, 100, 0, 4, 1, HASH)],
        )
        assert _ccip_lane(
            spoke_ctx=ctx, rate=10**12
        ).observation().call() == LaneObservation(
            100 * 10**12, 1000 * 10**12, 4, 1, HASH
        )


class TestAttestation:
    def test_propose_encodes_per_transport(self):
        call = _stargate_lane().propose_balance(OBSERVATION)
        assert call.data[:4] == selector(
            "proposeBalance((uint256,uint256,uint64,uint64,uint64,uint64,bytes32))"
        )
        assert call.output_types is None
        call = _ccip_lane().propose_balance(OBSERVATION)
        assert call.data[:4] == selector(
            "proposeBalance(uint256,uint256,uint64,uint64,uint64,uint64,bytes32)"
        )
        assert call.output_types == ["uint256"]
        assert decode(["uint256"] * 2, call.data[4:68]) == (SPOKE, 999_700)
        assert _ccip_lane().approve_balance(3).data == selector(
            "approveBalance(uint256)"
        ) + encode(["uint256"], [3])
        with pytest.raises(ValueError, match="this lane serves 8453"):
            _ccip_lane().propose_balance(
                BalanceObservation(ChainId(1), 1, 1, 1, 1, 1, HASH)
            )

    @pytest.mark.parametrize("make", [_stargate_lane, _ccip_lane])
    def test_proposal_id_from_logs(self, make):
        lane = make()
        proposed = {
            "address": EXECUTOR.lower(),
            "topics": ["0x" + lane.BALANCE_PROPOSED_TOPIC.hex()],
            "data": "0x"
            + encode(["uint256", "uint256", "uint256"], [42, SPOKE, 999_700]).hex(),
        }
        other_contract = dict(proposed, address=REMOTE_VAULT.lower())
        other_event = dict(proposed, topics=["0x" + (b"\x01" * 32).hex()])
        assert lane.proposal_id_from_logs([other_contract, other_event, proposed]) == 42
        assert lane.proposal_id_from_receipt({"logs": [proposed]}) == 42
        with pytest.raises(ValueError, match="no BalanceProposed log"):
            lane.proposal_id_from_logs([other_contract])


def _ctx_answering(answers: dict[bytes, bytes], chain_id: int = 1) -> MagicMock:
    """A ctx whose eth_call answers by selector and reverts on anything else."""
    ctx = MagicMock()
    ctx.chain_id = chain_id
    ctx.default_block = "latest"

    def call(to, data, block=None):
        if data[:4] in answers:
            return answers[data[:4]]
        raise ContractLogicError("execution reverted")

    ctx.call.side_effect = call
    return ctx


class TestDiscovery:
    def test_detect_transport(self):
        ccip = _ctx_answering({selector("transportKind()"): encode(["uint8"], [2])})
        assert (
            detect_transport(ccip, EXECUTOR) == CrosschainTransportKind.CHAINLINK_CCIP
        )
        stargate = _ctx_answering(
            {selector("STARGATE_POOL()"): encode(["address"], [POOL])}
        )
        assert (
            detect_transport(stargate, EXECUTOR)
            == CrosschainTransportKind.STARGATE_LAYERZERO
        )
        zero_pool = _ctx_answering(
            {selector("STARGATE_POOL()"): encode(["address"], [ZERO_ADDRESS])}
        )
        with pytest.raises(ValueError, match="not a crosschain executor"):
            detect_transport(zero_pool, EXECUTOR)
        with pytest.raises(ValueError, match="not a crosschain executor"):
            detect_transport(_ctx_answering({}), EXECUTOR)

    def test_discover_lane_fuses_by_market_and_selector(self):
        market = 54
        codes = {
            FUSES.supply: selector(StargateCrosschainSupplyFuse._ENTER) + b"\x00",
            FUSES.command: selector(StargateCrosschainCommandFuse._ENTER),
            FUSES.claim: selector("enter((address,uint256))"),
            # an ERC-4626 supply fuse shares the claim selector but not the market
            REMOTE_VAULT: selector("enter((address,uint256))"),
        }
        markets = {addr: market for addr in astuple(FUSES)} | {REMOTE_VAULT: 100_001}
        ctx = MagicMock()
        ctx.default_block = "latest"
        ctx.call.side_effect = lambda to, data, block=None: encode(
            ["uint256"], [markets[to]]
        )
        ctx.web3.eth.get_code.side_effect = lambda addr, block_identifier=None: codes[
            addr
        ]
        found = discover_lane_fuses(
            ctx,
            [REMOTE_VAULT, *codes][:4],
            CrosschainTransportKind.STARGATE_LAYERZERO,
            market,
        )
        assert found == FUSES
        with pytest.raises(ValueError, match="no supply, command, claim fuse"):
            discover_lane_fuses(
                ctx, [REMOTE_VAULT], CrosschainTransportKind.STARGATE_LAYERZERO, market
            )
        codes[REMOTE_VAULT] = selector("enter((address,uint256))")
        markets[REMOTE_VAULT] = market
        with pytest.raises(ValueError, match="two claim fuses"):
            discover_lane_fuses(
                ctx, list(codes), CrosschainTransportKind.STARGATE_LAYERZERO, market
            )
        with pytest.raises(ValueError, match="unsupported transport"):
            discover_lane_fuses(ctx, [], CrosschainTransportKind.UNDEFINED, market)

    def test_open_lane_builds_each_transport(self):
        spoke_ctx = MagicMock()
        spoke_ctx.chain_id = SPOKE
        route_raw = encode(
            ["(uint64,address,address,uint96,uint96,uint256,bool)"], [ROUTE.as_tuple()]
        )
        ccip_ctx = _ctx_answering(
            {
                selector("transportKind()"): encode(["uint8"], [2]),
                selector("ccipRoute(uint256)"): route_raw,
            }
        )
        lane = open_lane(
            ccip_ctx, spoke_ctx, executor=EXECUTOR, market_id=54, fuses=FUSES
        )
        assert isinstance(lane, CcipLane)
        assert lane.route == ROUTE
        assert lane.spoke_chain_id == SPOKE
        stargate_ctx = _ctx_answering(
            {
                selector("STARGATE_POOL()"): encode(["address"], [POOL]),
                selector("MANAGER()"): encode(["address"], [VAULT]),
                selector("getFuses()"): encode(
                    ["address[]"], [[FUSES.supply, FUSES.command, FUSES.claim]]
                ),
                selector("MARKET_ID()"): encode(["uint256"], [54]),
            }
        )
        codes = {
            FUSES.supply: selector(StargateCrosschainSupplyFuse._ENTER),
            FUSES.command: selector(StargateCrosschainCommandFuse._ENTER),
            FUSES.claim: selector("enter((address,uint256))"),
        }
        stargate_ctx.web3.eth.get_code.side_effect = (
            lambda addr, block_identifier=None: codes[addr]
        )
        lane = open_lane(stargate_ctx, spoke_ctx, executor=EXECUTOR, market_id=54)
        assert isinstance(lane, StargateLane)
        assert lane.fuses == FUSES
        assert isinstance(lane, CrosschainLane)

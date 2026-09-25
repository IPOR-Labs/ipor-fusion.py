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
    LANES,
    CcipLane,
    CrosschainExecutorInfo,
    CrosschainLane,
    LaneFuses,
    LaneObservation,
    PlasmaVault,
    StargateLane,
    detect_transport,
    discover_deployment,
    discover_lane_fuses,
    open_lane,
    open_lanes,
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
    CrosschainSubstrateLib,
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


CCIP_EXECUTOR = Web3.to_checksum_address("0xb99ab307ce3df269b9f8763657fa128bbd38e2aa")
CCIP_FUSES = LaneFuses(
    supply=Web3.to_checksum_address("0x4444444444444444444444444444444444444444"),
    command=Web3.to_checksum_address("0x5555555555555555555555555555555555555555"),
    claim=FUSES.claim,
)
STARGATE_FACTORY = Web3.to_checksum_address(
    "0xb7894a9081d9060ced0b2e33714cdb075de67b9e"
)
CCIP_FACTORY = Web3.to_checksum_address("0xf360e8b00694c03fdc33dc2c54396fa41fabaadf")
PROPOSER = Web3.to_checksum_address("0x4F56543f62aB0186bA390e754EFC6c0870Dc5df5")
APPROVER = Web3.to_checksum_address("0xCeE5C4272E246A424AeDE992c987966736E0F63b")
ARBITRUM = ChainId(42161)
ARBITRUM_VAULT = Web3.to_checksum_address("0x174bfA12935AC416caA7d27397Bd4f3b15175980")
MARKET = 54


def _sel(call) -> bytes:
    return bytes(call.data[:4])


class TestLaneParts:
    def test_fuse_encoders_come_from_the_lane_class(self):
        lane = _stargate_lane()
        assert StargateLane.supply_fuse_cls is StargateCrosschainSupplyFuse
        assert isinstance(lane.supply_fuse, StargateCrosschainSupplyFuse)
        assert isinstance(lane.command_fuse, StargateCrosschainCommandFuse)
        assert isinstance(lane.claim_fuse, CrosschainClaimFuse)
        ccip = _ccip_lane()
        assert isinstance(ccip.supply_fuse, CcipCrosschainSupplyFuse)
        assert isinstance(ccip.command_fuse, CcipCrosschainCommandFuse)
        assert LANES == {
            CrosschainTransportKind.STARGATE_LAYERZERO: StargateLane,
            CrosschainTransportKind.CHAINLINK_CCIP: CcipLane,
        }

    @pytest.mark.parametrize("make", [_stargate_lane, _ccip_lane])
    def test_attestation_marks_the_accounted_balance(self, make):
        lane = make()
        observation = LaneObservation(
            tracked_idle=1,
            accounted_balance=999_700,
            state_version=6,
            command_config_epoch=2,
            tracked_position_set_hash=HASH,
        )
        assert lane.attestation(
            observation,
            remote_block=51_723_200,
            remote_timestamp=1_790_235_747,
            expiry=1_790_237_651,
        ) == BalanceObservation(
            SPOKE, 999_700, 6, 51_723_200, 1_790_235_747, 1_790_237_651, HASH
        )

    def test_staleness_max_per_transport(self):
        stargate = _stargate_lane()
        assert _sel(stargate.staleness_max()) == selector("STALENESS_MAX()")
        ccip = _ccip_lane()
        assert _sel(ccip.staleness_max()) == selector("BALANCE_STALENESS_MAX()")

    @pytest.mark.parametrize(
        ("settled", "pending", "last", "now", "margin", "due"),
        [
            (5, 0, 1_000, 4_601, 0, True),  # one second past the window
            (5, 0, 1_000, 4_600, 0, False),  # exactly at the window: still fresh
            (5, 0, 1_000, 4_560, 60, True),  # margin brings it forward
            (5, 0, 0, 10, 0, True),  # never attested
            (0, 0, 0, 10, 0, False),  # nothing settled, nothing to mark
            (5, 1, 1_000, 9_999, 0, False),  # a transfer in flight: wait
        ],
    )
    def test_needs_attestation(self, settled, pending, last, now, margin, due):
        probe = _stargate_lane()
        answers = {
            _sel(probe.settled_remote_balance()): encode(["uint256"], [settled]),
            _sel(probe.pending_transfer_count()): encode(["uint256"], [pending]),
            _sel(probe.last_approved_observed_at()): encode(["uint64"], [last]),
            _sel(probe.staleness_max()): encode(["uint256"], [3_600]),
        }
        lane = _stargate_lane(hub_ctx=_ctx_answering(answers))
        assert lane.needs_attestation(now=now, margin=margin) is due


def _deployment_ctx() -> MagicMock:
    """A hub whose crosschain market grants two executors (one per transport)
    and two remote vaults; the CCIP executor serves Base only."""
    vault = MagicMock()
    substrates = [
        CrosschainSubstrateLib.executor_substrate(EXECUTOR),
        CrosschainSubstrateLib.executor_substrate(CCIP_EXECUTOR),
        CrosschainSubstrateLib.remote_vault_substrate(SPOKE, REMOTE_VAULT),
        CrosschainSubstrateLib.remote_vault_substrate(ARBITRUM, ARBITRUM_VAULT),
    ]
    fuses = [
        FUSES.supply,
        FUSES.command,
        CCIP_FUSES.supply,
        CCIP_FUSES.command,
        FUSES.claim,
    ]
    probe_vault = PlasmaVault(vault, VAULT)
    probe_exec = StargateCrosschainExecutor(vault, EXECUTOR)
    route_raw = encode(
        ["(uint64,address,address,uint96,uint96,uint256,bool)"], [ROUTE.as_tuple()]
    )
    by_selector: dict[tuple[str, bytes], bytes] = {
        (VAULT, _sel(probe_vault.get_market_substrates(MARKET))): encode(
            ["bytes32[]"], [substrates]
        ),
        (VAULT, _sel(probe_vault.get_fuses())): encode(["address[]"], [fuses]),
        (EXECUTOR, selector("STARGATE_POOL()")): encode(["address"], [POOL]),
        (EXECUTOR, _sel(probe_exec.manager())): encode(["address"], [VAULT]),
        (EXECUTOR, _sel(probe_exec.factory())): encode(["address"], [STARGATE_FACTORY]),
        (EXECUTOR, _sel(probe_exec.balance_proposer())): encode(
            ["address"], [PROPOSER]
        ),
        (EXECUTOR, _sel(probe_exec.balance_approver())): encode(
            ["address"], [APPROVER]
        ),
        (CCIP_EXECUTOR, selector("transportKind()")): encode(["uint8"], [2]),
        (CCIP_EXECUTOR, _sel(probe_exec.manager())): encode(["address"], [VAULT]),
        (CCIP_EXECUTOR, _sel(probe_exec.factory())): encode(
            ["address"], [CCIP_FACTORY]
        ),
        (CCIP_EXECUTOR, _sel(probe_exec.balance_proposer())): encode(
            ["address"], [PROPOSER]
        ),
        (CCIP_EXECUTOR, _sel(probe_exec.balance_approver())): encode(
            ["address"], [APPROVER]
        ),
        (CCIP_EXECUTOR, selector("ccipRoute(uint256)")): route_raw,
    }
    by_selector |= {
        (fuse, selector("MARKET_ID()")): encode(["uint256"], [MARKET]) for fuse in fuses
    }
    by_data: dict[tuple[str, bytes], bytes] = {
        (EXECUTOR, bytes(probe_exec.has_dispatcher(SPOKE).data)): encode(
            ["bool"], [True]
        ),
        (EXECUTOR, bytes(probe_exec.has_dispatcher(ARBITRUM).data)): encode(
            ["bool"], [True]
        ),
        (CCIP_EXECUTOR, bytes(probe_exec.has_dispatcher(SPOKE).data)): encode(
            ["bool"], [True]
        ),
        (CCIP_EXECUTOR, bytes(probe_exec.has_dispatcher(ARBITRUM).data)): encode(
            ["bool"], [False]
        ),
    }
    codes = {
        FUSES.supply: selector(StargateCrosschainSupplyFuse._ENTER),
        FUSES.command: selector(StargateCrosschainCommandFuse._ENTER),
        CCIP_FUSES.supply: selector(CcipCrosschainSupplyFuse._ENTER),
        CCIP_FUSES.command: selector(CcipCrosschainCommandFuse._ENTER),
        FUSES.claim: selector("enter((address,uint256))"),
    }
    ctx = MagicMock()
    ctx.chain_id = 1
    ctx.default_block = "latest"

    def call(to, data, block=None):
        data = bytes(data)
        if (to, data) in by_data:
            return by_data[(to, data)]
        if (to, data[:4]) in by_selector:
            return by_selector[(to, data[:4])]
        raise ContractLogicError("execution reverted")

    ctx.call.side_effect = call
    ctx.web3.eth.get_code.side_effect = lambda addr, block_identifier=None: codes[addr]
    return ctx


class TestDeploymentDiscovery:
    def test_discover_deployment_reads_the_market_grants(self):
        deployment = discover_deployment(_deployment_ctx(), VAULT, MARKET)
        assert deployment.vault == VAULT
        assert deployment.spoke_chain_ids == (SPOKE, ARBITRUM)
        assert deployment.remote_vaults == {
            SPOKE: (REMOTE_VAULT,),
            ARBITRUM: (ARBITRUM_VAULT,),
        }
        stargate = deployment.executor(CrosschainTransportKind.STARGATE_LAYERZERO)
        assert stargate == CrosschainExecutorInfo(
            address=EXECUTOR,
            transport_kind=CrosschainTransportKind.STARGATE_LAYERZERO,
            factory=STARGATE_FACTORY,
            fuses=FUSES,
            balance_proposer=PROPOSER,
            balance_approver=APPROVER,
            spoke_chain_ids=(SPOKE, ARBITRUM),
        )
        ccip = deployment.executor(CrosschainTransportKind.CHAINLINK_CCIP)
        assert ccip.fuses == CCIP_FUSES
        assert ccip.factory == CCIP_FACTORY
        assert ccip.spoke_chain_ids == (SPOKE,)
        with pytest.raises(ValueError, match="0 UNDEFINED executors"):
            deployment.executor(CrosschainTransportKind.UNDEFINED)

    def test_discover_deployment_rejects_a_foreign_executor(self):
        ctx = _deployment_ctx()
        inner = ctx.call.side_effect
        manager = _sel(StargateCrosschainExecutor(MagicMock(), EXECUTOR).manager())

        def call(to, data, block=None):
            if to == CCIP_EXECUTOR and bytes(data)[:4] == manager:
                return encode(["address"], [REMOTE_VAULT])
            return inner(to, data, block)

        ctx.call.side_effect = call
        with pytest.raises(ValueError, match="managed by"):
            discover_deployment(ctx, VAULT, MARKET)

    def test_open_lanes_one_per_executor_and_served_spoke(self):
        hub_ctx = _deployment_ctx()
        deployment = discover_deployment(hub_ctx, VAULT, MARKET)
        base_ctx, arbitrum_ctx = MagicMock(), MagicMock()
        base_ctx.chain_id, arbitrum_ctx.chain_id = SPOKE, ARBITRUM
        lanes = open_lanes(hub_ctx, {SPOKE: base_ctx}, deployment=deployment)
        assert [(type(lane), lane.spoke_chain_id) for lane in lanes] == [
            (StargateLane, SPOKE),
            (CcipLane, SPOKE),
        ]
        lanes = open_lanes(
            hub_ctx, {SPOKE: base_ctx, ARBITRUM: arbitrum_ctx}, deployment=deployment
        )
        assert [(type(lane), lane.spoke_chain_id) for lane in lanes] == [
            (StargateLane, SPOKE),
            (StargateLane, ARBITRUM),
            (CcipLane, SPOKE),
        ]
        assert [lane.executor_address for lane in lanes] == [
            EXECUTOR,
            EXECUTOR,
            CCIP_EXECUTOR,
        ]
        ccip = next(lane for lane in lanes if isinstance(lane, CcipLane))
        assert ccip.route == ROUTE and ccip.fuses == CCIP_FUSES

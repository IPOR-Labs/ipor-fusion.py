"""Offline tests for the crosschain contract wrappers: every method encodes the
right selector and decodes its return through a mocked context."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.crosschain import (
    ActiveCommand,
    BalanceObservation,
    CcipCrosschainDispatcher,
    CcipCrosschainExecutor,
    CcipCrosschainFactory,
    CcipObservation,
    CcipRouteConfig,
    CommandStatus,
    CrosschainTransportKind,
    DeploymentStatus,
    ExecutorInitParams,
    Observation,
    SafetyConfig,
    StargateCrosschainDispatcher,
    StargateCrosschainExecutor,
    StargateCrosschainFactory,
)

ADDR = Web3.to_checksum_address("0x1d5c9d44f8d556ec7f557ae992401cc770937e6e")
VAULT = Web3.to_checksum_address("0x0Aa75BfD30Ae2d4061FF8261453b933Ab5959991")
POOL = Web3.to_checksum_address("0xc026395860Db2d07ee33e05fE50ed7bD583189C7")
ASSET_ID = b"\xd6" + b"\x00" * 31


def _wrapper(cls, return_types: list[str] | None = None, values: tuple | None = None):
    ctx = MagicMock()
    if return_types is not None:
        ctx.call.return_value = encode(return_types, list(values or ()))
    return cls(ctx, ADDR)


def _selector(call, signature: str) -> None:
    assert call.to == ADDR
    assert call.data[:4] == function_signature_to_4byte_selector(signature)


# Every simple view: (wrapper, method, args, signature, return types, raw values, decoded)
_VIEWS = [
    (
        StargateCrosschainExecutor,
        "manager",
        (),
        "MANAGER()",
        ["address"],
        (VAULT.lower(),),
        VAULT,
    ),
    (
        StargateCrosschainExecutor,
        "asset",
        (),
        "ASSET()",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        StargateCrosschainExecutor,
        "asset_id",
        (),
        "ASSET_ID()",
        ["bytes32"],
        (ASSET_ID,),
        ASSET_ID,
    ),
    (
        StargateCrosschainExecutor,
        "factory",
        (),
        "FACTORY()",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        StargateCrosschainExecutor,
        "admin",
        (),
        "ADMIN()",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        StargateCrosschainExecutor,
        "balance_proposer",
        (),
        "BALANCE_PROPOSER()",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        StargateCrosschainExecutor,
        "balance_approver",
        (),
        "BALANCE_APPROVER()",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        StargateCrosschainExecutor,
        "big_change_bps",
        (),
        "BIG_CHANGE_BPS()",
        ["uint256"],
        (1000,),
        1000,
    ),
    (
        StargateCrosschainExecutor,
        "min_update_interval",
        (),
        "MIN_UPDATE_INTERVAL()",
        ["uint256"],
        (60,),
        60,
    ),
    (
        StargateCrosschainExecutor,
        "transfer_staleness_max",
        (),
        "TRANSFER_STALENESS_MAX()",
        ["uint256"],
        (3600,),
        3600,
    ),
    (
        StargateCrosschainExecutor,
        "local_decimals",
        (),
        "LOCAL_DECIMALS()",
        ["uint8"],
        (6,),
        6,
    ),
    (
        StargateCrosschainExecutor,
        "shared_decimals",
        (),
        "SHARED_DECIMALS()",
        ["uint8"],
        (6,),
        6,
    ),
    (
        StargateCrosschainExecutor,
        "get_balance",
        (),
        "getBalance()",
        ["uint256"],
        (5,),
        5,
    ),
    (
        StargateCrosschainExecutor,
        "get_balance_by_chain",
        (8453,),
        "getBalanceByChain(uint256)",
        ["uint256"],
        (5,),
        5,
    ),
    (
        StargateCrosschainExecutor,
        "idle_ledger",
        (),
        "idleLedger()",
        ["uint256"],
        (1,),
        1,
    ),
    (
        StargateCrosschainExecutor,
        "settled_remote_balance",
        (8453,),
        "settledRemoteBalance(uint256)",
        ["uint256"],
        (2,),
        2,
    ),
    (
        StargateCrosschainExecutor,
        "outbound_in_flight",
        (8453,),
        "outboundInFlight(uint256)",
        ["uint256"],
        (3,),
        3,
    ),
    (
        StargateCrosschainExecutor,
        "has_dispatcher",
        (8453,),
        "hasDispatcher(uint256)",
        ["bool"],
        (True,),
        True,
    ),
    (
        StargateCrosschainExecutor,
        "is_dispatcher_ready",
        (8453,),
        "isDispatcherReady(uint256)",
        ["bool"],
        (True,),
        True,
    ),
    (
        StargateCrosschainExecutor,
        "chain_blocked",
        (8453,),
        "chainBlocked(uint256)",
        ["bool"],
        (False,),
        False,
    ),
    (
        StargateCrosschainExecutor,
        "last_approved_observed_at",
        (8453,),
        "lastApprovedObservedAt(uint256)",
        ["uint64"],
        (7,),
        7,
    ),
    (
        StargateCrosschainExecutor,
        "stargate_pool",
        (),
        "STARGATE_POOL()",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        StargateCrosschainExecutor,
        "local_chain_id",
        (),
        "LOCAL_CHAIN_ID()",
        ["uint256"],
        (1,),
        1,
    ),
    (
        StargateCrosschainExecutor,
        "staleness_max",
        (),
        "STALENESS_MAX()",
        ["uint256"],
        (3600,),
        3600,
    ),
    (
        StargateCrosschainExecutor,
        "deployment_ttl",
        (),
        "DEPLOYMENT_TTL()",
        ["uint256"],
        (86400,),
        86400,
    ),
    (
        StargateCrosschainExecutor,
        "registered_chain_count",
        (),
        "registeredChainCount()",
        ["uint256"],
        (2,),
        2,
    ),
    (
        StargateCrosschainExecutor,
        "proposal_count",
        (),
        "proposalCount()",
        ["uint256"],
        (9,),
        9,
    ),
    (
        StargateCrosschainExecutor,
        "eid_of",
        (8453,),
        "eidOf(uint256)",
        ["uint32"],
        (30184,),
        30184,
    ),
    (
        StargateCrosschainExecutor,
        "chain_id_of",
        (30184,),
        "chainIdOf(uint32)",
        ["uint256"],
        (8453,),
        8453,
    ),
    (
        StargateCrosschainExecutor,
        "return_in_flight",
        (8453,),
        "returnInFlight(uint256)",
        ["uint256"],
        (4,),
        4,
    ),
    (
        StargateCrosschainExecutor,
        "pending_arrival_reserve",
        (),
        "pendingArrivalReserve()",
        ["uint256"],
        (4,),
        4,
    ),
    (
        StargateCrosschainExecutor,
        "in_flight_since",
        (8453,),
        "inFlightSince(uint256)",
        ["uint64"],
        (4,),
        4,
    ),
    (
        StargateCrosschainExecutor,
        "acknowledged_remote_state_version",
        (8453,),
        "acknowledgedRemoteStateVersion(uint256)",
        ["uint64"],
        (5,),
        5,
    ),
    (
        StargateCrosschainExecutor,
        "accounting_epoch",
        (8453,),
        "accountingEpoch(uint256)",
        ["uint64"],
        (2,),
        2,
    ),
    (
        StargateCrosschainExecutor,
        "next_sequence_to_send",
        (8453,),
        "nextSequenceToSend(uint256)",
        ["uint64"],
        (3,),
        3,
    ),
    (
        StargateCrosschainExecutor,
        "chain_config_epoch",
        (8453,),
        "chainConfigEpoch(uint256)",
        ["uint64"],
        (1,),
        1,
    ),
    (
        StargateCrosschainExecutor,
        "pending_transfer_count",
        (8453,),
        "pendingTransferCount(uint256)",
        ["uint256"],
        (0,),
        0,
    ),
    (
        StargateCrosschainExecutor,
        "pending_request_by_chain",
        (8453,),
        "pendingRequestByChain(uint256)",
        ["uint256"],
        (0,),
        0,
    ),
    (
        StargateCrosschainExecutor,
        "active_proposal_id",
        (8453,),
        "activeProposalId(uint256)",
        ["uint256"],
        (0,),
        0,
    ),
    (
        StargateCrosschainExecutor,
        "last_approval_at",
        (8453,),
        "lastApprovalAt(uint256)",
        ["uint64"],
        (1,),
        1,
    ),
    (
        StargateCrosschainExecutor,
        "deployment_status",
        (1,),
        "deploymentStatus(uint256)",
        ["uint8"],
        (2,),
        DeploymentStatus.DEPLOYED,
    ),
    (
        CcipCrosschainExecutor,
        "transport_kind",
        (),
        "transportKind()",
        ["uint8"],
        (2,),
        CrosschainTransportKind.CHAINLINK_CCIP,
    ),
    (
        CcipCrosschainExecutor,
        "executor_interface_version",
        (),
        "executorInterfaceVersion()",
        ["uint32"],
        (7,),
        7,
    ),
    (
        CcipCrosschainExecutor,
        "ccip_router",
        (),
        "CCIP_ROUTER()",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        CcipCrosschainExecutor,
        "balance_staleness_max",
        (),
        "BALANCE_STALENESS_MAX()",
        ["uint256"],
        (3600,),
        3600,
    ),
    (
        CcipCrosschainExecutor,
        "chain_id_of_selector",
        (5,),
        "chainIdOfSelector(uint64)",
        ["uint256"],
        (8453,),
        8453,
    ),
    (
        CcipCrosschainExecutor,
        "last_remote_state_version",
        (8453,),
        "lastRemoteStateVersion(uint256)",
        ["uint64"],
        (4,),
        4,
    ),
    (
        CcipCrosschainExecutor,
        "active_return",
        (8453,),
        "activeReturn(uint256)",
        ["bytes32"],
        (ASSET_ID,),
        ASSET_ID,
    ),
    (
        CcipCrosschainExecutor,
        "active_command",
        (8453,),
        "activeCommand(uint256)",
        ["bytes32"],
        (ASSET_ID,),
        ASSET_ID,
    ),
    (
        CcipCrosschainExecutor,
        "next_command_sequence",
        (8453,),
        "nextCommandSequence(uint256)",
        ["uint64"],
        (2,),
        2,
    ),
    (
        CcipCrosschainExecutor,
        "command_config_epoch",
        (8453,),
        "commandConfigEpoch(uint256)",
        ["uint64"],
        (1,),
        1,
    ),
    (
        CcipCrosschainExecutor,
        "command_status",
        (ASSET_ID,),
        "commandStatus(bytes32)",
        ["uint8"],
        (3,),
        CommandStatus.FAILED,
    ),
    (
        CcipCrosschainExecutor,
        "oldest_pending_at",
        (8453,),
        "oldestPendingAt(uint256)",
        ["uint64"],
        (0,),
        0,
    ),
    (
        CcipCrosschainExecutor,
        "pending_transfer_count",
        (8453,),
        "pendingTransferCount(uint256)",
        ["uint8"],
        (1,),
        1,
    ),
    (
        CcipCrosschainExecutor,
        "processed_ccip_message",
        (ASSET_ID,),
        "processedCcipMessage(bytes32)",
        ["bool"],
        (True,),
        True,
    ),
    (
        StargateCrosschainDispatcher,
        "asset",
        (),
        "ASSET()",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        StargateCrosschainDispatcher,
        "asset_id",
        (),
        "ASSET_ID()",
        ["bytes32"],
        (ASSET_ID,),
        ASSET_ID,
    ),
    (
        StargateCrosschainDispatcher,
        "factory",
        (),
        "FACTORY()",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        StargateCrosschainDispatcher,
        "executor_chain_id",
        (),
        "EXECUTOR_CHAIN_ID()",
        ["uint256"],
        (1,),
        1,
    ),
    (
        StargateCrosschainDispatcher,
        "local_decimals",
        (),
        "LOCAL_DECIMALS()",
        ["uint8"],
        (6,),
        6,
    ),
    (
        StargateCrosschainDispatcher,
        "shared_decimals",
        (),
        "SHARED_DECIMALS()",
        ["uint8"],
        (6,),
        6,
    ),
    (
        StargateCrosschainDispatcher,
        "state_version",
        (),
        "stateVersion()",
        ["uint64"],
        (5,),
        5,
    ),
    (
        StargateCrosschainDispatcher,
        "tracked_idle",
        (),
        "trackedIdle()",
        ["uint256"],
        (0,),
        0,
    ),
    (
        StargateCrosschainDispatcher,
        "next_command_sequence",
        (),
        "nextCommandSequence()",
        ["uint64"],
        (3,),
        3,
    ),
    (
        StargateCrosschainDispatcher,
        "command_config_epoch",
        (),
        "commandConfigEpoch()",
        ["uint64"],
        (1,),
        1,
    ),
    (
        StargateCrosschainDispatcher,
        "is_allowed_vault",
        (VAULT,),
        "isAllowedVault(address)",
        ["bool"],
        (True,),
        True,
    ),
    (
        StargateCrosschainDispatcher,
        "is_tracked_vault",
        (VAULT,),
        "isTrackedVault(address)",
        ["bool"],
        (False,),
        False,
    ),
    (
        StargateCrosschainDispatcher,
        "pending_request_shares",
        (VAULT,),
        "pendingRequestShares(address)",
        ["uint256"],
        (0,),
        0,
    ),
    (
        StargateCrosschainDispatcher,
        "command_status",
        (ASSET_ID,),
        "commandStatus(bytes32)",
        ["uint8"],
        (2,),
        CommandStatus.SUCCEEDED,
    ),
    (
        StargateCrosschainDispatcher,
        "stargate_pool",
        (),
        "STARGATE_POOL()",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        StargateCrosschainDispatcher,
        "executor_eid",
        (),
        "EXECUTOR_EID()",
        ["uint32"],
        (30101,),
        30101,
    ),
    (
        StargateCrosschainDispatcher,
        "get_allowed_vaults",
        (),
        "getAllowedVaults()",
        ["address[]"],
        ([VAULT.lower()],),
        [VAULT],
    ),
    (
        StargateCrosschainDispatcher,
        "get_tracked_vaults",
        (),
        "getTrackedVaults()",
        ["address[]"],
        ([],),
        [],
    ),
    (
        StargateCrosschainDispatcher,
        "recall_consumed",
        (ASSET_ID,),
        "recallConsumed(bytes32)",
        ["bool"],
        (True,),
        True,
    ),
    (
        StargateCrosschainDispatcher,
        "transfer_settled",
        (ASSET_ID,),
        "transferSettled(bytes32)",
        ["bool"],
        (True,),
        True,
    ),
    (
        CcipCrosschainDispatcher,
        "ccip_router",
        (),
        "CCIP_ROUTER()",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        CcipCrosschainDispatcher,
        "accounted_balance",
        (),
        "accountedBalance()",
        ["uint256"],
        (6,),
        6,
    ),
    (
        CcipCrosschainDispatcher,
        "tracked_vaults_length",
        (),
        "trackedVaultsLength()",
        ["uint256"],
        (1,),
        1,
    ),
    (
        CcipCrosschainDispatcher,
        "tracked_position_set_hash",
        (),
        "trackedPositionSetHash()",
        ["bytes32"],
        (ASSET_ID,),
        ASSET_ID,
    ),
    (
        CcipCrosschainDispatcher,
        "response_queue_head",
        (),
        "responseQueueHead()",
        ["uint64"],
        (0,),
        0,
    ),
    (
        CcipCrosschainDispatcher,
        "response_queue_tail",
        (),
        "responseQueueTail()",
        ["uint64"],
        (5,),
        5,
    ),
    (
        CcipCrosschainDispatcher,
        "queued_token_amount",
        (),
        "queuedTokenAmount()",
        ["uint256"],
        (0,),
        0,
    ),
    (
        CcipCrosschainDispatcher,
        "processed_operation",
        (ASSET_ID,),
        "processedOperation(bytes32)",
        ["bool"],
        (True,),
        True,
    ),
    (
        CcipCrosschainDispatcher,
        "processed_ccip_message",
        (ASSET_ID,),
        "processedCcipMessage(bytes32)",
        ["bool"],
        (False,),
        False,
    ),
    (
        StargateCrosschainFactory,
        "compute_executor_address",
        (VAULT, ASSET_ID),
        "computeExecutorAddress(address,bytes32)",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        StargateCrosschainFactory,
        "executors_of",
        (VAULT,),
        "executorsOf(address)",
        ["address[]"],
        ([POOL.lower()],),
        [POOL],
    ),
    (
        StargateCrosschainFactory,
        "is_executor",
        (POOL,),
        "isExecutor(address)",
        ["bool"],
        (True,),
        True,
    ),
    (
        StargateCrosschainFactory,
        "salt_of",
        (POOL,),
        "saltOf(address)",
        ["bytes32"],
        (ASSET_ID,),
        ASSET_ID,
    ),
    (
        StargateCrosschainFactory,
        "creation_restricted",
        (),
        "creationRestricted()",
        ["bool"],
        (True,),
        True,
    ),
    (
        StargateCrosschainFactory,
        "is_allowed_creator",
        (VAULT,),
        "isAllowedCreator(address)",
        ["bool"],
        (False,),
        False,
    ),
    (
        StargateCrosschainFactory,
        "asset_frozen",
        (ASSET_ID,),
        "assetFrozen(bytes32)",
        ["bool"],
        (True,),
        True,
    ),
    (
        StargateCrosschainFactory,
        "creation_codes_configured",
        (),
        "creationCodesConfigured()",
        ["bool"],
        (True,),
        True,
    ),
    (
        StargateCrosschainFactory,
        "eid_of",
        (8453,),
        "eidOf(uint256)",
        ["uint32"],
        (30184,),
        30184,
    ),
    (
        StargateCrosschainFactory,
        "chain_id_of",
        (30184,),
        "chainIdOf(uint32)",
        ["uint256"],
        (8453,),
        8453,
    ),
    (
        StargateCrosschainFactory,
        "path_frozen",
        (8453,),
        "pathFrozen(uint256)",
        ["bool"],
        (True,),
        True,
    ),
    (
        StargateCrosschainFactory,
        "max_native_fee",
        (8453,),
        "maxNativeFee(uint256)",
        ["uint256"],
        (10**16,),
        10**16,
    ),
    (
        StargateCrosschainFactory,
        "config_delay",
        (),
        "CONFIG_DELAY()",
        ["uint256"],
        (300,),
        300,
    ),
    (
        StargateCrosschainFactory,
        "request_count",
        (),
        "requestCount()",
        ["uint256"],
        (2,),
        2,
    ),
    (
        CcipCrosschainFactory,
        "factory_interface_version",
        (),
        "factoryInterfaceVersion()",
        ["uint32"],
        (1,),
        1,
    ),
    (
        CcipCrosschainFactory,
        "ccip_router",
        (),
        "CCIP_ROUTER()",
        ["address"],
        (POOL.lower(),),
        POOL,
    ),
    (
        CcipCrosschainFactory,
        "chain_id_of_selector",
        (5,),
        "chainIdOfSelector(uint64)",
        ["uint256"],
        (8453,),
        8453,
    ),
]


@pytest.mark.parametrize(
    ("cls", "method", "args", "signature", "types", "raw", "expected"),
    _VIEWS,
    ids=[f"{cls.__name__}.{method}" for cls, method, *_ in _VIEWS],
)
def test_views_encode_selector_and_decode_return(
    cls, method, args, signature, types, raw, expected
):
    wrapper = _wrapper(cls, types, raw)
    call = getattr(wrapper, method)(*args)
    _selector(call, signature)
    assert call.call() == expected


def test_struct_views():
    executor = _wrapper(
        StargateCrosschainExecutor,
        ["bool", "bool", "bool", "uint8", "bytes32", "uint64", "uint64"],
        (True, False, False, 1, ASSET_ID, 4, 0),
    )
    call = executor.active_command(8453)
    _selector(call, "activeCommand(uint256)")
    assert call.call() == ActiveCommand(
        True, False, False, CommandStatus.PENDING, ASSET_ID, 4, 0
    )

    dispatcher = _wrapper(
        StargateCrosschainDispatcher,
        ["(uint256,address[],uint256[],uint64,uint64,bytes32)"],
        ((999_414, [VAULT.lower()], [999_414], 5, 1, ASSET_ID),),
    )
    call = dispatcher.observation()
    _selector(call, "observation()")
    assert call.call() == Observation(999_414, [VAULT], [999_414], 5, 1, ASSET_ID)

    ccip = _wrapper(
        CcipCrosschainDispatcher,
        ["(uint256,uint256,uint256,uint64,uint64,bytes32)"],
        ((3, 1, 2, 4, 1, ASSET_ID),),
    )
    assert ccip.observation().call() == CcipObservation(3, 1, 2, 4, 1, ASSET_ID)

    route_raw = (
        15971525489660198786,
        ADDR.lower(),
        POOL.lower(),
        1_200_000,
        1_000_000,
        10**16,
        True,
    )
    for cls in (
        CcipCrosschainExecutor,
        CcipCrosschainDispatcher,
        CcipCrosschainFactory,
    ):
        wrapper = _wrapper(
            cls, ["(uint64,address,address,uint96,uint96,uint256,bool)"], (route_raw,)
        )
        call = wrapper.ccip_route(8453)
        _selector(call, "ccipRoute(uint256)")
        assert call.call() == CcipRouteConfig(
            15971525489660198786, ADDR, POOL, 1_200_000, 1_000_000, 10**16, True
        )

    stargate_factory = _wrapper(
        StargateCrosschainFactory,
        ["address", "address", "bool"],
        (POOL.lower(), ADDR.lower(), True),
    )
    assert stargate_factory.asset_config(ASSET_ID).call() == (POOL, ADDR, True)
    ccip_factory = _wrapper(
        CcipCrosschainFactory, ["address", "uint8", "bool"], (POOL.lower(), 6, True)
    )
    assert ccip_factory.asset_config(ASSET_ID).call() == (POOL, 6, True)


def test_writes_encode_arguments():
    executor = _wrapper(StargateCrosschainExecutor)
    observation = BalanceObservation(
        8453, 999_414, 6, 51_700_000, 1_790_000_000, 1_790_001_800, ASSET_ID
    )
    call = executor.propose_balance(observation)
    _selector(
        call, "proposeBalance((uint256,uint256,uint64,uint64,uint64,uint64,bytes32))"
    )
    (decoded,) = decode(
        ["(uint256,uint256,uint64,uint64,uint64,uint64,bytes32)"], call.data[4:]
    )
    assert decoded == observation.as_tuple()
    _selector(executor.approve_balance(3), "approveBalance(uint256)")
    _selector(executor.reject_and_block_proposal(3), "rejectAndBlockProposal(uint256)")
    _selector(executor.claim_asset(5), "claimAsset(uint256)")
    _selector(
        executor.cancel_stale_return_request(8453), "cancelStaleReturnRequest(uint256)"
    )

    ccip = _wrapper(CcipCrosschainExecutor)
    call = ccip.propose_balance(
        8453, 100_000, 4, 51_700_000, 1_790_000_000, 1_790_001_800, ASSET_ID
    )
    _selector(
        call, "proposeBalance(uint256,uint256,uint64,uint64,uint64,uint64,bytes32)"
    )
    assert decode(
        ["uint256", "uint256", "uint64", "uint64", "uint64", "uint64", "bytes32"],
        call.data[4:],
    ) == (8453, 100_000, 4, 51_700_000, 1_790_000_000, 1_790_001_800, ASSET_ID)
    _selector(ccip.reject_balance_and_block(1), "rejectBalanceAndBlock(uint256)")
    _selector(
        ccip.cancel_stale_pending_return(8453), "cancelStalePendingReturn(uint256)"
    )
    _selector(_wrapper(CcipCrosschainDispatcher).flush_response(), "flushResponse()")

    factory = _wrapper(StargateCrosschainFactory, ["address"], (POOL.lower(),))
    params = ExecutorInitParams(
        ASSET_ID, VAULT, ADDR, POOL, 86400, ADDR, POOL, 3600, 1000, 60, 86400, 3600
    )
    call = factory.create_executor(b"\x01" * 32, params)
    _selector(
        call,
        "createExecutor(bytes32,(bytes32,address,address,address,uint256,address,address,uint256,uint256,uint256,uint256,uint256))",
    )
    assert call.call() == POOL
    _selector(factory.cancel_pending_deployment(1), "cancelPendingDeployment(uint256)")

    ccip_factory = _wrapper(CcipCrosschainFactory, ["address"], (POOL.lower(),))
    safety = SafetyConfig(ADDR, POOL, ADDR, POOL, 86400, 3600, 1000, 60, 3600)
    call = ccip_factory.create_executor(b"\x01" * 32, ASSET_ID, VAULT, safety)
    _selector(
        call,
        "createExecutor(bytes32,bytes32,address,(address,address,address,address,uint256,uint256,uint256,uint256,uint256))",
    )
    salt, asset_id, manager, decoded_safety = decode(
        [
            "bytes32",
            "bytes32",
            "address",
            "(address,address,address,address,uint256,uint256,uint256,uint256,uint256)",
        ],
        call.data[4:],
    )
    assert (salt, asset_id, manager) == (b"\x01" * 32, ASSET_ID, VAULT.lower())
    assert decoded_safety[4:] == (86400, 3600, 1000, 60, 3600)
    _selector(
        ccip_factory.sync_ccip_route_policy(ADDR, 8453),
        "syncCcipRoutePolicy(address,uint256)",
    )
    _selector(
        ccip_factory.cancel_expired_deployment(ADDR, 8453),
        "cancelExpiredDeployment(address,uint256)",
    )

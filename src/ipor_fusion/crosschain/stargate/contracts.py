"""Wrappers of the Stargate V2 / LayerZero V2 crosschain contracts
(``contracts/crosschain/stargate/**``)."""

from __future__ import annotations

from dataclasses import dataclass

from eth_typing import ChecksumAddress

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.crosschain.contracts import (
    BalanceObservation,
    CrosschainDispatcher,
    CrosschainExecutor,
    CrosschainFactory,
    _address,
    _address_list,
)
from ipor_fusion.crosschain.messages import CommandStatus, DeploymentStatus
from ipor_fusion.types import Amount, ChainId


@dataclass(frozen=True, slots=True)
class ActiveCommand:
    """``StargateExecutorState.ActiveCommand`` as returned by ``activeCommand``."""

    active: bool
    is_config_update: bool
    awaiting_cancel: bool
    status: CommandStatus
    command_id: bytes
    sequence: int
    new_config_epoch: int


@dataclass(frozen=True, slots=True)
class Observation:
    """``ICrosschainDispatcher.Observation``: the canonical dispatcher snapshot
    attestations are derived from (amounts in local asset decimals)."""

    tracked_idle: Amount
    tracked_vaults: list[ChecksumAddress]
    vault_asset_values: list[Amount]
    state_version: int
    command_config_epoch: int
    tracked_position_set_hash: bytes


@dataclass(frozen=True, slots=True)
class ExecutorInitParams:
    """``ICrosschainFactory.ExecutorInitParams``: the immutable configuration
    ``StargateCrosschainFactory.createExecutor`` bakes into an executor."""

    asset_id: bytes
    manager: ChecksumAddress
    admin: ChecksumAddress
    rescue_guardian: ChecksumAddress
    rescue_delay: int
    balance_proposer: ChecksumAddress
    balance_approver: ChecksumAddress
    staleness_max: int
    big_change_bps: int
    min_update_interval: int
    deployment_ttl: int
    transfer_staleness_max: int

    def as_tuple(self) -> tuple:
        return (
            self.asset_id,
            self.manager,
            self.admin,
            self.rescue_guardian,
            self.rescue_delay,
            self.balance_proposer,
            self.balance_approver,
            self.staleness_max,
            self.big_change_bps,
            self.min_update_interval,
            self.deployment_ttl,
            self.transfer_staleness_max,
        )


_EXECUTOR_INIT_PARAMS_TUPLE = (
    "(bytes32,address,address,address,uint256,address,address,uint256,uint256,"
    "uint256,uint256,uint256)"
)

_BALANCE_OBSERVATION_TUPLE = "(uint256,uint256,uint64,uint64,uint64,uint64,bytes32)"


class StargateCrosschainExecutor(CrosschainExecutor):
    """``StargateCrosschainExecutor``: the Stargate V2 / LayerZero V2 executor."""

    def stargate_pool(self) -> Call[ChecksumAddress]:
        return self._view("STARGATE_POOL()", output_types=["address"], decoder=_address)

    def local_chain_id(self) -> Call[ChainId]:
        return self._view("LOCAL_CHAIN_ID()", output_types=["uint256"], decoder=ChainId)

    def staleness_max(self) -> Call[int]:
        return self._view("STALENESS_MAX()", output_types=["uint256"])

    def deployment_ttl(self) -> Call[int]:
        return self._view("DEPLOYMENT_TTL()", output_types=["uint256"])

    def registered_chain_count(self) -> Call[int]:
        return self._view("registeredChainCount()", output_types=["uint256"])

    def proposal_count(self) -> Call[int]:
        return self._view("proposalCount()", output_types=["uint256"])

    def eid_of(self, chain_id: ChainId) -> Call[int]:
        return self._view("eidOf(uint256)", chain_id, output_types=["uint32"])

    def chain_id_of(self, eid: int) -> Call[ChainId]:
        return self._view(
            "chainIdOf(uint32)", eid, output_types=["uint256"], decoder=ChainId
        )

    def return_in_flight(self, chain_id: ChainId) -> Call[Amount]:
        return self._view(
            "returnInFlight(uint256)",
            chain_id,
            output_types=["uint256"],
            decoder=Amount,
        )

    def pending_arrival_reserve(self) -> Call[Amount]:
        return self._view(
            "pendingArrivalReserve()", output_types=["uint256"], decoder=Amount
        )

    def in_flight_since(self, chain_id: ChainId) -> Call[int]:
        return self._view("inFlightSince(uint256)", chain_id, output_types=["uint64"])

    def acknowledged_remote_state_version(self, chain_id: ChainId) -> Call[int]:
        return self._view(
            "acknowledgedRemoteStateVersion(uint256)",
            chain_id,
            output_types=["uint64"],
        )

    def accounting_epoch(self, chain_id: ChainId) -> Call[int]:
        return self._view("accountingEpoch(uint256)", chain_id, output_types=["uint64"])

    def next_sequence_to_send(self, chain_id: ChainId) -> Call[int]:
        return self._view(
            "nextSequenceToSend(uint256)", chain_id, output_types=["uint64"]
        )

    def chain_config_epoch(self, chain_id: ChainId) -> Call[int]:
        return self._view(
            "chainConfigEpoch(uint256)", chain_id, output_types=["uint64"]
        )

    def pending_transfer_count(self, chain_id: ChainId) -> Call[int]:
        return self._view(
            "pendingTransferCount(uint256)", chain_id, output_types=["uint256"]
        )

    def pending_request_by_chain(self, chain_id: ChainId) -> Call[int]:
        return self._view(
            "pendingRequestByChain(uint256)", chain_id, output_types=["uint256"]
        )

    def active_command(self, chain_id: ChainId) -> Call[ActiveCommand]:
        return self._view(
            "activeCommand(uint256)",
            chain_id,
            output_types=[
                "bool",
                "bool",
                "bool",
                "uint8",
                "bytes32",
                "uint64",
                "uint64",
            ],
            decoder=_active_command_decoder,
        )

    def active_proposal_id(self, chain_id: ChainId) -> Call[int]:
        return self._view(
            "activeProposalId(uint256)", chain_id, output_types=["uint256"]
        )

    def last_approval_at(self, chain_id: ChainId) -> Call[int]:
        return self._view("lastApprovalAt(uint256)", chain_id, output_types=["uint64"])

    def deployment_status(self, request_id: int) -> Call[DeploymentStatus]:
        return self._view(
            "deploymentStatus(uint256)",
            request_id,
            output_types=["uint8"],
            decoder=DeploymentStatus,
        )

    def propose_balance(self, observation: BalanceObservation) -> Call[None]:
        """``BALANCE_PROPOSER`` only; ``settled_balance`` in local decimals."""
        return self._write(
            f"proposeBalance({_BALANCE_OBSERVATION_TUPLE})", observation.as_tuple()
        )

    def reject_and_block_proposal(self, proposal_id: int) -> Call[None]:
        """``BALANCE_APPROVER`` only: reject and fail the chain closed."""
        return self._write("rejectAndBlockProposal(uint256)", proposal_id)

    def cancel_stale_return_request(self, chain_id: ChainId) -> Call[None]:
        """Permissionless: free a recall slot after ``TRANSFER_STALENESS_MAX``
        while no return metadata or compose has arrived."""
        return self._write("cancelStaleReturnRequest(uint256)", chain_id)


def _active_command_decoder(values: tuple) -> ActiveCommand:
    active, is_config, awaiting_cancel, status, command_id, sequence, epoch = values
    return ActiveCommand(
        active=bool(active),
        is_config_update=bool(is_config),
        awaiting_cancel=bool(awaiting_cancel),
        status=CommandStatus(status),
        command_id=bytes(command_id),
        sequence=int(sequence),
        new_config_epoch=int(epoch),
    )


class StargateCrosschainDispatcher(CrosschainDispatcher):
    """``StargateCrosschainDispatcher``: the remote half of a Stargate executor,
    at the executor's address on the remote chain."""

    def stargate_pool(self) -> Call[ChecksumAddress]:
        return self._view("STARGATE_POOL()", output_types=["address"], decoder=_address)

    def executor_eid(self) -> Call[int]:
        return self._view("EXECUTOR_EID()", output_types=["uint32"])

    def observation(self) -> Call[Observation]:
        """``trackedIdle`` in local decimals plus ``convertToAssets`` of every
        tracked vault position, at one EVM state snapshot."""
        return self._view(
            "observation()",
            output_types=["(uint256,address[],uint256[],uint64,uint64,bytes32)"],
            decoder=_observation_decoder,
        )

    def get_allowed_vaults(self) -> Call[list[ChecksumAddress]]:
        return self._view(
            "getAllowedVaults()", output_types=["address[]"], decoder=_address_list
        )

    def get_tracked_vaults(self) -> Call[list[ChecksumAddress]]:
        return self._view(
            "getTrackedVaults()", output_types=["address[]"], decoder=_address_list
        )

    def recall_consumed(self, request_id: bytes) -> Call[bool]:
        return self._view("recallConsumed(bytes32)", request_id, output_types=["bool"])

    def transfer_settled(self, transfer_id: bytes) -> Call[bool]:
        return self._view(
            "transferSettled(bytes32)", transfer_id, output_types=["bool"]
        )


def _observation_decoder(values: tuple) -> Observation:
    idle, vaults, asset_values, version, epoch, set_hash = values
    return Observation(
        tracked_idle=Amount(idle),
        tracked_vaults=_address_list(list(vaults)),
        vault_asset_values=[Amount(v) for v in asset_values],
        state_version=int(version),
        command_config_epoch=int(epoch),
        tracked_position_set_hash=bytes(set_hash),
    )


class StargateTokenMessaging(ContractWrapper):
    """Stargate V2 ``TokenMessaging``, the OApp that carries taxi packets:
    ``peers`` tells whether a route to an endpoint id exists."""

    def peers(self, eid: int) -> Call[bytes]:
        """The peer OApp on ``eid`` as bytes32; zero when no route exists."""
        return self._view("peers(uint32)", eid, output_types=["bytes32"])

    def stargate_impls(self, asset_id: int) -> Call[ChecksumAddress]:
        """The local pool of ``asset_id``."""
        return self._view(
            "stargateImpls(uint16)",
            asset_id,
            output_types=["address"],
            decoder=_address,
        )


class StargateCrosschainFactory(CrosschainFactory):
    """``StargateCrosschainFactory``: same CREATE3 address on every chain;
    creates executors and, on a ticket from a peer factory, dispatchers."""

    def create_executor(
        self, user_salt: bytes, params: ExecutorInitParams
    ) -> Call[ChecksumAddress]:
        """Deploy an executor for ``msg.sender``/``user_salt``. A write: ``.send()``
        it, or ``.call()`` inside a simulation to preview the address (equal to
        ``compute_executor_address``). Reverts ``CreatorNotAllowed`` while
        creation is restricted and the caller is not allowlisted."""
        return self._view(
            f"createExecutor(bytes32,{_EXECUTOR_INIT_PARAMS_TUPLE})",
            user_salt,
            params.as_tuple(),
            output_types=["address"],
            decoder=_address,
        )

    def eid_of(self, chain_id: ChainId) -> Call[int]:
        return self._view("eidOf(uint256)", chain_id, output_types=["uint32"])

    def chain_id_of(self, eid: int) -> Call[ChainId]:
        return self._view(
            "chainIdOf(uint32)", eid, output_types=["uint256"], decoder=ChainId
        )

    def asset_config(
        self, asset_id: bytes
    ) -> Call[tuple[ChecksumAddress, ChecksumAddress, bool]]:
        """``(localToken, localStargatePool, enabled)`` for an asset id."""
        return self._view(
            "assetConfig(bytes32)",
            asset_id,
            output_types=["address", "address", "bool"],
            decoder=lambda v: (_address(v[0]), _address(v[1]), bool(v[2])),
        )

    def path_frozen(self, chain_id: ChainId) -> Call[bool]:
        return self._view("pathFrozen(uint256)", chain_id, output_types=["bool"])

    def max_native_fee(self, chain_id: ChainId) -> Call[int]:
        return self._view("maxNativeFee(uint256)", chain_id, output_types=["uint256"])

    def config_delay(self) -> Call[int]:
        return self._view("CONFIG_DELAY()", output_types=["uint256"])

    def request_count(self) -> Call[int]:
        return self._view("requestCount()", output_types=["uint256"])

    def cancel_pending_deployment(self, request_id: int) -> Call[None]:
        """Permissionless after the executor's ``DEPLOYMENT_TTL``."""
        return self._write("cancelPendingDeployment(uint256)", request_id)

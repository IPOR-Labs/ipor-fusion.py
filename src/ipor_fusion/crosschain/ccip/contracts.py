"""Wrappers of the Chainlink CCIP crosschain contracts
(``contracts/crosschain/ccip/**``)."""

from __future__ import annotations

from dataclasses import dataclass

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import keccak

from ipor_fusion.core.contract import Call
from ipor_fusion.crosschain.contracts import (
    BalanceObservation,
    CrosschainDispatcher,
    CrosschainExecutor,
    CrosschainFactory,
    _address,
)
from ipor_fusion.crosschain.messages import CommandStatus, CrosschainTransportKind
from ipor_fusion.types import Amount, ChainId


@dataclass(frozen=True, slots=True)
class CcipObservation:
    """``CcipCrosschainDispatcher.CcipObservation`` (amounts in shared decimals)."""

    accounted_balance: Amount
    tracked_idle: Amount
    queued_token_amount: Amount
    state_version: int
    command_config_epoch: int
    tracked_position_set_hash: bytes


@dataclass(frozen=True, slots=True)
class CcipRouteConfig:
    """``ICcipCrosschain.CcipRouteConfig``: one CCIP lane. ``chain_selector``
    and ``peer`` are immutable after registration; the rest is delivery policy
    synchronized from the factory."""

    chain_selector: int
    peer: ChecksumAddress
    fee_token: ChecksumAddress
    message_gas_limit: int
    token_gas_limit: int
    max_fee: int
    enabled: bool

    def as_tuple(self) -> tuple:
        return (
            self.chain_selector,
            self.peer,
            self.fee_token,
            self.message_gas_limit,
            self.token_gas_limit,
            self.max_fee,
            self.enabled,
        )

    @classmethod
    def from_tuple(cls, values: tuple) -> CcipRouteConfig:
        selector, peer, fee_token, message_gas, token_gas, max_fee, enabled = values
        return cls(
            chain_selector=int(selector),
            peer=_address(peer),
            fee_token=_address(fee_token),
            message_gas_limit=int(message_gas),
            token_gas_limit=int(token_gas),
            max_fee=int(max_fee),
            enabled=bool(enabled),
        )


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    """``CcipCrosschainExecutor.SafetyConfig``: the immutable configuration
    ``CcipCrosschainFactory.createExecutor`` bakes into a CCIP executor."""

    rescue_admin: ChecksumAddress
    rescue_guardian: ChecksumAddress
    balance_proposer: ChecksumAddress
    balance_approver: ChecksumAddress
    rescue_delay: int
    balance_staleness_max: int
    big_change_bps: int
    min_update_interval: int
    transfer_staleness_max: int

    def as_tuple(self) -> tuple:
        return (
            self.rescue_admin,
            self.rescue_guardian,
            self.balance_proposer,
            self.balance_approver,
            self.rescue_delay,
            self.balance_staleness_max,
            self.big_change_bps,
            self.min_update_interval,
            self.transfer_staleness_max,
        )


_ROUTE_TUPLE = "(uint64,address,address,uint96,uint96,uint256,bool)"
_UINT96_MAX = 2**96 - 1


def _require_uint96(gas_limit: int) -> None:
    if not 0 <= gas_limit <= _UINT96_MAX:
        raise ValueError(f"gas limit {gas_limit} is outside uint96")


_SAFETY_CONFIG_TUPLE = (
    "(address,address,address,address,uint256,uint256,uint256,uint256,uint256)"
)


class CcipCrosschainExecutor(CrosschainExecutor):
    """``CcipCrosschainExecutor``: the Chainlink CCIP executor."""

    def transport_kind(self) -> Call[CrosschainTransportKind]:
        return self._view(
            "transportKind()", output_types=["uint8"], decoder=CrosschainTransportKind
        )

    def executor_interface_version(self) -> Call[int]:
        return self._view("executorInterfaceVersion()", output_types=["uint32"])

    def ccip_router(self) -> Call[ChecksumAddress]:
        return self._view("CCIP_ROUTER()", output_types=["address"], decoder=_address)

    def decimal_conversion_rate(self) -> Call[int]:
        return self._view("DECIMAL_CONVERSION_RATE()", output_types=["uint256"])

    def balance_staleness_max(self) -> Call[int]:
        return self._view("BALANCE_STALENESS_MAX()", output_types=["uint256"])

    def ccip_route(self, chain_id: ChainId) -> Call[CcipRouteConfig]:
        return self._view(
            "ccipRoute(uint256)",
            chain_id,
            output_types=[_ROUTE_TUPLE],
            decoder=CcipRouteConfig.from_tuple,
        )

    def chain_id_of_selector(self, selector: int) -> Call[ChainId]:
        return self._view(
            "chainIdOfSelector(uint64)",
            selector,
            output_types=["uint256"],
            decoder=ChainId,
        )

    def last_remote_state_version(self, chain_id: ChainId) -> Call[int]:
        return self._view(
            "lastRemoteStateVersion(uint256)", chain_id, output_types=["uint64"]
        )

    def active_return(self, chain_id: ChainId) -> Call[bytes]:
        return self._view(
            "activeReturn(uint256)", chain_id, output_types=["bytes32"], decoder=bytes
        )

    def active_command(self, chain_id: ChainId) -> Call[bytes]:
        """The active command id for the chain, or 32 zero bytes."""
        return self._view(
            "activeCommand(uint256)",
            chain_id,
            output_types=["bytes32"],
            decoder=bytes,
        )

    def next_command_sequence(self, chain_id: ChainId) -> Call[int]:
        return self._view(
            "nextCommandSequence(uint256)", chain_id, output_types=["uint64"]
        )

    def command_config_epoch(self, chain_id: ChainId) -> Call[int]:
        return self._view(
            "commandConfigEpoch(uint256)", chain_id, output_types=["uint64"]
        )

    def accounting_epoch(self, chain_id: ChainId) -> Call[int]:
        """``accountingEpoch(uint256)``: the per-chain accounting epoch that
        every value-moving operation bumps to invalidate the active proposal."""
        return self._view("accountingEpoch(uint256)", chain_id, output_types=["uint64"])

    def active_proposal_id(self, chain_id: ChainId) -> Call[int]:
        """``activeProposalId(uint256)``: the active balance proposal id for
        the chain, 0 when there is none."""
        return self._view(
            "activeProposalId(uint256)", chain_id, output_types=["uint256"]
        )

    def attestation_anchor_principal(self, chain_id: ChainId) -> Call[Amount]:
        """``attestationAnchorPrincipal(uint256)``: the per-chain accounting
        anchor ``approveBalance`` bounds attestations against, in shared
        decimals."""
        return self._view(
            "attestationAnchorPrincipal(uint256)",
            chain_id,
            output_types=["uint256"],
            decoder=Amount,
        )

    def command_status(self, command_id: bytes) -> Call[CommandStatus]:
        return self._view(
            "commandStatus(bytes32)",
            command_id,
            output_types=["uint8"],
            decoder=CommandStatus,
        )

    def oldest_pending_at(self, chain_id: ChainId) -> Call[int]:
        return self._view("oldestPendingAt(uint256)", chain_id, output_types=["uint64"])

    def pending_transfer_count(self, chain_id: ChainId) -> Call[int]:
        return self._view(
            "pendingTransferCount(uint256)", chain_id, output_types=["uint8"]
        )

    def processed_ccip_message(self, message_id: bytes) -> Call[bool]:
        return self._view(
            "processedCcipMessage(bytes32)", message_id, output_types=["bool"]
        )

    def propose_balance(self, observation: BalanceObservation) -> Call[int]:
        """``BALANCE_PROPOSER`` only; ``settled_balance`` in local decimals.

        A write: ``.send()`` it, or ``.call()`` inside a simulation to preview
        the proposal id.
        """
        return self._view(
            "proposeBalance(uint256,uint256,uint64,uint64,uint64,uint64,bytes32)",
            *observation.as_tuple(),
            output_types=["uint256"],
        )

    def reject_balance_and_block(self, proposal_id: int) -> Call[None]:
        """``BALANCE_APPROVER`` only: reject and fail the chain closed."""
        return self._write("rejectBalanceAndBlock(uint256)", proposal_id)

    def cancel_stale_pending_return(self, chain_id: ChainId) -> Call[None]:
        """Permissionless: free a recall slot after ``TRANSFER_STALENESS_MAX``."""
        return self._write("cancelStalePendingReturn(uint256)", chain_id)


class CcipCrosschainDispatcher(CrosschainDispatcher):
    """``CcipCrosschainDispatcher``: the remote half of a CCIP executor."""

    def ccip_router(self) -> Call[ChecksumAddress]:
        return self._view("CCIP_ROUTER()", output_types=["address"], decoder=_address)

    def ccip_route(self, chain_id: ChainId) -> Call[CcipRouteConfig]:
        return self._view(
            "ccipRoute(uint256)",
            chain_id,
            output_types=[_ROUTE_TUPLE],
            decoder=CcipRouteConfig.from_tuple,
        )

    def observation(self) -> Call[CcipObservation]:
        return self._view(
            "observation()",
            output_types=["(uint256,uint256,uint256,uint64,uint64,bytes32)"],
            decoder=_ccip_observation_decoder,
        )

    def accounted_balance(self) -> Call[Amount]:
        """Tracked idle plus queued tokens plus every tracked vault position,
        in shared decimals."""
        return self._view(
            "accountedBalance()", output_types=["uint256"], decoder=Amount
        )

    def tracked_vaults_length(self) -> Call[int]:
        return self._view("trackedVaultsLength()", output_types=["uint256"])

    def tracked_position_set_hash(self) -> Call[bytes]:
        return self._view(
            "trackedPositionSetHash()", output_types=["bytes32"], decoder=bytes
        )

    def response_queue_head(self) -> Call[int]:
        return self._view("responseQueueHead()", output_types=["uint64"])

    def response_queue_tail(self) -> Call[int]:
        return self._view("responseQueueTail()", output_types=["uint64"])

    def queued_token_amount(self) -> Call[Amount]:
        return self._view(
            "queuedTokenAmount()", output_types=["uint256"], decoder=Amount
        )

    def processed_operation(self, operation_id: bytes) -> Call[bool]:
        return self._view(
            "processedOperation(bytes32)", operation_id, output_types=["bool"]
        )

    def processed_ccip_message(self, message_id: bytes) -> Call[bool]:
        return self._view(
            "processedCcipMessage(bytes32)", message_id, output_types=["bool"]
        )

    def flush_response(self) -> Call[None]:
        """Permissionless: retry the head of the ordered response queue once
        its fee budget or receiver failure has been repaired."""
        return self._write("flushResponse()")


def _ccip_observation_decoder(values: tuple) -> CcipObservation:
    accounted, idle, queued, version, epoch, set_hash = values
    return CcipObservation(
        accounted_balance=Amount(accounted),
        tracked_idle=Amount(idle),
        queued_token_amount=Amount(queued),
        state_version=int(version),
        command_config_epoch=int(epoch),
        tracked_position_set_hash=bytes(set_hash),
    )


class CcipCrosschainFactory(CrosschainFactory):
    """``CcipCrosschainFactory``: same CREATE3 address on every chain; creates
    executors and, on a CCIP ticket from a peer factory, dispatchers."""

    def create_executor(
        self,
        user_salt: bytes,
        asset_id: bytes,
        manager: ChecksumAddress,
        safety: SafetyConfig,
    ) -> Call[ChecksumAddress]:
        """Deploy a CCIP executor bound to ``manager``. Same write/preview
        semantics as the Stargate factory's ``create_executor``."""
        return self._view(
            f"createExecutor(bytes32,bytes32,address,{_SAFETY_CONFIG_TUPLE})",
            user_salt,
            asset_id,
            manager,
            safety.as_tuple(),
            output_types=["address"],
            decoder=_address,
        )

    def factory_interface_version(self) -> Call[int]:
        return self._view("factoryInterfaceVersion()", output_types=["uint32"])

    def ccip_router(self) -> Call[ChecksumAddress]:
        return self._view("CCIP_ROUTER()", output_types=["address"], decoder=_address)

    def ccip_route(self, chain_id: ChainId) -> Call[CcipRouteConfig]:
        return self._view(
            "ccipRoute(uint256)",
            chain_id,
            output_types=[_ROUTE_TUPLE],
            decoder=CcipRouteConfig.from_tuple,
        )

    def chain_id_of_selector(self, selector: int) -> Call[ChainId]:
        return self._view(
            "chainIdOfSelector(uint64)",
            selector,
            output_types=["uint256"],
            decoder=ChainId,
        )

    def asset_config(self, asset_id: bytes) -> Call[tuple[ChecksumAddress, int, bool]]:
        """``(token, sharedDecimals, enabled)`` for an asset id."""
        return self._view(
            "assetConfig(bytes32)",
            asset_id,
            output_types=["address", "uint8", "bool"],
            decoder=lambda v: (_address(v[0]), int(v[1]), bool(v[2])),
        )

    def is_dispatcher(self, dispatcher: ChecksumAddress) -> Call[bool]:
        return self._view("isDispatcher(address)", dispatcher, output_types=["bool"])

    def pending_deployment_ticket(
        self, executor: ChecksumAddress, dst_chain_id: ChainId
    ) -> Call[bytes]:
        return self._view(
            "pendingDeploymentTicket(address,uint256)",
            executor,
            dst_chain_id,
            output_types=["bytes32"],
            decoder=bytes,
        )

    def pending_deployment_created_at(
        self, executor: ChecksumAddress, dst_chain_id: ChainId
    ) -> Call[int]:
        return self._view(
            "pendingDeploymentCreatedAt(address,uint256)",
            executor,
            dst_chain_id,
            output_types=["uint64"],
        )

    def owner(self) -> Call[ChecksumAddress]:
        return self._view("OWNER()", output_types=["address"], decoder=_address)

    def config_delay(self) -> Call[int]:
        """Timelock of every scheduled asset, factory route and dispatcher
        deployment gas limit: 24 hours in source, 300 seconds on the test
        builds."""
        return self._view("CONFIG_DELAY()", output_types=["uint256"])

    def executor_creation_code_hash(self) -> Call[bytes]:
        """keccak of the executor creation code the factory stores. It tells
        builds apart where ``executorInterfaceVersion`` does not."""
        return self._view(
            "executorCreationCodeHash()", output_types=["bytes32"], decoder=bytes
        )

    def dispatcher_creation_code_hash(self) -> Call[bytes]:
        return self._view(
            "dispatcherCreationCodeHash()", output_types=["bytes32"], decoder=bytes
        )

    def set_creation_restricted(self, restricted: bool) -> Call[None]:
        """Owner-only. Restricted (the default) limits ``createExecutor`` to
        allowed creators, each a spender of the factory's fee budget."""
        return self._write("setCreationRestricted(bool)", restricted)

    def set_creator_allowed(
        self, creator: ChecksumAddress, allowed: bool
    ) -> Call[None]:
        """Owner-only; the owner is allowed from construction."""
        return self._write("setCreatorAllowed(address,bool)", creator, allowed)

    def configure_creation_codes(
        self, executor_code: bytes, dispatcher_code: bytes
    ) -> Call[None]:
        """Owner-only and write-once: the factory stores both creation codes
        (constructor arguments are appended per deployment) and reverts
        ``CreationCodesAlreadyConfigured`` afterwards. A new generation of
        executors therefore needs a new factory, not a re-registration."""
        if not executor_code or not dispatcher_code:
            raise ValueError("executor and dispatcher creation code must not be empty")
        return self._write(
            "configureCreationCodes(bytes,bytes)", executor_code, dispatcher_code
        )

    def schedule_asset(
        self, asset_id: bytes, token: ChecksumAddress, shared_decimals: int
    ) -> Call[None]:
        """Owner-only; executable after ``config_delay`` with the same arguments."""
        return self._write(
            "scheduleAsset(bytes32,address,uint8)", asset_id, token, shared_decimals
        )

    def execute_asset(
        self, asset_id: bytes, token: ChecksumAddress, shared_decimals: int
    ) -> Call[None]:
        return self._write(
            "executeAsset(bytes32,address,uint8)", asset_id, token, shared_decimals
        )

    def schedule_factory_route(
        self, chain_id: ChainId, route: CcipRouteConfig
    ) -> Call[None]:
        """Owner-only; executable after ``config_delay`` with the same route.
        The peer of a factory-to-factory route is the factory itself (same
        CREATE3 address on every chain), so any other peer is refused here as
        the contract refuses it."""
        self._require_self_peer(route)
        return self._write(
            f"scheduleFactoryRoute(uint256,{_ROUTE_TUPLE})", chain_id, route.as_tuple()
        )

    def execute_factory_route(
        self, chain_id: ChainId, route: CcipRouteConfig
    ) -> Call[None]:
        self._require_self_peer(route)
        return self._write(
            f"executeFactoryRoute(uint256,{_ROUTE_TUPLE})", chain_id, route.as_tuple()
        )

    def dispatcher_deployment_gas_limit(self, chain_id: ChainId) -> Call[int]:
        """``dispatcherDeploymentGasLimit(uint256)``: the destination gas limit
        of the ``CREATE_DISPATCHER`` ticket to ``chain_id``. 0 means the ticket
        falls back to the route's ``messageGasLimit``; commands, ACK and NACK
        always use ``messageGasLimit``. A factory without this getter (built
        before it existed) reverts the call."""
        return self._view(
            "dispatcherDeploymentGasLimit(uint256)", chain_id, output_types=["uint96"]
        )

    def schedule_dispatcher_deployment_gas_limit(
        self, chain_id: ChainId, gas_limit: int
    ) -> Call[None]:
        """Owner-only; executable after ``config_delay`` with the same
        arguments. 0 resets the ticket to the route's ``messageGasLimit``."""
        _require_uint96(gas_limit)
        return self._write(
            "scheduleDispatcherDeploymentGasLimit(uint256,uint96)", chain_id, gas_limit
        )

    def execute_dispatcher_deployment_gas_limit(
        self, chain_id: ChainId, gas_limit: int
    ) -> Call[None]:
        _require_uint96(gas_limit)
        return self._write(
            "executeDispatcherDeploymentGasLimit(uint256,uint96)", chain_id, gas_limit
        )

    @staticmethod
    def dispatcher_deployment_gas_limit_commitment(
        chain_id: ChainId, gas_limit: int
    ) -> bytes:
        """The timelock commitment of a scheduled dispatcher deployment gas
        limit, ``keccak256(abi.encode("DEPLOY_GAS", chainId, gasLimit))``: the
        argument :meth:`cancel_scheduled_config` takes to drop it."""
        _require_uint96(gas_limit)
        return keccak(
            encode(["string", "uint256", "uint96"], ["DEPLOY_GAS", chain_id, gas_limit])
        )

    def cancel_scheduled_config(self, commitment: bytes) -> Call[None]:
        """Owner-only: drop a scheduled asset, route or dispatcher deployment
        gas limit by its commitment hash."""
        return self._write("cancelScheduledConfig(bytes32)", commitment)

    def _require_self_peer(self, route: CcipRouteConfig) -> None:
        if route.peer != self.address:
            raise ValueError(
                f"factory route peer {route.peer} must be the factory itself "
                f"({self.address})"
            )

    def register_executor_route(
        self,
        executor: ChecksumAddress,
        chain_id: ChainId,
        route: CcipRouteConfig,
    ) -> Call[None]:
        """Register the factory-approved route on ``executor``. The executor's
        manager must send this call and ``route.peer`` must be the executor."""
        if route.peer != executor:
            raise ValueError(
                f"CCIP executor route peer {route.peer} != executor {executor}"
            )
        return self._write(
            f"registerExecutorRoute(address,uint256,{_ROUTE_TUPLE})",
            executor,
            chain_id,
            route.as_tuple(),
        )

    def request_dispatcher(
        self,
        executor: ChecksumAddress,
        dst_chain_id: ChainId,
        vaults: list[ChecksumAddress],
        max_fee: int,
    ) -> Call[bytes]:
        """Request the executor's CREATE3 dispatcher on ``dst_chain_id``.

        The executor's manager must send this call. The source factory pays
        the quoted CCIP fee from its native balance. The ticket carries the
        factory's :meth:`dispatcher_deployment_gas_limit` for ``dst_chain_id``,
        or the route's ``messageGasLimit`` when that is 0. A write: ``.send()`` it,
        or ``.call()`` inside a simulation to preview the CCIP message id.
        """
        return self._view(
            "requestDispatcher(address,uint256,address[],uint256)",
            executor,
            dst_chain_id,
            vaults,
            max_fee,
            output_types=["bytes32"],
            decoder=bytes,
        )

    def sync_ccip_route_policy(
        self, app: ChecksumAddress, chain_id: ChainId
    ) -> Call[None]:
        """Permissionless: copy the factory's route policy for ``chain_id`` to a
        registered executor or dispatcher."""
        return self._write("syncCcipRoutePolicy(address,uint256)", app, chain_id)

    def cancel_expired_deployment(
        self, executor: ChecksumAddress, dst_chain_id: ChainId
    ) -> Call[None]:
        """Permissionless strictly after ``DEPLOYMENT_TTL`` (7 days)."""
        return self._write(
            "cancelExpiredDeployment(address,uint256)", executor, dst_chain_id
        )

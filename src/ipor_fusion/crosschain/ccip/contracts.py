"""Wrappers of the Chainlink CCIP crosschain contracts
(``contracts/crosschain/ccip/**``)."""

from __future__ import annotations

from dataclasses import dataclass

from eth_typing import ChecksumAddress

from ipor_fusion.core.contract import Call
from ipor_fusion.crosschain.contracts import (
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

    def propose_balance(
        self,
        chain_id: ChainId,
        balance: Amount,
        state_version: int,
        remote_block: int,
        remote_timestamp: int,
        expiry: int,
        tracked_position_set_hash: bytes,
    ) -> Call[None]:
        """``BALANCE_PROPOSER`` only; ``balance`` in local decimals."""
        return self._write(
            "proposeBalance(uint256,uint256,uint64,uint64,uint64,uint64,bytes32)",
            chain_id,
            balance,
            state_version,
            remote_block,
            remote_timestamp,
            expiry,
            tracked_position_set_hash,
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

"""The Chainlink CCIP lane."""

from __future__ import annotations

from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3.types import Timestamp

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.crosschain.ccip.contracts import (
    CcipCrosschainDispatcher,
    CcipCrosschainExecutor,
    CcipObservation,
    CcipRouteConfig,
)
from ipor_fusion.crosschain.contracts import BalanceObservation
from ipor_fusion.crosschain.fuses.base import SendParams
from ipor_fusion.crosschain.fuses.ccip import (
    CcipCrosschainCommandFuse,
    CcipCrosschainSupplyFuse,
    CcipSendParams,
)
from ipor_fusion.crosschain.lane import CrosschainLane, LaneFuses, LaneObservation
from ipor_fusion.crosschain.messages import CommandStatus, CrosschainTransportKind
from ipor_fusion.errors import CrosschainDiscoveryError
from ipor_fusion.types import Amount, ChainId, Period, ProposalId


class CcipLane(CrosschainLane):
    """A lane over CCIP programmable token transfers and messages. The default
    send parameters come from the executor's registered ``route`` (fee
    ceiling, fee token, token or message gas limit). ``decimal_conversion_rate``
    is the executor's ``DECIMAL_CONVERSION_RATE`` (local per shared unit), used
    to report the dispatcher observation in local decimals.

    `open` refuses a spoke the executor has no route to, or whose route names
    another peer than the dispatcher at the executor's address; a registered but
    disabled route opens (state can still be read) and refuses the default send
    parameters."""

    transport_kind = CrosschainTransportKind.CHAINLINK_CCIP
    enforces_min_received = False
    BALANCE_PROPOSED_TOPIC = keccak(
        text="BalanceProposed(uint256,uint256,uint256,uint64,bytes32)"
    )

    supply_fuse_cls = CcipCrosschainSupplyFuse
    command_fuse_cls = CcipCrosschainCommandFuse

    executor: CcipCrosschainExecutor
    dispatcher: CcipCrosschainDispatcher
    supply_fuse: CcipCrosschainSupplyFuse
    command_fuse: CcipCrosschainCommandFuse

    def __init__(
        self,
        *,
        hub_ctx: Web3Context,
        spoke_ctx: Web3Context,
        executor: CcipCrosschainExecutor,
        dispatcher: CcipCrosschainDispatcher,
        fuses: LaneFuses,
        route: CcipRouteConfig,
        decimal_conversion_rate: int,
    ) -> None:
        super().__init__(
            hub_ctx=hub_ctx,
            spoke_ctx=spoke_ctx,
            executor=executor,
            dispatcher=dispatcher,
            fuses=fuses,
        )
        self.route = route
        self.decimal_conversion_rate = decimal_conversion_rate

    @classmethod
    def open(
        cls,
        hub_ctx: Web3Context,
        spoke_ctx: Web3Context,
        *,
        executor: ChecksumAddress,
        fuses: LaneFuses,
    ) -> CcipLane:
        hub_executor = CcipCrosschainExecutor(executor)
        route = hub_executor.ccip_route(spoke_ctx.chain_id).call(hub_ctx)
        _require_route_to_dispatcher(route, hub_executor.address, spoke_ctx.chain_id)
        return cls(
            hub_ctx=hub_ctx,
            spoke_ctx=spoke_ctx,
            executor=hub_executor,
            dispatcher=CcipCrosschainDispatcher(executor),
            fuses=fuses,
            route=route,
            decimal_conversion_rate=hub_executor.decimal_conversion_rate().call(
                hub_ctx
            ),
        )

    def staleness_max(self) -> Call[Period]:
        return self.executor.balance_staleness_max()

    def default_send(self, *, token: bool) -> CcipSendParams:
        if not self.route.enabled:
            raise CrosschainDiscoveryError(
                f"CCIP route of executor {self.executor.address} to chain "
                f"{self.spoke_chain_id} is disabled"
            )
        return CcipSendParams.from_route(self.route, token=token)

    def default_command_send(self) -> CcipSendParams:
        return self.default_send(token=False)

    def _supply_send(
        self, send: SendParams, amount: Amount, min_received: Amount
    ) -> CcipSendParams:
        if not isinstance(send, CcipSendParams):
            raise TypeError(f"CcipLane takes CcipSendParams, got {type(send).__name__}")
        if min_received != 0:
            raise ValueError(
                "CCIP cannot enforce min_received after token-pool fees; "
                "pass 0 and inspect the settlement receipt"
            )
        return send

    def remote_state_version(self) -> Call[int]:
        return self.executor.last_remote_state_version(self.spoke_chain_id)

    def has_active_command(self) -> Call[bool]:
        return self.executor.active_command(self.spoke_chain_id).map(
            lambda command_id: command_id != bytes(32)
        )

    def command_in_flight(self) -> bool:
        command_id = self.executor.active_command(self.spoke_chain_id).call(
            self.hub_ctx
        )
        if command_id == bytes(32):
            return False
        status = self.executor.command_status(command_id).call(self.hub_ctx)
        return status == CommandStatus.PENDING

    def pending_transfer_count(self) -> Call[int]:
        return self.executor.pending_transfer_count(self.spoke_chain_id)

    def transfer_in_flight(self) -> bool:
        if self.pending_transfer_count().call(self.hub_ctx) != 0:
            return True
        active_return = self.executor.active_return(self.spoke_chain_id)
        return active_return.call(self.hub_ctx) != bytes(32)

    def observation(self) -> Call[LaneObservation]:
        rate = self.decimal_conversion_rate
        return self.dispatcher.observation().map(
            lambda observation: _to_lane(observation, rate)
        )

    def last_value_moving_receipt_at(self) -> Call[Timestamp]:
        return self.executor.last_value_moving_receipt_at(self.spoke_chain_id)

    def propose_balance(self, observation: BalanceObservation) -> Call[ProposalId]:
        """Returns the proposal id (the function's return value)."""
        self._require_spoke(observation)
        return self.executor.propose_balance(observation)


def _to_lane(observation: CcipObservation, rate: int) -> LaneObservation:
    return LaneObservation(
        tracked_idle=Amount(observation.tracked_idle * rate),
        accounted_balance=Amount(observation.accounted_balance * rate),
        state_version=observation.state_version,
        command_config_epoch=observation.command_config_epoch,
        tracked_position_set_hash=observation.tracked_position_set_hash,
        unpriced_count=observation.unpriced_count,
    )


def _require_route_to_dispatcher(
    route: CcipRouteConfig, executor: ChecksumAddress, spoke: ChainId
) -> None:
    """The executor sends to the dispatcher at its own address on the spoke; an
    unregistered route reads back as all zeros."""
    if route.chain_selector == 0 or int(route.peer, 16) == 0:
        raise CrosschainDiscoveryError(
            f"CCIP executor {executor} has no route to chain {spoke}"
        )
    if route.peer != executor:
        raise CrosschainDiscoveryError(
            f"CCIP executor {executor} routes chain {spoke} to {route.peer}, "
            "not to the dispatcher at its own address"
        )

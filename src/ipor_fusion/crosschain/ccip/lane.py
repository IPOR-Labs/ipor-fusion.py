"""The Chainlink CCIP lane."""

from __future__ import annotations

from typing import Any

from eth_typing import ChecksumAddress
from eth_utils import keccak

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.crosschain.ccip.contracts import (
    CcipCrosschainDispatcher,
    CcipCrosschainExecutor,
    CcipObservation,
    CcipRouteConfig,
    _ccip_observation_decoder,
)
from ipor_fusion.crosschain.contracts import BalanceObservation
from ipor_fusion.crosschain.lane import CrosschainLane, LaneFuses, LaneObservation
from ipor_fusion.crosschain.messages import CrosschainTransportKind
from ipor_fusion.fuses.crosschain.base import SendParams
from ipor_fusion.fuses.crosschain.ccip import (
    CcipCrosschainCommandFuse,
    CcipCrosschainSupplyFuse,
    CcipSendParams,
)
from ipor_fusion.types import Amount, ChainId


class CcipLane(CrosschainLane):
    """A lane over CCIP programmable token transfers and messages. The default
    send parameters come from the executor's registered ``route`` (fee
    ceiling, fee token, token or message gas limit). ``decimal_conversion_rate``
    is the executor's ``DECIMAL_CONVERSION_RATE`` (local per shared unit; 1
    for USDC), used to report the dispatcher observation in local decimals."""

    transport_kind = CrosschainTransportKind.CHAINLINK_CCIP
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
        executor: CcipCrosschainExecutor,
        dispatcher: CcipCrosschainDispatcher,
        spoke_chain_id: ChainId,
        fuses: LaneFuses,
        route: CcipRouteConfig,
        decimal_conversion_rate: int = 1,
    ) -> None:
        super().__init__(
            executor=executor,
            dispatcher=dispatcher,
            spoke_chain_id=spoke_chain_id,
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
        hub_executor = CcipCrosschainExecutor(hub_ctx, executor)
        spoke = ChainId(spoke_ctx.chain_id)
        return cls(
            executor=hub_executor,
            dispatcher=CcipCrosschainDispatcher(spoke_ctx, executor),
            spoke_chain_id=spoke,
            fuses=fuses,
            route=hub_executor.ccip_route(spoke).call(),
        )

    def staleness_max(self) -> Call[int]:
        return self.executor.balance_staleness_max()

    def default_send(self, *, token: bool) -> CcipSendParams:
        return CcipSendParams.from_route(self.route, token=token)

    def default_command_send(self) -> CcipSendParams:
        return self.default_send(token=False)

    def _supply_send(
        self, send: SendParams, amount: Amount, min_received: Amount
    ) -> CcipSendParams:
        if not isinstance(send, CcipSendParams):
            raise TypeError(f"CcipLane takes CcipSendParams, got {type(send).__name__}")
        # CCIP credits the sent amount 1:1 on the destination; a floor above it
        # can never be met, so it is a caller error rather than a transport param.
        if min_received > amount:
            raise ValueError(f"min_received {min_received} exceeds amount {amount}")
        return send

    def remote_state_version(self) -> Call[int]:
        return self.executor.last_remote_state_version(self.spoke_chain_id)

    def has_active_command(self) -> Call[bool]:
        call = self.executor.active_command(self.spoke_chain_id)
        return Call(
            to=call.to,
            data=call.data,
            output_types=call.output_types,
            decoder=lambda value: bytes(value) != bytes(32),
            ctx=call.ctx,
        )

    def pending_transfer_count(self) -> Call[int]:
        return self.executor.pending_transfer_count(self.spoke_chain_id)

    def observation(self) -> Call[LaneObservation]:
        call = self.dispatcher.observation()
        rate = self.decimal_conversion_rate
        return Call(
            to=call.to,
            data=call.data,
            output_types=call.output_types,
            decoder=lambda values: _to_lane(_ccip_observation_decoder(values), rate),
            ctx=call.ctx,
        )

    def propose_balance(self, observation: BalanceObservation) -> Call[Any]:
        """Returns the proposal id (the function's return value)."""
        self._require_spoke(observation)
        call = self.executor.propose_balance(
            observation.chain_id,
            observation.settled_balance,
            observation.state_version,
            observation.remote_block,
            observation.remote_timestamp,
            observation.expiry,
            observation.tracked_position_set_hash,
        )
        return Call(to=call.to, data=call.data, output_types=["uint256"], ctx=call.ctx)


def _to_lane(observation: CcipObservation, rate: int) -> LaneObservation:
    return LaneObservation(
        tracked_idle=Amount(observation.tracked_idle * rate),
        accounted_balance=Amount(observation.accounted_balance * rate),
        state_version=observation.state_version,
        command_config_epoch=observation.command_config_epoch,
        tracked_position_set_hash=observation.tracked_position_set_hash,
    )

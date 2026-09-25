"""The Stargate V2 / LayerZero V2 lane."""

from __future__ import annotations

from dataclasses import replace
from typing import Any

from eth_typing import ChecksumAddress
from eth_utils import keccak

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.crosschain.contracts import BalanceObservation
from ipor_fusion.crosschain.lane import CrosschainLane, LaneFuses, LaneObservation
from ipor_fusion.crosschain.messages import CrosschainTransportKind
from ipor_fusion.crosschain.stargate.contracts import (
    Observation,
    StargateCrosschainDispatcher,
    StargateCrosschainExecutor,
    _active_command_decoder,
    _observation_decoder,
)
from ipor_fusion.crosschain.stargate.layerzero import OptionsBuilder
from ipor_fusion.fuses.crosschain.base import SendParams
from ipor_fusion.fuses.crosschain.stargate import (
    StargateCrosschainCommandFuse,
    StargateCrosschainSupplyFuse,
    StargateSendParams,
)
from ipor_fusion.types import Amount, ChainId


class StargateLane(CrosschainLane):
    """A lane over Stargate taxi transfers and LayerZero messages. The default
    options are the ones the mainnet POC scripts use: 250k ``lzReceive`` plus
    1.5M ``lzCompose`` gas on a supply, 2M ``lzReceive`` gas on a recall and
    no native drop. Pass ``send`` to change them or to add a native drop."""

    transport_kind = CrosschainTransportKind.STARGATE_LAYERZERO
    BALANCE_PROPOSED_TOPIC = keccak(
        text="BalanceProposed(uint256,uint256,uint256,uint64,uint64,uint64,uint64,bytes32)"
    )
    DEFAULT_SUPPLY_OPTIONS = (
        OptionsBuilder.new_options()
        .add_executor_lz_receive_option(250_000)
        .add_executor_lz_compose_option(0, 1_500_000)
    )
    DEFAULT_RECALL_OPTIONS = (
        OptionsBuilder.new_options().add_executor_lz_receive_option(2_000_000)
    )

    supply_fuse_cls = StargateCrosschainSupplyFuse
    command_fuse_cls = StargateCrosschainCommandFuse

    executor: StargateCrosschainExecutor
    dispatcher: StargateCrosschainDispatcher
    supply_fuse: StargateCrosschainSupplyFuse
    command_fuse: StargateCrosschainCommandFuse

    def __init__(
        self,
        *,
        executor: StargateCrosschainExecutor,
        dispatcher: StargateCrosschainDispatcher,
        spoke_chain_id: ChainId,
        fuses: LaneFuses,
    ) -> None:
        super().__init__(
            executor=executor,
            dispatcher=dispatcher,
            spoke_chain_id=spoke_chain_id,
            fuses=fuses,
        )

    @classmethod
    def open(
        cls,
        hub_ctx: Web3Context,
        spoke_ctx: Web3Context,
        *,
        executor: ChecksumAddress,
        fuses: LaneFuses,
    ) -> StargateLane:
        return cls(
            executor=StargateCrosschainExecutor(hub_ctx, executor),
            dispatcher=StargateCrosschainDispatcher(spoke_ctx, executor),
            spoke_chain_id=ChainId(spoke_ctx.chain_id),
            fuses=fuses,
        )

    def staleness_max(self) -> Call[int]:
        return self.executor.staleness_max()

    def default_send(self, *, token: bool) -> StargateSendParams:
        options = self.DEFAULT_SUPPLY_OPTIONS if token else self.DEFAULT_RECALL_OPTIONS
        return StargateSendParams(options=options)

    def default_command_send(self) -> None:
        return None

    def _supply_send(
        self, send: SendParams, amount: Amount, min_received: Amount
    ) -> StargateSendParams:
        if not isinstance(send, StargateSendParams):
            raise TypeError(
                f"StargateLane takes StargateSendParams, got {type(send).__name__}"
            )
        return replace(send, min_amount_ld=min_received)

    def remote_state_version(self) -> Call[int]:
        return self.executor.acknowledged_remote_state_version(self.spoke_chain_id)

    def has_active_command(self) -> Call[bool]:
        call = self.executor.active_command(self.spoke_chain_id)
        return Call(
            to=call.to,
            data=call.data,
            output_types=call.output_types,
            decoder=lambda values: _active_command_decoder(values).active,
            ctx=call.ctx,
        )

    def pending_transfer_count(self) -> Call[int]:
        return self.executor.pending_transfer_count(self.spoke_chain_id)

    def observation(self) -> Call[LaneObservation]:
        call = self.dispatcher.observation()
        return Call(
            to=call.to,
            data=call.data,
            output_types=call.output_types,
            decoder=lambda values: _to_lane(_observation_decoder(values)),
            ctx=call.ctx,
        )

    def propose_balance(self, observation: BalanceObservation) -> Call[Any]:
        """No return value on this transport: read the id with
        ``proposal_id_from_logs`` or ``executor.active_proposal_id``."""
        self._require_spoke(observation)
        return self.executor.propose_balance(observation)


def _to_lane(observation: Observation) -> LaneObservation:
    return LaneObservation(
        tracked_idle=observation.tracked_idle,
        accounted_balance=Amount(
            observation.tracked_idle + sum(observation.vault_asset_values)
        ),
        state_version=observation.state_version,
        command_config_epoch=observation.command_config_epoch,
        tracked_position_set_hash=observation.tracked_position_set_hash,
    )

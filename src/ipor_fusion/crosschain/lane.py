"""Lanes: one executor/dispatcher pair, driven without knowing its transport.

A lane is the hub-side executor plus its same-address dispatcher on one spoke
chain. ``CrosschainLane`` exposes what a keeper or alpha needs on any
transport: the supply, recall, command and claim actions as ``FuseAction``s
for ``PlasmaVault.execute``, the executor buckets, the dispatcher observation
normalized to ``LaneObservation``, and the two-key attestation. Transport
specifics (LayerZero options, CCIP fees) come from ``default_send`` unless
the caller passes ``send`` explicitly. Registration, balance refresh, retry
and cancel stay on the per-transport command fuses, reachable through
``lane.command_fuse``.

``open_lane`` in :mod:`ipor_fusion.crosschain.discovery` builds the right lane
for an executor address.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any, ClassVar

from eth_abi import decode
from eth_typing import ChecksumAddress

from ipor_fusion.core.contract import Call
from ipor_fusion.crosschain.contracts import (
    BalanceObservation,
    CrosschainDispatcher,
    CrosschainExecutor,
)
from ipor_fusion.crosschain.logs import log_address, log_bytes, log_topic0
from ipor_fusion.crosschain.messages import Command, CrosschainTransportKind
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.fuses.crosschain.base import (
    CrosschainClaimFuse,
    CrosschainCommandFuse,
    CrosschainSupplyFuse,
    SendParams,
)
from ipor_fusion.types import Amount, ChainId


@dataclass(frozen=True, slots=True)
class LaneObservation:
    """The dispatcher snapshot an attestation is derived from, the same shape
    on every transport, in the executor's local asset decimals.
    ``accounted_balance`` is tracked idle plus every tracked vault position:
    the value the proposer marks as the chain's settled balance."""

    tracked_idle: Amount
    accounted_balance: Amount
    state_version: int
    command_config_epoch: int
    tracked_position_set_hash: bytes


@dataclass(frozen=True, slots=True)
class LaneFuses:
    """The three fuses a lane drives on the hub vault."""

    supply: ChecksumAddress
    command: ChecksumAddress
    claim: ChecksumAddress


class CrosschainLane(ABC):
    """One executor/dispatcher pair on one spoke chain, transport-agnostic."""

    transport_kind: ClassVar[CrosschainTransportKind]
    #: topic0 of this transport's ``BalanceProposed`` event; ``proposalId`` is
    #: its first (non-indexed) field on both transports.
    BALANCE_PROPOSED_TOPIC: ClassVar[bytes]

    def __init__(
        self,
        *,
        executor: CrosschainExecutor,
        dispatcher: CrosschainDispatcher,
        spoke_chain_id: ChainId,
        fuses: LaneFuses,
        supply_fuse: CrosschainSupplyFuse,
        command_fuse: CrosschainCommandFuse,
    ) -> None:
        # CREATE3 puts the executor and its dispatcher at one address on every
        # chain; the fuses address the spoke side by the executor address.
        if dispatcher.address != executor.address:
            raise ValueError(
                "executor and dispatcher must share one address, got "
                f"{executor.address} and {dispatcher.address}"
            )
        self.executor = executor
        self.dispatcher = dispatcher
        self.spoke_chain_id = spoke_chain_id
        self.fuses = fuses
        self.supply_fuse = supply_fuse
        self.command_fuse = command_fuse
        self.claim_fuse: CrosschainClaimFuse = CrosschainClaimFuse(fuses.claim)

    @property
    def executor_address(self) -> ChecksumAddress:
        """The executor on the hub and the dispatcher on the spoke: one address."""
        return self.executor.address

    # ── actions: FuseActions for PlasmaVault.execute ──────────────────────

    def supply(
        self,
        *,
        asset: ChecksumAddress,
        amount: Amount,
        min_received: Amount,
        send: SendParams | None = None,
    ) -> FuseAction:
        """Bridge ``amount`` of ``asset`` to the dispatcher, requiring at least
        ``min_received`` to be credited there (the transport's slippage floor;
        CCIP delivers 1:1 and only checks the bound is not above ``amount``)."""
        params = send if send is not None else self.default_send(token=True)
        return self.supply_fuse.enter(
            executor=self.executor_address,
            asset=asset,
            dst_chain_id=self.spoke_chain_id,
            amount=amount,
            send=self._supply_send(params, amount, min_received),
        )

    def recall(
        self, *, amount: Amount, min_return: Amount, send: SendParams | None = None
    ) -> FuseAction:
        """Ask the dispatcher to return ``amount`` of its tracked idle; the
        tokens land in the executor's idle ledger later, then ``claim``."""
        return self.supply_fuse.exit(
            executor=self.executor_address,
            dst_chain_id=self.spoke_chain_id,
            amount=amount,
            min_return=min_return,
            send=send if send is not None else self.default_send(token=False),
        )

    def send_command(
        self, command: Command, send: SendParams | None = None
    ) -> FuseAction:
        """Send one business command (DEPOSIT, REDEEM, ...) to the dispatcher."""
        return self.command_fuse.send_command(
            executor=self.executor_address,
            chain_id=self.spoke_chain_id,
            command=command,
            send=send if send is not None else self.default_command_send(),
        )

    def claim(self, amount: Amount) -> FuseAction:
        """Pull up to ``amount`` of the executor's idle ledger into the vault."""
        return self.claim_fuse.enter(executor=self.executor_address, amount=amount)

    # ── reads: Calls on the executor ──────────────────────────────────────

    def settled_remote_balance(self) -> Call[Amount]:
        return self.executor.settled_remote_balance(self.spoke_chain_id)

    def outbound_in_flight(self) -> Call[Amount]:
        return self.executor.outbound_in_flight(self.spoke_chain_id)

    def idle_ledger(self) -> Call[Amount]:
        return self.executor.idle_ledger()

    def get_balance(self) -> Call[Amount]:
        """The executor's fail-closed NAV; reverts while the lane is stale or blocked."""
        return self.executor.get_balance()

    def is_dispatcher_ready(self) -> Call[bool]:
        return self.executor.is_dispatcher_ready(self.spoke_chain_id)

    def chain_blocked(self) -> Call[bool]:
        return self.executor.chain_blocked(self.spoke_chain_id)

    # ── attestation ───────────────────────────────────────────────────────

    def approve_balance(self, proposal_id: int) -> Call[None]:
        """``BALANCE_APPROVER`` only."""
        return self.executor.approve_balance(proposal_id)

    def proposal_id_from_logs(self, logs: Iterable[Mapping]) -> int:
        """The id of the proposal this lane's executor emitted in ``logs``
        (a receipt's or a simulated call's), from its ``BalanceProposed``."""
        for log in logs:
            if (
                log_topic0(log) == self.BALANCE_PROPOSED_TOPIC
                and log_address(log) == self.executor_address
            ):
                (proposal_id,) = decode(["uint256"], log_bytes(log["data"])[:32])
                return int(proposal_id)
        raise ValueError("no BalanceProposed log from the executor")

    def proposal_id_from_receipt(self, receipt: Mapping) -> int:
        return self.proposal_id_from_logs(receipt["logs"])

    def _require_spoke(self, observation: BalanceObservation) -> None:
        if observation.chain_id != self.spoke_chain_id:
            raise ValueError(
                f"observation is for chain {observation.chain_id}, "
                f"this lane serves {self.spoke_chain_id}"
            )

    # ── per-transport ─────────────────────────────────────────────────────

    @abstractmethod
    def default_send(self, *, token: bool) -> SendParams:
        """The transport parameters used when an action gets no ``send``:
        for a token-bearing send (supply) or a plain message (recall)."""

    @abstractmethod
    def default_command_send(self) -> SendParams | None:
        """The parameters of a command-lane send, or ``None`` when the
        transport takes none."""

    @abstractmethod
    def _supply_send(
        self, send: SendParams, amount: Amount, min_received: Amount
    ) -> SendParams:
        """Bind ``min_received`` into the transport's send parameters."""

    @abstractmethod
    def remote_state_version(self) -> Call[int]:
        """The dispatcher state version the executor has acknowledged: what an
        attestation must be observed at."""

    @abstractmethod
    def has_active_command(self) -> Call[bool]:
        """Whether a command still occupies the lane's single command slot."""

    @abstractmethod
    def pending_transfer_count(self) -> Call[int]:
        """Unresolved value-moving records (outbound and return) on the lane."""

    @abstractmethod
    def observation(self) -> Call[LaneObservation]:
        """The dispatcher's canonical snapshot, read on the spoke chain."""

    @abstractmethod
    def propose_balance(self, observation: BalanceObservation) -> Call[Any]:
        """``BALANCE_PROPOSER`` only. Returns the proposal id where the
        transport's function does (CCIP); Stargate emits it only, see
        ``proposal_id_from_logs``."""

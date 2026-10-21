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

The executor and its dispatcher share one address, so a ``Call`` alone cannot
tell which chain it belongs to. The lane owns both contexts: ``hub_ctx`` for
the executor, ``spoke_ctx`` for the dispatcher, and its eager reads pick the
right one: ``read_hub_state()`` (every executor bucket and counter at one block)
and the predicates ``needs_attestation``, ``command_in_flight`` and
``transfer_in_flight`` run on ``hub_ctx``, ``read_observation()`` on
``spoke_ctx``. The Calls it returns stay unexecuted, for a batch or a
simulation of the caller's own; the caller then runs them on the matching
context: ``lane.get_balance().call(lane.hub_ctx)``,
``lane.observation().call(lane.spoke_ctx)``.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import ClassVar

from eth_abi import decode
from eth_typing import ChecksumAddress
from web3.types import Timestamp

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.fuse_action import FuseAction
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.crosschain.contracts import (
    BalanceObservation,
    CrosschainDispatcher,
    CrosschainExecutor,
)
from ipor_fusion.crosschain.fuses.base import (
    CrosschainClaimFuse,
    CrosschainCommandFuse,
    CrosschainSupplyFuse,
    SendParams,
)
from ipor_fusion.crosschain.logs import log_address, log_bytes, log_topic0
from ipor_fusion.crosschain.messages import Command, CrosschainTransportKind
from ipor_fusion.types import (
    Amount,
    ChainId,
    FuseAddress,
    Period,
    PositionSetHash,
    ProposalId,
)


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
    tracked_position_set_hash: PositionSetHash


# Block tags that name a block number which moves between two reads; a read
# set pinned to the number the tag resolves to once keeps the tag's meaning.
# "pending" names no mined block and is left as is.
_MOVING_BLOCK_TAGS = frozenset({"latest", "safe", "finalized"})
_NO_MARGIN = Period(0)


@dataclass(frozen=True, slots=True)
class LaneHubState:
    """The lane's executor buckets and counters on the hub, read at one block
    (`CrosschainLane.read_hub_state`). Each field is the lane method of the
    same name; ``dispatcher_ready`` is ``is_dispatcher_ready``."""

    settled_remote_balance: Amount
    outbound_in_flight: Amount
    idle_ledger: Amount
    dispatcher_ready: bool
    chain_blocked: bool
    last_approved_observed_at: Timestamp
    staleness_max: Period
    remote_state_version: int
    has_active_command: bool
    pending_transfer_count: int


@dataclass(frozen=True, slots=True)
class LaneFuses:
    """The three fuses a lane drives on the hub vault."""

    supply: FuseAddress
    command: FuseAddress
    claim: FuseAddress


class CrosschainLane(ABC):
    """One executor/dispatcher pair on one spoke chain, transport-agnostic."""

    transport_kind: ClassVar[CrosschainTransportKind]
    #: Whether ``supply`` can enforce ``min_received`` after bridge fees.
    enforces_min_received: ClassVar[bool]
    #: The fuse encoders of this transport, instantiated on ``fuses``.
    supply_fuse_cls: ClassVar[type[CrosschainSupplyFuse]]
    command_fuse_cls: ClassVar[type[CrosschainCommandFuse]]
    #: topic0 of this transport's ``BalanceProposed`` event; ``proposalId`` is
    #: its first (non-indexed) field on both transports.
    BALANCE_PROPOSED_TOPIC: ClassVar[bytes]

    def __init__(
        self,
        *,
        hub_ctx: Web3Context,
        spoke_ctx: Web3Context,
        executor: CrosschainExecutor,
        dispatcher: CrosschainDispatcher,
        fuses: LaneFuses,
    ) -> None:
        # CREATE3 puts the executor and its dispatcher at one address on every
        # chain; the fuses address the spoke side by the executor address.
        if dispatcher.address != executor.address:
            raise ValueError(
                "executor and dispatcher must share one address, got "
                f"{executor.address} and {dispatcher.address}"
            )
        self.hub_ctx = hub_ctx
        self.spoke_ctx = spoke_ctx
        self.executor = executor
        self.dispatcher = dispatcher
        self.spoke_chain_id: ChainId = spoke_ctx.chain_id
        self.fuses = fuses
        self.supply_fuse: CrosschainSupplyFuse = self.supply_fuse_cls(fuses.supply)
        self.command_fuse: CrosschainCommandFuse = self.command_fuse_cls(fuses.command)
        self.claim_fuse: CrosschainClaimFuse = CrosschainClaimFuse(fuses.claim)

    @classmethod
    @abstractmethod
    def open(
        cls,
        hub_ctx: Web3Context,
        spoke_ctx: Web3Context,
        *,
        executor: ChecksumAddress,
        fuses: LaneFuses,
    ) -> CrosschainLane:
        """Wrap ``executor`` on the hub and its dispatcher on the spoke, reading
        whatever else this transport's lane needs (the CCIP route) on
        ``hub_ctx``."""

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
        """Bridge ``amount`` of ``asset`` to the dispatcher. When
        ``enforces_min_received`` is false, pass ``min_received=0`` and inspect
        the settlement receipt for the amount actually credited."""
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
        tokens land in the executor's idle ledger later, then ``claim``. On
        CCIP, ``min_return`` is checked on the spoke before bridge fees; the
        hub credits the post-fee amount even if it is below that floor."""
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

    # ── reads: Calls on the executor, run them on ``hub_ctx`` ─────────────

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

    def last_approved_observed_at(self) -> Call[Timestamp]:
        """Remote timestamp of the approved observation NAV freshness is
        measured from; 0 before the first attestation."""
        return self.executor.last_approved_observed_at(self.spoke_chain_id)

    # ── eager reads: each runs on the context the lane owns for it ────────

    def read_hub_state(self) -> LaneHubState:
        """Every executor read of :class:`LaneHubState` on ``hub_ctx``, batched
        through `Multicall3` at one block, so the fields share it even where
        Multicall3 falls back to one call per read: a ``latest``, ``safe`` or
        ``finalized`` default block is resolved to its number once; a number or
        hash is used as is (and ``pending`` too, which names no block)."""
        hub = self.hub_ctx
        if hub.default_block in _MOVING_BLOCK_TAGS:
            hub = hub.at_block(hub.web3.eth.get_block(hub.default_block)["number"])
        batch = Multicall3(hub).batch()
        settled_remote_balance = batch.add(self.settled_remote_balance())
        outbound_in_flight = batch.add(self.outbound_in_flight())
        idle_ledger = batch.add(self.idle_ledger())
        dispatcher_ready = batch.add(self.is_dispatcher_ready())
        chain_blocked = batch.add(self.chain_blocked())
        last_approved_observed_at = batch.add(self.last_approved_observed_at())
        staleness_max = batch.add(self.staleness_max())
        remote_state_version = batch.add(self.remote_state_version())
        has_active_command = batch.add(self.has_active_command())
        pending_transfer_count = batch.add(self.pending_transfer_count())
        batch.run()
        return LaneHubState(
            settled_remote_balance=settled_remote_balance.value,
            outbound_in_flight=outbound_in_flight.value,
            idle_ledger=idle_ledger.value,
            dispatcher_ready=dispatcher_ready.value,
            chain_blocked=chain_blocked.value,
            last_approved_observed_at=last_approved_observed_at.value,
            staleness_max=staleness_max.value,
            remote_state_version=remote_state_version.value,
            has_active_command=has_active_command.value,
            pending_transfer_count=pending_transfer_count.value,
        )

    def read_observation(self) -> LaneObservation:
        """:meth:`observation` run on ``spoke_ctx``."""
        return self.observation().call(self.spoke_ctx)

    # ── attestation ───────────────────────────────────────────────────────

    def attestation(
        self,
        observation: LaneObservation,
        *,
        remote_block: int,
        remote_timestamp: Timestamp,
        expiry: Timestamp,
    ) -> BalanceObservation:
        """The proposal for ``observation``, read on the spoke at
        ``remote_block`` / ``remote_timestamp`` and valid on the hub until
        ``expiry``: the dispatcher's accounted balance marked at its state
        version, which must equal ``remote_state_version`` when proposed and
        when approved."""
        return BalanceObservation(
            chain_id=self.spoke_chain_id,
            settled_balance=observation.accounted_balance,
            state_version=observation.state_version,
            remote_block=remote_block,
            remote_timestamp=remote_timestamp,
            expiry=expiry,
            tracked_position_set_hash=observation.tracked_position_set_hash,
        )

    def needs_attestation(self, *, now: Timestamp, margin: Period = _NO_MARGIN) -> bool:
        """Whether a keeper should propose now (up to seven reads on the hub):
        the settled bucket is non-zero, no transfer or command receipt is in
        flight, and the approved observation is missing or older than
        ``staleness_max - margin`` seconds, after which ``getBalance()`` fails
        closed. A parked failed command does not block attestation because it
        cannot change the remote state version without first being retried or
        cancelled. A receipt that never arrives (a stuck command or transfer)
        keeps this false; resolve it on the executor first."""
        if self.settled_remote_balance().call(self.hub_ctx) == 0:
            return False
        if self.transfer_in_flight():
            return False
        if self.command_in_flight():
            return False
        last = self.last_approved_observed_at().call(self.hub_ctx)
        return (
            last == 0 or now - last > self.staleness_max().call(self.hub_ctx) - margin
        )

    def approve_balance(self, proposal_id: ProposalId) -> Call[None]:
        """``BALANCE_APPROVER`` only."""
        return self.executor.approve_balance(proposal_id)

    def proposal_id_from_logs(self, logs: Iterable[Mapping]) -> ProposalId:
        """The id of the proposal this lane's executor emitted in ``logs``
        (a receipt's or a simulated call's), from its ``BalanceProposed``."""
        for log in logs:
            if (
                log_topic0(log) == self.BALANCE_PROPOSED_TOPIC
                and log_address(log) == self.executor_address
            ):
                (proposal_id,) = decode(["uint256"], log_bytes(log["data"])[:32])
                return ProposalId(int(proposal_id))
        raise ValueError("no BalanceProposed log from the executor")

    def proposal_id_from_receipt(self, receipt: Mapping) -> ProposalId:
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
    def staleness_max(self) -> Call[Period]:
        """Seconds an approved observation keeps the settled bucket in NAV
        (``STALENESS_MAX`` on Stargate, ``BALANCE_STALENESS_MAX`` on CCIP)."""

    @abstractmethod
    def remote_state_version(self) -> Call[int]:
        """The dispatcher state version the executor has acknowledged: what an
        attestation must be observed at."""

    @abstractmethod
    def has_active_command(self) -> Call[bool]:
        """Whether a command still occupies the lane's single command slot."""

    @abstractmethod
    def command_in_flight(self) -> bool:
        """Whether a command receipt that can change the remote state version
        is outstanding (read on ``hub_ctx``). A parked failed command is not in
        flight."""

    @abstractmethod
    def pending_transfer_count(self) -> Call[int]:
        """Raw executor counter: outbound transfers on CCIP; outbound and
        return records on Stargate. Use ``transfer_in_flight`` for either leg."""

    @abstractmethod
    def transfer_in_flight(self) -> bool:
        """Whether an outbound transfer or recall is unresolved.

        Reads the hub executor on ``hub_ctx``; CCIP needs a separate active-return read
        because its pending counter covers only outbound transfers.
        """

    @abstractmethod
    def observation(self) -> Call[LaneObservation]:
        """The dispatcher's canonical snapshot: run it on ``spoke_ctx``."""

    @abstractmethod
    def propose_balance(
        self, observation: BalanceObservation
    ) -> Call[ProposalId | None]:
        """``BALANCE_PROPOSER`` only, sent on ``hub_ctx``. Returns the proposal id where the
        transport's function does (CCIP); Stargate emits it only, see
        ``proposal_id_from_logs``."""

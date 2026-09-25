"""Multi-chain simulation: one ``VaultSimulator`` per chain plus a relay that
replays every crosschain message a chain emits as inbound calls on its
destination, until no message is left in flight.

``eth_simulateV1`` state does not survive a ``run()``, so every relay round
re-runs each chain from its pinned block with its full call list: the calls
queued by the test plus every delivery appended so far. Runs are
deterministic, so message ids repeat across rounds and each message is
delivered once. The pinned blocks of the chains should be close in wall-clock
time: the contracts compare timestamps across chains (command TTLs, attestation
staleness).

Example -- bridge from a source vault and let the dispatcher settle::

    sim = CrosschainSimulator(transport)
    src = sim.add_chain(1, web3_eth, block=ETH_BLOCK, vault=VAULT, alpha=ALPHA)
    sim.add_chain(8453, web3_base, block=BASE_BLOCK)
    sim.fund_native(8453, DISPATCHER, 10**17)   # the dispatcher pays its receipts
    src.execute([supply_fuse.enter(...)])
    sim.observe(1, "settled", executor.settled_remote_balance(8453))
    results = sim.relay()
    assert results[1].get("settled") > 0
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import BlockIdentifier

from ipor_fusion.core.contract import Call
from ipor_fusion.core.simulation import SimulationResult, VaultSimulator
from ipor_fusion.crosschain.transport import CrosschainTransport, OutboundMessage
from ipor_fusion.fuses.base import ZERO_ADDRESS
from ipor_fusion.types import ChainId


class CrosschainSimulator:
    """Relay-driven simulation over several chains sharing one transport."""

    def __init__(self, transport: CrosschainTransport) -> None:
        self._transport = transport
        self._chains: dict[ChainId, VaultSimulator] = {}
        self._results: dict[ChainId, SimulationResult] = {}
        self._delivered: dict[bytes, OutboundMessage] = {}

    @property
    def transport(self) -> CrosschainTransport:
        return self._transport

    @property
    def results(self) -> Mapping[ChainId, SimulationResult]:
        """The latest ``run()`` result per chain, after ``relay()``."""
        return self._results

    @property
    def delivered(self) -> list[OutboundMessage]:
        """Every message delivered so far, in delivery order."""
        return list(self._delivered.values())

    def add_chain(
        self,
        chain_id: ChainId,
        web3: Web3,
        *,
        block: BlockIdentifier = "latest",
        vault: ChecksumAddress | None = None,
        alpha: ChecksumAddress | None = None,
    ) -> VaultSimulator:
        """Register a chain and return its simulator. ``vault`` and ``alpha``
        are needed only where ``execute`` batches run (the source vault's
        chain); a destination chain takes neither."""
        if chain_id not in self._transport.chain_ids:
            raise ValueError(f"chain {chain_id} is not configured on the transport")
        if chain_id in self._chains:
            raise ValueError(f"chain {chain_id} already added")
        zero: Any = ZERO_ADDRESS
        sim = VaultSimulator(
            web3, vault=vault or zero, alpha=alpha or zero, block=block
        )
        self._chains[chain_id] = sim
        return sim

    def chain(self, chain_id: ChainId) -> VaultSimulator:
        return self._chains[chain_id]

    def fund_native(
        self, chain_id: ChainId, address: ChecksumAddress, wei: int
    ) -> None:
        """Override ``address``'s native balance on ``chain_id``. Executors and
        dispatchers pay bridge fees from their own balance, and the live ones
        are usually near empty."""
        self._chains[chain_id].with_state_override(address, balance=hex(wei))

    def observe(self, chain_id: ChainId, label: str, call: Call) -> None:
        """Queue a labelled read on ``chain_id`` (see ``VaultSimulator.observe``)."""
        self._chains[chain_id].observe(label, call)

    def relay(self, max_rounds: int = 8) -> dict[ChainId, SimulationResult]:
        """Run every chain and deliver messages until nothing is in flight.

        Raises ``RuntimeError`` if messages are still being produced after
        ``max_rounds`` rounds. A delivery that reverts stays in the destination
        call list and surfaces through that chain's ``SimulationResult``.
        """
        pending = {cid for cid, sim in self._chains.items() if sim.has_calls}
        for _ in range(max_rounds):
            if not pending:
                return dict(self._results)
            pending = self._run_round(pending)
        raise RuntimeError(f"relay did not settle within {max_rounds} rounds")

    def _run_round(self, chain_ids: set[ChainId]) -> set[ChainId]:
        touched: set[ChainId] = set()
        for chain_id in sorted(chain_ids):
            result = self._chains[chain_id].run()
            self._results[chain_id] = result
            logs = [log for call in result.calls for log in call.logs]
            for message in self._transport.outbound_messages(chain_id, logs):
                if message.message_id in self._delivered:
                    continue
                self._deliver(message)
                touched.add(message.dst_chain_id)
        return touched

    def _deliver(self, message: OutboundMessage) -> None:
        destination = self._chains.get(message.dst_chain_id)
        if destination is None:
            raise RuntimeError(
                f"message {message.message_id.hex()} targets chain "
                f"{message.dst_chain_id}, which was not added to the simulator"
            )
        for step in self._transport.delivery_calls(message):
            destination.add_call(
                Call(to=step.to, data=step.data), from_=step.from_, label=step.label
            )
        self._delivered[message.message_id] = message

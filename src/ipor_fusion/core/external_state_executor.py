"""Per-vault ExternalStateExecutor wrapper (external-state market NAV mark).

The executor is deployed once per vault and holds the off-vault capital tracked
by the external-state market. Its address lives in the VAULT's ERC-7201 storage
(no public getter exists on-chain), so `for_vault` reads it directly.

NAV for the external-state market is marked by a dual-custodian propose/confirm
on this executor, then a `update_markets_balances([EXTERNAL_STATE])` refresh on
the vault. `mark_nav` runs that whole sequence in one process; a SEPARATED
confirm service instead reads `pending_proposal` and confirms the hash it
returns, never guessing the executor's global nonce. `parse_balance_proposed`
decodes the matching event for audit and for verifying a hash computed offline.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.fuses.base import ZERO_ADDRESS
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import Amount, ChainId, MarketId

if TYPE_CHECKING:
    from ipor_fusion.core.plasma_vault import PlasmaVault


def _log_bytes(value: Any) -> bytes:
    """Normalize one log field to bytes.

    Receipts carry `topics` and `data` as HexBytes; `eth_simulateV1` and raw
    JSON-RPC carry them as `0x`-hex strings. Both shapes reach the public
    parsers, so neither can be assumed."""
    if isinstance(value, str):
        return bytes.fromhex(value.removeprefix("0x"))
    return bytes(value)


@dataclass(frozen=True, slots=True)
class BalanceProposal:
    """A dual-custodian balance proposal, from either route that yields one.

    `parse_balance_proposed` / `find_balance_proposed` build it from the
    `BalanceProposed` event; `pending_proposal` builds it from the executor's
    pending slot. `proposal_hash` is populated on both -- the event carries it,
    and the state route recomputes it from the same fields -- so it is always
    the value `confirm_balance` expects.
    """

    balance_account: ChecksumAddress
    proposer: ChecksumAddress
    value: Amount
    nonce: int
    proposed_at: int
    proposal_hash: bytes


@dataclass(frozen=True, slots=True)
class NavMark:
    """Outcome of a `mark_nav` run: the confirmed proposal plus every receipt.

    `refresh_receipt` is `None` when `mark_nav` was called without a `vault`
    (propose + confirm only, no cached-NAV refresh).
    """

    proposed_at: int
    nonce: int
    proposal_hash: bytes
    propose_receipt: TxReceipt
    confirm_receipt: TxReceipt
    refresh_receipt: TxReceipt | None


class ExternalStateExecutor(ContractWrapper):
    """Per-vault ExternalStateExecutor: NAV propose/confirm plus its reads.

    Use `for_vault` when you have only the vault address -- it resolves the
    executor from the vault's ERC-7201 storage. Construct directly,
    `ExternalStateExecutor(ctx, executor_address)`, when the executor address is
    already known (config, a cached resolution, a deploy event), to skip the
    storage read.
    """

    # ERC-7201 slot `io.ipor.externalState.Executor`; `executor` is the struct's
    # first field, so it sits at the base slot. No public getter exists on-chain,
    # so resolution reads this slot directly. Mirrors the contract's own
    # pre-computed constant; a unit test re-derives it from the namespace.
    _EXECUTOR_STORAGE_SLOT = (
        0x1781023874512EC457C16827AD102F41A5C5CE1CD7BA8AA8FCD2DA52541D8A00
    )

    # topic0 of `proposeBalance`'s event; its non-indexed data carries the exact
    # nonce, proposedAt, and proposalHash the contract stored -- `mark_nav` reads
    # them from the propose receipt rather than re-querying (race-free).
    _BALANCE_PROPOSED_TOPIC = Web3.keccak(
        text="BalanceProposed(address,address,uint256,uint256,uint64,bytes32)"
    )

    @classmethod
    def for_vault(
        cls, ctx: Web3Context, vault_address: ChecksumAddress
    ) -> ExternalStateExecutor:
        """Resolve the per-vault executor from vault storage and wrap it.

        Raises `ValueError` when no executor is deployed yet (the slot reads
        zero) rather than returning a wrapper around the zero address, and lets
        `decode` reject a slot whose high 12 bytes are non-zero (a wrong or
        corrupt slot, not an address-in-slot).
        """
        # Left-pad so an RPC that strips leading zero bytes still decodes; the
        # address is the slot's low 20 bytes, `decode` verifies the rest is zero.
        raw = bytes(
            ctx.get_storage_at(
                Web3.to_checksum_address(vault_address), cls._EXECUTOR_STORAGE_SLOT
            )
        ).rjust(32, b"\x00")
        executor = Web3.to_checksum_address(decode(["address"], raw)[0])
        if executor == ZERO_ADDRESS:
            raise ValueError(
                f"no ExternalStateExecutor deployed for vault {vault_address}"
            )
        return cls(ctx, executor)

    def balances(self, balance_account: ChecksumAddress) -> Call[Amount]:
        """Underlying-unit balance the executor tracks for `balance_account`.

        The figure the custodians last confirmed (or that an enter/exit last
        adjusted), not an on-chain token balance -- the executor holds no tokens
        between operations. This is what the balance fuse aggregates into the
        external-state market's value."""
        return self._view(
            "balances(address)",
            balance_account,
            output_types=["uint256"],
            decoder=Amount,
        )

    def nonce(self) -> Call[int]:
        """Monotonic proposal nonce, incremented on every `proposeBalance`.

        Read it AFTER a propose to bind that proposal's hash (the contract does
        `++nonce` before storing the pending proposal)."""
        return self._view("nonce()", output_types=["uint256"])

    def pending_proposal(
        self, balance_account: ChecksumAddress
    ) -> Call[BalanceProposal | None]:
        """The proposal awaiting confirmation for `balance_account`, or `None`.

        This is the read a SEPARATED confirm service wants: it returns the
        CURRENT pending proposal with the hash `confirm_balance` will verify, so
        custodian B needs nothing from custodian A beyond "go look". Computing
        the hash instead from `proposal_hash(...)` plus a fresh `nonce()` is
        unsound -- `nonce` is executor-global, so another account's proposal in
        between silently yields the wrong hash.

        `None` means no proposal is pending: a successful `confirm_balance`
        DELETES the slot, so this is the normal state between marks, not an
        error. The hash is bound to this wrapper's chain, so the wrapper needs a
        `Web3Context` (an `encoder()` instance raises).
        """
        account = Web3.to_checksum_address(balance_account)
        chain_id = self._require_chain_id()

        def to_proposal(values: tuple[int, str, int, int]) -> BalanceProposal | None:
            value, proposer, proposed_at, nonce = values
            if Web3.to_checksum_address(proposer) == ZERO_ADDRESS:
                return None
            return BalanceProposal(
                balance_account=account,
                proposer=Web3.to_checksum_address(proposer),
                value=Amount(value),
                nonce=nonce,
                proposed_at=proposed_at,
                proposal_hash=self.proposal_hash(
                    executor=self._address,
                    chain_id=chain_id,
                    balance_account=account,
                    value=Amount(value),
                    proposer=Web3.to_checksum_address(proposer),
                    proposed_at=proposed_at,
                    nonce=nonce,
                ),
            )

        # PendingProposal struct fields in declaration order; the auto-getter
        # returns them flattened, not as a tuple type.
        return self._view(
            "pendingProposals(address)",
            account,
            output_types=["uint256", "address", "uint64", "uint256"],
            decoder=to_proposal,
        )

    def _require_chain_id(self) -> ChainId:
        """The wrapper's chain, or a clear error on a ctx-less `encoder()`."""
        if self._ctx is None:
            raise ValueError(
                "Web3Context required: the proposal hash is bound to a chain id, "
                "so build this wrapper with a ctx rather than via encoder()."
            )
        return self._ctx.chain_id

    def propose_balance(
        self, balance_account: ChecksumAddress, value: Amount
    ) -> Call[None]:
        """CUSTODIAN-only: propose `value` (underlying units) as the new tracked
        balance of `balance_account`. Confirm it from a *different* custodian
        with `confirm_balance`; see `mark_nav` for the full sequence."""
        return self._write("proposeBalance(address,uint256)", balance_account, value)

    def confirm_balance(
        self, balance_account: ChecksumAddress, proposal_hash: bytes
    ) -> Call[None]:
        """CUSTODIAN-only: confirm the pending proposal for `balance_account`.
        Must be sent by a custodian other than the proposer, and `proposal_hash`
        must equal `proposal_hash(...)` over the propose tx's timestamp and the
        post-propose nonce."""
        return self._write(
            "confirmBalance(address,bytes32)", balance_account, proposal_hash
        )

    @staticmethod
    def proposal_hash(
        *,
        executor: ChecksumAddress,
        chain_id: ChainId,
        balance_account: ChecksumAddress,
        value: Amount,
        proposer: ChecksumAddress,
        proposed_at: int,
        nonce: int,
    ) -> bytes:
        """The confirm hash that `confirmBalance` verifies against:
        ``keccak(abi.encode(executor, chainId, balanceAccount, value, proposer,
        proposedAt, nonce))``.

        `proposed_at` is the propose tx's block timestamp; `nonce` is `nonce()`
        read after the propose. Pure -- no chain access -- so callers can
        pre-compute or verify a hash offline.
        """
        return keccak(
            encode(
                [
                    "address",
                    "uint256",
                    "address",
                    "uint256",
                    "address",
                    "uint64",
                    "uint256",
                ],
                [
                    Web3.to_checksum_address(executor),
                    int(chain_id),
                    Web3.to_checksum_address(balance_account),
                    int(value),
                    Web3.to_checksum_address(proposer),
                    int(proposed_at),
                    int(nonce),
                ],
            )
        )

    @classmethod
    def parse_balance_proposed(cls, log: Mapping[str, Any]) -> BalanceProposal:
        """Decode one `BalanceProposed` log into a `BalanceProposal`.

        Accepts receipt-shaped logs (HexBytes) and JSON-RPC / `eth_simulateV1`
        -shaped logs (`0x`-hex strings) alike, so it also decodes rows from an
        event index. Needs no chain access.

        Checks topic0 only: it does NOT verify which contract emitted the log,
        since any contract may emit this signature. Verify the emitter yourself,
        or use `find_balance_proposed`, which does.
        """
        topics = log.get("topics") or ()
        if not topics or _log_bytes(topics[0]) != cls._BALANCE_PROPOSED_TOPIC:
            raise ValueError("log is not a BalanceProposed event")
        # data fields in declaration order; all six are unindexed, so topics
        # carries topic0 alone.
        account, proposer, value, nonce, proposed_at, proposal_hash = decode(
            ["address", "address", "uint256", "uint256", "uint64", "bytes32"],
            _log_bytes(log["data"]),
        )
        return BalanceProposal(
            balance_account=Web3.to_checksum_address(account),
            proposer=Web3.to_checksum_address(proposer),
            value=Amount(value),
            nonce=nonce,
            proposed_at=proposed_at,
            proposal_hash=proposal_hash,
        )

    def find_balance_proposed(
        self, logs: Iterable[Mapping[str, Any]]
    ) -> BalanceProposal:
        """The first `BalanceProposed` log THIS executor emitted in `logs`.

        Takes any iterable of logs, so it serves `receipt["logs"]`, a simulated
        call's `logs`, and `get_logs` output alike. Unlike
        `parse_balance_proposed` it matches on the emitting address, so a
        same-signature event from another contract cannot be mistaken for ours.

        Reading a proposal from the propose transaction's own logs is race-free
        by construction; `pending_proposal` is the route for a service that did
        not send that transaction.
        """
        for log in logs:
            topics = log.get("topics") or ()
            if (
                topics
                and _log_bytes(topics[0]) == self._BALANCE_PROPOSED_TOPIC
                and Web3.to_checksum_address(log["address"]) == self._address
            ):
                return self.parse_balance_proposed(log)
        raise ValueError(f"no BalanceProposed log from executor {self._address}")

    def mark_nav(
        self,
        *,
        value: Amount,
        balance_account: ChecksumAddress,
        proposer_ctx: Web3Context,
        confirmer_ctx: Web3Context,
        vault: PlasmaVault | None = None,
    ) -> NavMark:
        """Mark the external-state NAV: propose `value` (custodian A), confirm it
        (custodian B), and optionally refresh the vault's cached NAV.

        `proposer_ctx` signs the propose; `confirmer_ctx` signs the confirm (and,
        when `vault` is given, the `update_markets_balances([EXTERNAL_STATE])`
        refresh). The two signers MUST be different custodians. The confirm hash,
        nonce, and timestamp are taken from the propose tx's `BalanceProposed`
        event -- exactly what the contract stored, so no read can race the
        executor's global nonce. Returns a `NavMark` with the proposal and every
        receipt.

        The marked balance is valued into the vault's `totalAssets` via the
        external-state market (50), so it moves the share price; the refresh is
        what propagates the new value into the vault's cached NAV.

        Sends up to three transactions and blocks on each; see `NavMark`.

        Security: mark_nav composes `propose_balance` + `confirm_balance`, so it
        needs both custodian keys in one process and does not preserve the
        dual-custodian separation. When that separation is the point, run the two
        primitives from separate services instead.
        """
        proposer = proposer_ctx.signer
        confirmer = confirmer_ctx.signer
        if proposer is None:
            raise ValueError("proposer_ctx must have a signer")
        if confirmer is None:
            raise ValueError("confirmer_ctx must have a signer")
        if Web3.to_checksum_address(proposer) == Web3.to_checksum_address(confirmer):
            raise ValueError("proposer and confirmer must be different custodians")

        propose_receipt = self.propose_balance(balance_account, value).send(
            proposer_ctx
        )
        proposal = self.find_balance_proposed(propose_receipt["logs"])
        confirm_receipt = self.confirm_balance(
            balance_account, proposal.proposal_hash
        ).send(confirmer_ctx)

        refresh_receipt: TxReceipt | None = None
        if vault is not None:
            refresh_receipt = vault.update_markets_balances(
                [MarketId(IporFusionMarkets.EXTERNAL_STATE)]
            ).send(confirmer_ctx)

        return NavMark(
            proposed_at=proposal.proposed_at,
            nonce=proposal.nonce,
            proposal_hash=proposal.proposal_hash,
            propose_receipt=propose_receipt,
            confirm_receipt=confirm_receipt,
            refresh_receipt=refresh_receipt,
        )

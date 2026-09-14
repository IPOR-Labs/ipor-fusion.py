"""Per-vault ExternalStateExecutor wrapper (external-state market NAV mark).

The executor is deployed once per vault and holds the off-vault capital tracked
by the external-state market. Its address lives in the VAULT's ERC-7201 storage
(no public getter exists on-chain), so `for_vault` reads it directly.

NAV for the external-state market is marked by a dual-custodian propose/confirm
on this executor, then a `update_markets_balances([EXTERNAL_STATE])` refresh on
the vault. `mark_nav` runs that whole sequence in one process; a SEPARATED
confirm service instead reads `pending_proposals` and confirms the hash it
returns, never guessing the executor's global nonce. `parse_balance_proposed`
decodes the matching event for audit and for verifying a hash computed offline.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Any

from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_typing import ChecksumAddress
from eth_utils import keccak
from hexbytes import HexBytes
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.fuses.base import ZERO_ADDRESS
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import Amount, ChainId, MarketId

if TYPE_CHECKING:
    from ipor_fusion.core.plasma_vault import PlasmaVault


# The log shapes the event readers below accept: a receipt log from web3.py
# (HexBytes fields, checksummed address), and a raw JSON-RPC or
# `eth_simulateV1` row (0x-hex strings, lowercase address). Looser than
# `web3.types.LogReceipt`, which the rest of `core/` uses, because those rows
# carry neither its field types nor all of its required keys.
RawLog = Mapping[str, Any]


def _log_emitter(log: RawLog) -> ChecksumAddress | None:
    """The contract that emitted `log`, or `None` if that cannot be established.

    Missing and unreadable are the same answer on purpose: neither can be
    matched against an expected emitter, and a caller scanning foreign rows
    should skip such a log rather than have the scan abort on it."""
    emitter = log.get("address")
    if emitter is None:
        return None
    try:
        return Web3.to_checksum_address(emitter)
    except (ValueError, TypeError):
        return None


@dataclass(frozen=True, slots=True)
class BalanceProposal:
    """A dual-custodian balance proposal, from any of the three routes.

    `parse_balance_proposed` / `find_balance_proposed` build it from the
    `BalanceProposed` event; `pending_proposals` builds it from the executor's
    pending slot. Every field is populated on all three routes.

    Every route binds `proposal_hash` to one executor -- the state route
    recomputes it, and both event routes accept only logs that executor
    emitted -- so it is the value `confirm_balance` expects.
    """

    balance_account: ChecksumAddress
    proposer: ChecksumAddress
    value: Amount
    nonce: int
    proposed_at: int
    proposal_hash: bytes


def _hash_of(
    proposal: BalanceProposal, *, executor: ChecksumAddress, chain_id: ChainId
) -> bytes:
    """`proposal`'s canonical hash, as `confirmBalance` recomputes it.

    The sole mapping from a proposal's fields onto `proposal_hash` arguments,
    so the state route and the event verifier cannot drift apart on it."""
    return ExternalStateExecutor.proposal_hash(
        executor=executor,
        chain_id=chain_id,
        balance_account=proposal.balance_account,
        value=proposal.value,
        proposer=proposal.proposer,
        proposed_at=proposal.proposed_at,
        nonce=proposal.nonce,
    )


def _require_matching_hash(
    proposal: BalanceProposal, *, executor: ChecksumAddress, chain_id: ChainId
) -> None:
    """Raise unless `proposal_hash` is the hash of `proposal`'s own fields.

    A payload whose value and hash disagree is exactly what `confirmBalance`
    accepts against a DIFFERENT proposal's slot, so every route that trusts a
    hash checks it here rather than restating the comparison.

    A mismatch has two causes the comparison cannot tell apart -- a lifted hash,
    or the right hash recomputed under the wrong executor or chain id -- so the
    message names both rather than accusing the payload."""
    if proposal.proposal_hash != _hash_of(
        proposal, executor=executor, chain_id=chain_id
    ):
        raise ValueError(
            "proposal hash does not match its own fields: it pairs this value "
            "with another proposal's hash, or it was computed for a different "
            "executor or chain id"
        )


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

        Executor-global, so any other balance account's propose advances it:
        reading it to reconstruct a proposal's hash races them. Take the nonce
        from `pending_proposals` or from the `BalanceProposed` event, both of
        which report the one the contract stored. This read is for observing
        the counter itself."""
        return self._view("nonce()", output_types=["uint256"])

    def pending_proposals(
        self,
        balance_account: ChecksumAddress,
        *,
        chain_id: ChainId | None = None,
    ) -> Call[BalanceProposal | None]:
        """The proposal awaiting confirmation for `balance_account`, or `None`.

        The read a SEPARATED confirm service needs: it returns the CURRENT
        pending proposal carrying the hash `confirm_balance` verifies, so
        custodian B never has to obtain it from custodian A. Deriving that hash
        instead from `proposal_hash(...)` plus a fresh `nonce()` is unsound --
        `nonce` is executor-global, so any other account's proposal in between
        silently yields the wrong one.

        Security: the returned hash is plumbing, not authorization. Confirming
        asserts that a SECOND custodian independently agrees with `value`, and
        that value flows into the vault's `totalAssets` and share price. The one
        thing the chain cannot check is whether `value` is true -- nothing
        on-chain sees the capital it claims -- so re-derive it from your own
        source before confirming. That agreement is the entire content of the
        second signature; confirming whatever happens to be pending reduces the
        two-custodian control to a one-custodian one.

        `confirmBalance` rejects a same-signer confirm and a proposal older than
        `stalenessMax` on its own, so checking `proposer` and `proposed_at`
        beforehand saves a reverted transaction rather than adding safety.

        `None` means nothing is pending: a successful `confirm_balance` DELETES
        the slot, so that is the normal state between marks, not an error.
        Under `VaultSimulator.observe` that sentinel is ambiguous -- the
        simulator reports a failed decode as `None` too -- so distinguish the
        two with `.call()` rather than an observed read.

        The hash is bound to a chain. `chain_id` defaults to this wrapper's
        context; pass it explicitly when the context that executes the call is
        not the one the wrapper was built with (`Call.call(other_ctx)`), or to
        read through a ctx-less `encoder()` instance.
        """
        account = Web3.to_checksum_address(balance_account)

        def to_proposal(values: tuple[int, str, int, int]) -> BalanceProposal | None:
            # Both hash inputs first, before the empty-slot return. Resolved
            # inside the decoder so a calldata-only `encoder()` wrapper keeps
            # working like every sibling, but ahead of the sentinel so a
            # misconfigured poller fails on its first read rather than on the
            # first read that happens to find a proposal.
            executor = self._require_address()
            resolved_chain = self._require_chain_id(chain_id)
            value, raw_proposer, proposed_at, nonce = values
            proposer = Web3.to_checksum_address(raw_proposer)
            if proposer == ZERO_ADDRESS:
                return None
            # Built hash-less, then stamped, so the field mapping lives in
            # `_hash_of` alone rather than being repeated here.
            unstamped = BalanceProposal(
                balance_account=account,
                proposer=proposer,
                value=Amount(value),
                nonce=nonce,
                proposed_at=proposed_at,
                proposal_hash=b"",
            )
            return replace(
                unstamped,
                proposal_hash=_hash_of(
                    unstamped, executor=executor, chain_id=resolved_chain
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

    def _require_address(self) -> ChecksumAddress:
        """This executor's address, or a clear error on an address-less
        `encoder()`.

        The other half of the hash's binding, and the placeholder zero address
        is not a usable stand-in for it: it stamps a hash bound to no contract,
        and it matches only a log claiming to come from none."""
        if self._address == ZERO_ADDRESS:
            raise ValueError(
                "executor address required: the proposal hash binds it and the "
                "event readers match it against a log's emitter, so build this "
                "wrapper with an address rather than a bare encoder()."
            )
        return self._address

    def _require_chain_id(self, override: ChainId | None = None) -> ChainId:
        """`override` if the caller named one, else the wrapper's chain, else a
        clear error on a ctx-less `encoder()`."""
        if override is not None:
            return override
        if self._ctx is None:
            raise ValueError(
                "Web3Context required: the proposal hash is bound to a chain id, "
                "so build this wrapper with a ctx rather than via encoder(), "
                "or pass chain_id= to this method."
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
        must be the hash the contract stored -- take it from `pending_proposals`
        or the `BalanceProposed` event, which report the nonce and timestamp it
        was built from."""
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

        `proposed_at` is the propose tx's block timestamp; `nonce` is the one
        the contract stored, from `pending_proposals` or the `BalanceProposed`
        event. The counter is executor-global, so reading `nonce()` after the
        propose races any other account's. Pure -- no chain access -- so
        callers can pre-compute or verify a hash offline.
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

    def _emitted_by_us(self, log: RawLog) -> bool:
        """Whether this executor emitted `log`.

        An address that is missing or not parseable cannot be ours, so it reads
        as a foreign log rather than aborting a scan over someone else's rows."""
        return _log_emitter(log) == self._address

    @classmethod
    def _is_balance_proposed(cls, log: RawLog) -> bool:
        """Whether `log` carries this event's topic0. Sole signature check.

        `HexBytes` absorbs the wire-shape difference: receipts give topics as
        HexBytes, `eth_simulateV1` and raw JSON-RPC as `0x`-hex strings. A
        topic that will not convert cannot be ours, so it reads as a different
        event -- the rule `_log_emitter` already applies to an unreadable
        address, and it keeps a raise meaning "ours, but the payload is broken".
        """
        topics = log.get("topics") or ()
        if not topics:
            return False
        try:
            return HexBytes(topics[0]) == cls._BALANCE_PROPOSED_TOPIC
        except (ValueError, TypeError):
            return False

    def parse_balance_proposed(
        self, log: RawLog, *, chain_id: ChainId | None = None
    ) -> BalanceProposal:
        """Decode one `BalanceProposed` log this executor emitted, hash-verified.

        Accepts receipt-shaped logs (HexBytes) and JSON-RPC / `eth_simulateV1`
        -shaped logs (`0x`-hex strings) alike, so it also decodes rows from an
        event index. Needs no chain access. Raises `ValueError` for a log that
        is not this event, was not emitted by this executor, carries a hash that
        is not the hash of its own fields, or whose data is malformed -- and for
        a call with no chain id available at all; `TypeError` for a field that
        is not hex data at all.

        Security: two checks, and both are needed. This executor must have
        emitted the log, and `proposal_hash` must be the hash of the log's own
        other fields. The emitter check alone would admit a log pairing a modest
        `value` with the `proposal_hash` of a DIFFERENT, larger proposal;
        validating `value` from such a log and then handing its `proposal_hash`
        to `confirm_balance` attests to a value you never saw, and it does NOT
        revert -- the on-chain check recomputes from the pending slot, which the
        lifted hash matches. The hash check alone would admit a forgery from any
        contract, since `proposal_hash` is public and pure, so a self-consistent
        payload naming any executor is trivial to build.

        A log whose address is missing or unreadable therefore fails: it cannot
        be attributed, which is not the same as being attributable to someone
        else, but is equally not proof it is ours.

        The hash is bound to a chain. `chain_id` defaults to this wrapper's
        context; a caller with no context -- decoding indexer or `get_logs`
        rows -- reaches this through `encoder(address)` and passes `chain_id`
        explicitly. Either way the whole path is pure, with no RPC.

        `find_balance_proposed` applies the same emitter check while scanning.
        """
        expected_emitter = self._require_address()
        if not self._is_balance_proposed(log):
            raise ValueError("log is not a BalanceProposed event")
        if not self._emitted_by_us(log):
            raise ValueError(
                f"BalanceProposed log was not emitted by {expected_emitter}"
            )
        proposal = self._decode_balance_proposed(log)
        _require_matching_hash(
            proposal,
            executor=self._address,
            chain_id=self._require_chain_id(chain_id),
        )
        return proposal

    @classmethod
    def _decode_balance_proposed(cls, log: RawLog) -> BalanceProposal:
        """Decode a log whose topic0 is already known to be this event."""
        data = log.get("data")
        if data is None:
            raise ValueError("BalanceProposed log carries no data")
        try:
            # Fields in the order `BalanceProposed` declares them in
            # ExternalStateExecutor.sol; all six are unindexed, so topics
            # carries topic0 alone.
            account, proposer, value, nonce, proposed_at, proposal_hash = decode(
                ["address", "address", "uint256", "uint256", "uint64", "bytes32"],
                HexBytes(data),
            )
        except (DecodingError, ValueError) as exc:
            raise ValueError(f"malformed BalanceProposed data: {exc}") from exc
        return BalanceProposal(
            balance_account=Web3.to_checksum_address(account),
            proposer=Web3.to_checksum_address(proposer),
            value=Amount(value),
            nonce=nonce,
            proposed_at=proposed_at,
            proposal_hash=proposal_hash,
        )

    def find_balance_proposed(
        self,
        logs: Iterable[RawLog],
        balance_account: ChecksumAddress | None = None,
    ) -> BalanceProposal:
        """The last `BalanceProposed` log THIS executor emitted among `logs`.

        Built for the logs of ONE call -- a propose receipt's `receipt["logs"]`,
        or a simulated call's `logs`. Like `parse_balance_proposed` it matches
        on the emitting address, so a same-signature event from another contract
        cannot be mistaken for ours and the hash it returns is one this executor
        computed; unlike it, a log that is not ours is skipped rather than
        raising, since scanning a batch is the point.

        The LAST match wins because `proposeBalance` OVERWRITES the pending
        slot: where several proposals for one account appear, only the newest
        can still be confirmed, and logs arrive oldest first.

        Pass `balance_account` when the logs may span several accounts, so the
        match is the one you proposed for rather than whichever came last.

        Over a `get_logs` range this still answers a different question than
        `pending_proposals`: a proposal may have been confirmed since, which
        deletes the slot but leaves the event behind. To read every proposal,
        filter the range by this executor's address and map
        `parse_balance_proposed` over it -- that one RAISES on a foreign log
        where this scan skips, and it additionally verifies each hash against
        its own log's fields, which a scan for the newest match does not need.

        Logs that are not this event, or do not come from this executor, are
        skipped -- including ones whose address is missing or unreadable, since
        neither can be ours. One of OURS that fails to decode raises instead,
        aborting the scan: on a propose receipt that is a broken assumption
        worth surfacing. Weigh it over a batch of index rows, where one bad
        payload costs the scan. Decoding runs before the `balance_account`
        filter, so one of ours that will not decode surfaces whichever account
        you asked for.
        """
        expected_emitter = self._require_address()
        wanted = (
            Web3.to_checksum_address(balance_account)
            if balance_account is not None
            else None
        )
        found: BalanceProposal | None = None
        for log in logs:
            if not self._emitted_by_us(log):
                continue
            if not self._is_balance_proposed(log):
                continue
            proposal = self._decode_balance_proposed(log)
            if wanted is None or proposal.balance_account == wanted:
                found = proposal
        if found is not None:
            return found
        for_account = f" for account {wanted}" if wanted is not None else ""
        raise ValueError(
            f"no BalanceProposed log from executor {expected_emitter}{for_account}"
        )

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

        Sends up to three transactions and blocks on each; see `NavMark`. If the
        propose receipt does not record the value and proposer this call sent,
        or carries a hash that is not the hash of those fields, it raises rather
        than confirming -- note the propose transaction has already landed at
        that point, leaving a pending proposal behind.

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
        proposer_address = Web3.to_checksum_address(proposer)
        if proposer_address == Web3.to_checksum_address(confirmer):
            raise ValueError("proposer and confirmer must be different custodians")
        # The two signing contexts only; the wrapper may legitimately be built
        # from a third, on a chain it never signs from.
        if proposer_ctx.chain_id != confirmer_ctx.chain_id:
            raise ValueError(
                f"proposer_ctx is on chain {proposer_ctx.chain_id} but "
                f"confirmer_ctx is on {confirmer_ctx.chain_id}; both custodians "
                "must sign on the same chain"
            )

        propose_receipt = self.propose_balance(balance_account, value).send(
            proposer_ctx
        )
        proposal = self.find_balance_proposed(propose_receipt["logs"], balance_account)
        # The receipt is data from the node, so confirm only what we asked for:
        # signing off on a proposal that differs from the one just sent would
        # attest a value this call never chose.
        if proposal.value != value:
            raise ValueError(
                f"proposed {value} but the receipt records {proposal.value}; "
                "refusing to confirm"
            )
        if proposal.proposer != proposer_address:
            raise ValueError(
                f"proposal was made by {proposal.proposer}, not {proposer_address}; "
                "refusing to confirm"
            )
        # The hash is what `confirmBalance` authorizes against, so the value and
        # proposer checks above only bind the confirm once the hash is known to
        # be theirs.
        _require_matching_hash(
            proposal, executor=self._address, chain_id=confirmer_ctx.chain_id
        )
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

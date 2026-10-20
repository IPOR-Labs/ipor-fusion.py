"""Per-vault ExternalStateExecutor wrapper (external-state market NAV mark).

The executor is deployed once per vault and holds the off-vault capital tracked
by the external-state market. Its address lives in the VAULT's ERC-7201 storage
(no public getter exists on-chain), so `for_vault` reads it directly.

NAV for the external-state market is marked by a dual-custodian propose/confirm
on this executor, then a `update_markets_balances([EXTERNAL_STATE])` refresh on
the vault. The confirming custodian's service reads `pending_proposals` and
confirms the hash it returns, never guessing the executor's global nonce. The
same proposal reaches an offline reader through the `BalanceProposed` event --
`find_balance_proposed` picks it out of a receipt's logs,
`parse_balance_proposed` decodes one row from an index -- and `balances` and
`last_updated` report what the custodians last confirmed for an account.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass, replace
from typing import Any

from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_typing import ChecksumAddress
from eth_utils import keccak
from hexbytes import HexBytes
from web3 import Web3
from web3.types import BlockIdentifier

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.errors import ExternalStateError
from ipor_fusion.types import (
    ZERO_ADDRESS,
    Amount,
    ChainId,
    PlasmaVaultAddress,
    ProposalHash,
)

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
    proposal_hash: ProposalHash


def _hash_of(
    proposal: BalanceProposal, *, executor: ChecksumAddress, chain_id: ChainId
) -> ProposalHash:
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


def _no_balance_proposed(
    executor: ChecksumAddress, balance_account: ChecksumAddress | None
) -> str:
    for_account = (
        f" for account {Web3.to_checksum_address(balance_account)}"
        if balance_account is not None
        else ""
    )
    return f"no BalanceProposed log from executor {executor}{for_account}"


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
        raise ExternalStateError(
            "proposal hash does not match its own fields: it pairs this value "
            "with another proposal's hash, or it was computed for a different "
            "executor or chain id"
        )


@dataclass(frozen=True, slots=True)
class ExternalStateVaultState:
    """The external-state market's vault-scoped state, read from the vault's
    ERC-7201 namespace ``io.ipor.externalState.Executor``
    (`ExternalStateExecutorStorageLib`) at one block.

    `executor` is the executor the vault deployed (``None`` while none is);
    `last_total_balance` is the balance fuse's cache of the executor's total in
    raw underlying units, as of the fuse's last refresh -- not the live total,
    which `ExternalStateExecutor.get_balance_fuse_snapshot` reads;
    `last_checked_custodian_timestamp` is the executor custodian timestamp the
    balance fuse last processed -- not necessarily the latest custodian update
    and not the pre-hook's staleness clock, which is
    `ExternalStateExecutor.get_oldest_update_timestamp`; `paused` is the flag
    the balance fuse sets on a big change and `ExternalStateUnpauseFuse` clears
    with an atomist signature. `ExternalStatePausePreHook` blocks the vault's
    gated user operations while the flag is set, and also, flag or not, while
    the oldest custodian update is older than `staleness_max` or an unprocessed
    custodian change exceeds `big_change_bps` against this cache. No contract
    exposes these four values as getters.
    """

    executor: ChecksumAddress | None
    last_total_balance: Amount
    last_checked_custodian_timestamp: int
    paused: bool
    block: int


@dataclass(frozen=True, slots=True)
class BalanceFuseSnapshot:
    """`ExternalStateExecutor.getBalanceFuseSnapshot()`: the live sum of the
    tracked balances in raw underlying units, the cached big-change threshold
    in basis points and the timestamp of the last confirmed custodian update."""

    total_balance: Amount
    big_change_bps: int
    last_custodian_update_timestamp: int


def _storage_word(raw: bytes) -> int:
    """A storage slot as an unsigned 256-bit integer: an RPC may strip leading
    zero bytes (padded back), but a word longer than 32 bytes is malformed data,
    never a value an EVM slot can hold."""
    data = bytes(raw)
    if len(data) > 32:
        raise ExternalStateError(
            f"storage word of {len(data)} bytes, more than a 32-byte slot"
        )
    return int.from_bytes(data.rjust(32, b"\x00"), "big")


_SNAPSHOT_TAGS = ("latest", "safe", "finalized", "earliest")


def _snapshot_block(ctx: Web3Context) -> tuple[BlockIdentifier, int]:
    """Resolve ``ctx.default_block`` once into (the identifier the storage reads
    use, the block number it denotes), keeping its meaning: a number as is; a
    tag (latest, safe, finalized, earliest) as the number it denotes now, so
    the four reads share one block; a block hash by identity, with its number
    reported; ``pending`` has no mined snapshot and is rejected."""
    ident = ctx.default_block
    if isinstance(ident, int) and not isinstance(ident, bool):
        return ident, ident
    if isinstance(ident, bytes | bytearray) or (
        isinstance(ident, str) and ident.startswith("0x") and len(ident) == 66
    ):
        return ident, int(ctx.web3.eth.get_block(ident)["number"])
    if ident in _SNAPSHOT_TAGS:
        number = int(ctx.web3.eth.get_block(ident)["number"])
        return number, number
    raise ValueError(
        f"the external-state snapshot needs a mined block; {ident!r} denotes none"
    )


def read_external_state(
    ctx: Web3Context, vault: PlasmaVaultAddress
) -> ExternalStateVaultState:
    """Read the external-state market's vault state as one coherent snapshot.

    Four storage reads of the vault (slots +0 .. +3 of the namespace) at one
    block: ``ctx.default_block`` resolved once with its meaning kept (a number
    as is, a tag such as ``finalized`` as the block it denotes now, a block
    hash by identity; ``pending`` is rejected), so the four values never mix
    blocks; the block number is returned so executor reads can be pinned to
    it. Decoding is canonical: a word longer than 32 bytes, an executor word
    with bits above the address, or a pause word other than 0 or 1, raises
    ``ExternalStateError`` rather than passing as a plausible value; a zero
    executor word is ``None`` (no executor deployed), which is a state, while
    an RPC or decoding failure raises.
    """
    checksum = Web3.to_checksum_address(vault)
    read_at, block = _snapshot_block(ctx)
    base = ExternalStateExecutor._EXECUTOR_STORAGE_SLOT
    words = [
        _storage_word(ctx.get_storage_at(checksum, base + i, read_at)) for i in range(4)
    ]
    if words[0] >> 160:
        raise ExternalStateError(
            f"malformed executor word in the external-state storage of {checksum}"
        )
    if words[3] not in (0, 1):
        raise ExternalStateError(
            f"malformed pause flag in the external-state storage of {checksum}"
        )
    return ExternalStateVaultState(
        executor=Web3.to_checksum_address(f"0x{words[0]:040x}") if words[0] else None,
        last_total_balance=Amount(words[1]),
        last_checked_custodian_timestamp=words[2],
        paused=words[3] == 1,
        block=block,
    )


class ExternalStateExecutor(ContractWrapper):
    """Per-vault ExternalStateExecutor: NAV propose/confirm plus its reads.

    Use `for_vault` when you have only the vault address -- it resolves the
    executor from the vault's ERC-7201 storage. Construct directly,
    `ExternalStateExecutor(executor_address)`, when the executor address is
    already known (config, a cached resolution, a deploy event), to skip the
    storage read. The wrapper holds no context: the proposal readers take the
    chain id the hash is bound to explicitly.
    """

    # ERC-7201 slot `io.ipor.externalState.Executor`; `executor` is the struct's
    # first field, so it sits at the base slot. No public getter exists on-chain,
    # so resolution reads this slot directly. Mirrors the contract's own
    # pre-computed constant; a unit test re-derives it from the namespace.
    _EXECUTOR_STORAGE_SLOT = (
        0x1781023874512EC457C16827AD102F41A5C5CE1CD7BA8AA8FCD2DA52541D8A00
    )

    # `BalanceProposed`'s parameter list, in declaration order. All six are
    # unindexed, so both event readers take them straight out of the log's
    # `data`, and the values are the ones the contract stored rather than
    # anything re-read afterwards. Declared once because topic0 and the payload
    # decode have to agree: a signature that drifts from the decode list stops
    # matching topic0 SILENTLY, which reads as "the event never happened".
    _BALANCE_PROPOSED_TYPES = (
        "address",  # balanceAccount
        "address",  # proposer
        "uint256",  # newValue
        "uint256",  # nonce
        "uint64",  # proposedAt
        "bytes32",  # proposalHash
    )
    _BALANCE_PROPOSED_TOPIC = Web3.keccak(
        text=f"BalanceProposed({','.join(_BALANCE_PROPOSED_TYPES)})"
    )

    # `pendingProposals`' auto-getter returns the struct's fields in declaration
    # order. Named so the drift gate can compare it with the Solidity rather
    # than with itself.
    _PENDING_PROPOSAL_TYPES = (
        "uint256",  # value
        "address",  # proposer
        "uint64",  # proposedAt
        "uint256",  # nonce
    )

    @classmethod
    def for_vault(
        cls, ctx: Web3Context, vault: PlasmaVaultAddress
    ) -> ExternalStateExecutor:
        """Resolve the per-vault executor from vault storage (at
        ``ctx.default_block``) and wrap it. An eager resolver: the wrapper it
        returns holds no context.

        Raises `ExternalStateError` when no executor is deployed yet (the slot
        reads zero) rather than returning a wrapper around the zero address, and
        when the slot is not an address (its high 12 bytes are non-zero: a wrong
        or corrupt slot) or is longer than 32 bytes.
        """
        word = _storage_word(
            ctx.get_storage_at(
                Web3.to_checksum_address(vault), cls._EXECUTOR_STORAGE_SLOT
            )
        )
        if word >> 160:
            raise ExternalStateError(
                f"malformed executor word in the external-state storage of {vault}"
            )
        if word == 0:
            raise ExternalStateError(
                f"no ExternalStateExecutor deployed for vault {vault}"
            )
        return cls(Web3.to_checksum_address(f"0x{word:040x}"))

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

    def last_updated(self, balance_account: ChecksumAddress) -> Call[int]:
        """Block timestamp of the last confirmed custodian update for
        `balance_account`, or 0 if there has never been one.

        Per account -- not the executor-wide `lastCustodianUpdateTimestamp`,
        which only tells the balance fuse that something was confirmed
        somewhere. This one drives the `minUpdateInterval` rate limit and feeds
        `getOldestUpdateTimestamp()`, so it is what an off-chain monitor reads
        to spot an account whose attestation has gone stale.

        Zero is a real answer: never confirmed, or cleared when
        `sync_substrates` purges an account that has left the substrate set --
        which it can only do once that account's balance is already zero. A
        sync that keeps an account does NOT reset its timestamp."""
        return self._view(
            "lastUpdated(address)", balance_account, output_types=["uint256"]
        )

    def get_balance_fuse_snapshot(self) -> Call[BalanceFuseSnapshot]:
        """The live aggregate the balance fuse reads: the sum of the tracked
        balances, the big-change threshold and the last confirmed custodian
        update timestamp, in one call. Pin the context's block to the one
        `read_external_state` returned to compare live against cached."""
        return self._view(
            "getBalanceFuseSnapshot()",
            output_types=["uint256", "uint256", "uint256"],
        ).map(
            lambda values: BalanceFuseSnapshot(
                Amount(values[0]), int(values[1]), int(values[2])
            )
        )

    def get_oldest_update_timestamp(self) -> Call[int]:
        """The pre-hook's staleness clock: the oldest non-zero `lastUpdated`
        across the balance accounts, 0 when no account was ever updated. The
        hook blocks when it is non-zero and older than `staleness_max` by more
        than the limit (strictly)."""
        return self._view("getOldestUpdateTimestamp()", output_types=["uint256"])

    def staleness_max(self) -> Call[int]:
        """Seconds after which a custodian report counts as stale: the pre-hook
        blocks the vault's gated user operations past it."""
        return self._view("stalenessMax()", output_types=["uint256"])

    def big_change_bps(self) -> Call[int]:
        """Basis points of total-balance change between two custodian updates
        that set the vault's pause flag (`ExternalStateBigChangeDetected`)."""
        return self._view("bigChangeBps()", output_types=["uint256"])

    def dust_threshold(self) -> Call[int]:
        """Underlying units below which a leftover is dust, not a balance."""
        return self._view("dustThreshold()", output_types=["uint256"])

    def min_update_interval(self) -> Call[int]:
        """Seconds a balance account must wait between two custodian
        updates."""
        return self._view("minUpdateInterval()", output_types=["uint256"])

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
        chain_id: ChainId,
    ) -> Call[BalanceProposal | None]:
        """The proposal awaiting confirmation for `balance_account`, or `None`.

        The read a separate confirm service needs: it returns the CURRENT
        pending proposal carrying the hash `confirm_balance` verifies, so
        custodian B never has to obtain it from custodian A. Deriving that hash
        instead from `proposal_hash(...)` plus a fresh `nonce()` is unsound; see
        `nonce` for why.

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
        two with `.call(ctx)` rather than an observed read.

        The hash is bound to a chain: ``chain_id`` is the chain the proposal
        lives on, captured in the decoder. Executing the Call on a context of
        another chain does not change it; pass that context's
        ``ctx.chain_id``.
        """
        account = Web3.to_checksum_address(balance_account)
        executor = self._require_address()

        def to_proposal(values: tuple[int, str, int, int]) -> BalanceProposal | None:
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
                proposal_hash=ProposalHash(b""),
            )
            return replace(
                unstamped,
                proposal_hash=_hash_of(unstamped, executor=executor, chain_id=chain_id),
            )

        return self._view(
            "pendingProposals(address)",
            account,
            output_types=list(self._PENDING_PROPOSAL_TYPES),
            decoder=to_proposal,
        )

    def _require_address(self) -> ChecksumAddress:
        """This executor's address, refusing the zero address.

        The other half of the hash's binding, and the zero address is not a
        usable stand-in for it: it stamps a hash bound to no contract, and it
        matches only a log claiming to come from none."""
        if self._address == ZERO_ADDRESS:
            raise ValueError(
                "executor address required: the proposal hash binds it and the "
                "event readers match it against a log's emitter, so build this "
                "wrapper with the executor's address, not the zero address."
            )
        return self._address

    def propose_balance(
        self, balance_account: ChecksumAddress, value: Amount
    ) -> Call[None]:
        """CUSTODIAN-only: propose `value` (underlying units) as the new tracked
        balance of `balance_account`. Confirm it from a *different* custodian
        with `confirm_balance`, then refresh the vault's cached NAV with
        `PlasmaVault.update_markets_balances([EXTERNAL_STATE])`."""
        return self._write("proposeBalance(address,uint256)", balance_account, value)

    def confirm_balance(
        self, balance_account: ChecksumAddress, proposal_hash: ProposalHash
    ) -> Call[None]:
        """CUSTODIAN-only: confirm the pending proposal for `balance_account`.
        Must be sent by a custodian other than the proposer, and `proposal_hash`
        must be the hash the contract stored -- take it from `pending_proposals`
        or the `BalanceProposed` event; see `nonce` for why not to rebuild it."""
        return self._write(
            "confirmBalance(address,bytes32)", balance_account, proposal_hash
        )

    def sync_substrates(self) -> Call[None]:
        """Permissionless: rebuild the executor's substrate cache (custodians,
        balance accounts, assets, guards) from the vault's current market
        grants.

        Send it after any grant or revoke of a CUSTODIAN, BALANCE_ACCOUNT, ASSET
        or guard substrate once the executor exists; until then the executor
        keeps acting on its old cache. TARGET changes apply without it.
        Revocation is asymmetric: a revoked balance account is refused at once,
        since propose/confirm check the vault first, but a revoked CUSTODIAN
        stays authorized until this runs -- so revoke and sync back to back.

        Reverts on a duplicate balance account or singleton, when
        `STALENESS_MAX` or `BIG_CHANGE_BPS` is missing or zero, and when a
        balance account leaving the set still holds a non-zero balance. A
        revert keeps the old cache, custodians included. So
        `ExternalStateOperationFuse.exit` a balance account to zero before
        revoking it: once revoked while funded, it can be neither exited nor
        synced away until it is re-granted.

        It reads the grants from the vault, so send it as its own transaction,
        never from inside `PlasmaVault.execute`.
        """
        return self._write("syncSubstrates()")

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
    ) -> ProposalHash:
        """The confirm hash that `confirmBalance` verifies against:
        ``keccak(abi.encode(executor, chainId, balanceAccount, value, proposer,
        proposedAt, nonce))``.

        `proposed_at` is the propose tx's block timestamp; `nonce` is the one
        the contract stored, from `pending_proposals` or the `BalanceProposed`
        event, never a fresh `nonce()` read -- see `nonce`. Pure -- no chain
        access -- so callers can pre-compute or verify a hash offline.
        """
        return ProposalHash(
            keccak(
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
        )

    def _emitted_by_us(self, log: RawLog) -> bool:
        """Whether this executor emitted `log`; see `_log_emitter` for why an
        address that is missing or unreadable answers "no"."""
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
        self, log: RawLog, *, chain_id: ChainId
    ) -> BalanceProposal:
        """Decode one `BalanceProposed` log this executor emitted, hash-verified.

        Accepts receipt-shaped logs (HexBytes) and JSON-RPC / `eth_simulateV1`
        -shaped logs (`0x`-hex strings) alike, so it also decodes rows from an
        event index. Needs no chain access. Raises `ValueError` for a log that
        is not this event or was not emitted by this executor;
        `ExternalStateError` (also a
        `ValueError`) for one of ours that carries a hash that is not the hash
        of its own fields, or whose data is missing or malformed.

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

        The hash is bound to a chain: ``chain_id`` is the chain the log was
        emitted on. The whole path is pure, with no RPC.

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
            chain_id=chain_id,
        )
        return proposal

    @classmethod
    def _decode_balance_proposed(cls, log: RawLog) -> BalanceProposal:
        """Decode a log whose topic0 is already known to be this event."""
        data = log.get("data")
        if data is None:
            raise ExternalStateError("BalanceProposed log carries no data")
        try:
            # Unpacked in the order `BalanceProposed` declares them; all six
            # are unindexed, so topics carries topic0 alone.
            account, proposer, value, nonce, proposed_at, proposal_hash = decode(
                cls._BALANCE_PROPOSED_TYPES,
                HexBytes(data),
            )
        except (DecodingError, ValueError, TypeError) as exc:
            raise ExternalStateError(f"malformed BalanceProposed data: {exc}") from exc
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
        *,
        chain_id: ChainId,
    ) -> BalanceProposal:
        """The last `BalanceProposed` log THIS executor emitted among `logs`,
        hash-verified.

        Built for the logs of ONE call -- a propose receipt's `receipt["logs"]`,
        or a simulated call's `logs`. Like `parse_balance_proposed` it accepts
        only logs this executor emitted, so a same-signature event from another
        contract cannot be mistaken for ours, and it verifies the match's hash
        against that log's own fields before returning it.

        What it skips and what it raises on differ, because scanning a batch is
        the point. A log that is not this event, or not from this executor --
        including one whose address is missing or unreadable, since neither can
        be ours -- is skipped. One of OURS that will not decode, or whose hash
        is not the hash of its own fields, raises and aborts the scan: on a
        propose receipt that is a broken assumption worth surfacing. Over a
        batch of index rows, where one bad payload would cost the whole scan,
        map `parse_balance_proposed` instead and catch per row. Only the match
        is hash-checked, so a log for an account
        you did not ask about can never abort a scan it is not part of, but
        decoding runs before the `balance_account` filter, so one of OURS that
        will not decode surfaces whichever account you asked for.

        The LAST match wins because `proposeBalance` OVERWRITES the pending
        slot: where several proposals for one account appear, only the newest
        can still be confirmed, and logs arrive oldest first.

        Pass `balance_account` when the logs may span several accounts, so the
        match is the one you proposed for rather than whichever came last.

        The hash is bound to a chain: ``chain_id`` is the chain the logs were
        emitted on.

        Over a `get_logs` range this still answers a different question than
        `pending_proposals`: a proposal may have been confirmed since, which
        deletes the slot but leaves the event behind. To read every proposal,
        filter the range by this executor's address and map
        `parse_balance_proposed` over it. Both verify identically; the reason to
        prefer it there is that it RAISES on a foreign log where this scan
        silently skips, and over a range you filtered yourself a foreign log
        means the filter is wrong, not that the row is uninteresting.
        """
        found = self._last_balance_proposed(logs, balance_account, chain_id)
        if found is not None:
            return found
        raise ValueError(_no_balance_proposed(self._address, balance_account))

    def _last_balance_proposed(
        self,
        logs: Iterable[RawLog],
        balance_account: ChecksumAddress | None,
        chain_id: ChainId,
    ) -> BalanceProposal | None:
        """`find_balance_proposed` without the raise when nothing matches."""
        self._require_address()
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
            # Outside the loop: one hash instead of N, and only over the log
            # actually being returned.
            _require_matching_hash(
                found,
                executor=self._address,
                chain_id=chain_id,
            )
        return found

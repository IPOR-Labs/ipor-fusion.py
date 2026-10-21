"""Unit tests for the ExternalStateExecutor wrapper: mock ctx, no RPC."""

import inspect
from typing import cast
from unittest.mock import MagicMock

import pytest
from _multicall import multicall_aware
from _simulate import simulate_response
from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError, ValueOutOfBounds
from eth_typing import ChecksumAddress
from eth_utils import keccak
from hexbytes import HexBytes
from web3 import Web3
from web3.types import Timestamp

from ipor_fusion.core.external_state_executor import (
    BalanceProposal,
    ExternalStateExecutor,
)
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.core.simulation import VaultSimulator
from ipor_fusion.errors import ExternalStateError
from ipor_fusion.types import ZERO_ADDRESS, Amount, ChainId

VAULT_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
EXECUTOR_ADDR = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")
BALANCE_ACCOUNT = Web3.to_checksum_address("0x3333333333333333333333333333333333333333")
# A second balance account this executor also tracks, versus an unrelated
# contract that emits the same event signature. Different roles, both needed.
OTHER_ACCOUNT = Web3.to_checksum_address("0x8888888888888888888888888888888888888888")
OTHER_EMITTER = Web3.to_checksum_address("0x7777777777777777777777777777777777777777")
PROPOSER = Web3.to_checksum_address("0x4444444444444444444444444444444444444444")
# Hex letters, so its checksummed form differs from its lowercase one. The
# all-digit addresses above are identical in both cases and therefore cannot
# exercise address normalization at all.
MIXED_CASE = Web3.to_checksum_address("0xabcdef0123456789abcdef0123456789abcdef01")
# The chain the fixture logs below are hashed for. `parse_balance_proposed`
# rehashes every log, so a wrapper that parses one has to name a chain.
EVENT_CHAIN = ChainId(8453)
# The signature the contract emits. Spelled out rather than derived from the
# SDK's own type tuple: the provenance test below pins that tuple against this
# string, and a derived one would assert the tuple against itself.
_BALANCE_PROPOSED_SIG = (
    "BalanceProposed(address,address,uint256,uint256,uint64,bytes32)"
)


def _slot_bytes(address: str) -> HexBytes:
    """A 32-byte storage slot holding `address` in its low 20 bytes."""
    return HexBytes(b"\x00" * 12 + bytes.fromhex(address[2:]))


def _make_executor(address=EXECUTOR_ADDR):
    ctx = MagicMock()
    # Explicit: `int()` on a bare MagicMock is 1, so the hash would be built
    # for chain 1 and every fixture hash would fail on an unrelated mismatch.
    ctx.chain_id = EVENT_CHAIN
    return ExternalStateExecutor(address), ctx


def _observed(call, return_data: bytes):
    """Run `call` through the real `VaultSimulator` decode path.

    Feeds a synthetic `eth_simulateV1` response to `_parse_response`, so the
    result is whatever an observation's ``value`` reports -- including a
    decoder that raises."""
    sim = VaultSimulator(MagicMock())
    slot = sim.observe(call)
    sim._parse_response(
        simulate_response(
            returnData=HexBytes(return_data).to_0x_hex(),
            status="0x1",
            gasUsed="0x0",
            logs=[],
        )
    )
    return slot.value


def _erc7201_base_slot(namespace: str) -> int:
    """ERC-7201 base slot: keccak(abi.encode(uint256(keccak(namespace)) - 1)) & ~0xff."""
    inner = int.from_bytes(keccak(text=namespace), "big") - 1
    return int.from_bytes(keccak(inner.to_bytes(32, "big")), "big") & ~0xFF


class TestStorageSlotProvenance:
    def test_slot_matches_erc7201_derivation(self):
        # Pins the hard-coded literal against the contract's namespace: if either
        # the constant or the derivation drifts, this fails loudly.
        assert (
            _erc7201_base_slot("io.ipor.externalState.Executor")
            == ExternalStateExecutor._EXECUTOR_STORAGE_SLOT
        )
        assert (
            ExternalStateExecutor._EXECUTOR_STORAGE_SLOT
            == 0x1781023874512EC457C16827AD102F41A5C5CE1CD7BA8AA8FCD2DA52541D8A00
        )


class TestEventSignatureProvenance:
    def test_topic_matches_the_declared_event_signature(self):
        # topic0 is built by joining the type tuple, so this pins the
        # derivation against the signature the contract actually emits --
        # a drift there would otherwise just stop matching, silently.
        assert ExternalStateExecutor._BALANCE_PROPOSED_TOPIC == Web3.keccak(
            text=_BALANCE_PROPOSED_SIG
        )


class TestForVault:
    def test_resolves_address_from_full_slot(self):
        ctx = MagicMock()
        ctx.get_storage_at.return_value = _slot_bytes(EXECUTOR_ADDR)

        executor = ExternalStateExecutor.for_vault(ctx, VAULT_ADDR)

        assert executor.address == EXECUTOR_ADDR
        ctx.get_storage_at.assert_called_once_with(
            VAULT_ADDR, ExternalStateExecutor._EXECUTOR_STORAGE_SLOT
        )

    def test_resolves_from_leading_zero_stripped_slot(self):
        # Some RPCs strip leading zero bytes; the wrapper left-pads before slicing.
        ctx = MagicMock()
        ctx.get_storage_at.return_value = HexBytes(bytes.fromhex(EXECUTOR_ADDR[2:]))

        executor = ExternalStateExecutor.for_vault(ctx, VAULT_ADDR)

        assert executor.address == EXECUTOR_ADDR

    def test_raises_when_slot_is_zero(self):
        ctx = MagicMock()
        ctx.get_storage_at.return_value = HexBytes(b"\x00" * 32)

        with pytest.raises(
            ExternalStateError, match="no ExternalStateExecutor deployed"
        ):
            ExternalStateExecutor.for_vault(ctx, VAULT_ADDR)

    @pytest.mark.parametrize("empty", ["0x00", "0x"])
    def test_raises_when_slot_is_empty(self, empty):
        # A 1-byte and a truly zero-length response both mean "slot untouched".
        ctx = MagicMock()
        ctx.get_storage_at.return_value = HexBytes(empty)

        with pytest.raises(
            ExternalStateError, match="no ExternalStateExecutor deployed"
        ):
            ExternalStateExecutor.for_vault(ctx, VAULT_ADDR)

    def test_raises_on_nonzero_high_bytes(self):
        # Non-zero high 12 bytes are not a clean address-in-slot (wrong/corrupt
        # slot); rejected rather than masking the low 20 bytes.
        ctx = MagicMock()
        ctx.get_storage_at.return_value = HexBytes(
            bytes(range(1, 13)) + bytes.fromhex(EXECUTOR_ADDR[2:])
        )

        with pytest.raises(ExternalStateError, match="malformed executor word"):
            ExternalStateExecutor.for_vault(ctx, VAULT_ADDR)


class TestBalances:
    def test_encodes_selector_and_account(self):
        executor, _ = _make_executor()

        call = executor.balances(BALANCE_ACCOUNT)

        assert call.to == EXECUTOR_ADDR
        assert call.data[:4] == Web3.keccak(text="balances(address)")[:4]
        (account,) = decode(["address"], call.data[4:])
        assert Web3.to_checksum_address(account) == BALANCE_ACCOUNT
        assert call.output_types == ["uint256"]
        assert call.decoder is Amount

    def test_decodes_uint256(self):
        executor, ctx = _make_executor()
        ctx.call.return_value = encode(["uint256"], [1_234_567])

        assert executor.balances(BALANCE_ACCOUNT).call(ctx) == Amount(1_234_567)


class TestLastUpdated:
    def test_encodes_selector_and_account(self):
        executor, _ = _make_executor()

        call = executor.last_updated(BALANCE_ACCOUNT)

        assert call.to == EXECUTOR_ADDR
        assert call.data[:4] == Web3.keccak(text="lastUpdated(address)")[:4]
        (account,) = decode(["address"], call.data[4:])
        assert Web3.to_checksum_address(account) == BALANCE_ACCOUNT
        assert call.output_types == ["uint256"]
        # `Timestamp` is a NewType: pinned here because swapping it for another
        # one would change nothing at runtime and still pass pyright.
        assert call.decoder is Timestamp

    def test_decodes_uint256(self):
        executor, ctx = _make_executor()
        ctx.call.return_value = encode(["uint256"], [1_700_000_123])

        assert executor.last_updated(BALANCE_ACCOUNT).call(ctx) == 1_700_000_123

    def test_zero_stays_zero(self):
        # "never confirmed" is a legitimate timestamp, not a missing value:
        # unlike `pending_proposals`, this read must not sentinel zero into None.
        executor, ctx = _make_executor()
        ctx.call.return_value = encode(["uint256"], [0])

        assert executor.last_updated(BALANCE_ACCOUNT).call(ctx) == 0


class TestNonce:
    def test_decodes_uint256(self):
        executor, ctx = _make_executor()
        ctx.call.return_value = encode(["uint256"], [42])

        assert executor.nonce().call(ctx) == 42

        to, data = ctx.call.call_args.args
        assert to == EXECUTOR_ADDR
        assert data == Web3.keccak(text="nonce()")[:4]


class TestPendingProposals:
    # Distinct values so a swapped value/proposedAt/nonce cannot pass, and
    # distinct from the module's `_EVENT_*` set so that a slot fixture wired
    # into an event assertion (or the reverse) fails instead of matching.
    VALUE = 5_000_000
    PROPOSED_AT = 1_700_000_123
    NONCE = 9
    CHAIN = EVENT_CHAIN
    # `pendingProposals`' flattened field order, for building synthetic slots.
    # The `output_types` assertion below stays a literal on purpose -- it pins
    # the SDK's own tuple, so sharing this one would compare it with itself.
    SLOT_TYPES = ["uint256", "address", "uint64", "uint256"]

    def _slot_ctx(self, *, proposer=PROPOSER, value=None, nonce=None, proposed_at=None):
        """A ctx whose eth_call answers with a synthetic pendingProposals slot,
        encoded in the struct's own field order."""
        ctx = MagicMock()
        ctx.chain_id = self.CHAIN
        ctx.call.return_value = encode(
            self.SLOT_TYPES,
            [
                self.VALUE if value is None else value,
                proposer,
                self.PROPOSED_AT if proposed_at is None else proposed_at,
                self.NONCE if nonce is None else nonce,
            ],
        )
        return ctx

    def _slot_hash(self, chain_id, *, balance_account=BALANCE_ACCOUNT):
        """The hash `pending_proposals` should compute for `_slot_ctx`'s slot."""
        return ExternalStateExecutor.proposal_hash(
            executor=EXECUTOR_ADDR,
            chain_id=chain_id,
            balance_account=balance_account,
            value=Amount(self.VALUE),
            proposer=PROPOSER,
            proposed_at=self.PROPOSED_AT,
            nonce=self.NONCE,
        )

    def _executor_with_slot(self, **kwargs):
        ctx = self._slot_ctx(**kwargs)
        return ExternalStateExecutor(EXECUTOR_ADDR), ctx

    def test_encodes_selector_and_account(self):
        executor, ctx = self._executor_with_slot()

        call = executor.pending_proposals(BALANCE_ACCOUNT, chain_id=self.CHAIN)

        assert call.to == EXECUTOR_ADDR
        assert call.data[:4] == Web3.keccak(text="pendingProposals(address)")[:4]
        (account,) = decode(["address"], call.data[4:])
        assert Web3.to_checksum_address(account) == BALANCE_ACCOUNT
        assert call.output_types == ["uint256", "address", "uint64", "uint256"]

    def test_decodes_slot_into_proposal(self):
        executor, ctx = self._executor_with_slot()

        proposal = executor.pending_proposals(
            BALANCE_ACCOUNT, chain_id=self.CHAIN
        ).call(ctx)

        assert proposal == BalanceProposal(
            # Not in the getter's return -- carried over from the argument.
            balance_account=BALANCE_ACCOUNT,
            proposer=PROPOSER,
            value=Amount(self.VALUE),
            nonce=self.NONCE,
            proposed_at=self.PROPOSED_AT,
            proposal_hash=self._slot_hash(self.CHAIN),
        )

    def test_hash_is_what_confirm_balance_expects(self):
        # The state route's whole point: feed the hash straight to confirm.
        executor, ctx = self._executor_with_slot()
        proposal = executor.pending_proposals(
            BALANCE_ACCOUNT, chain_id=self.CHAIN
        ).call(ctx)

        assert proposal is not None
        call = executor.confirm_balance(BALANCE_ACCOUNT, proposal.proposal_hash)

        # Against the independently computed hash, not the one that just made
        # the round trip: comparing the calldata with its own input would pass
        # for any value `pending_proposals` invented.
        _, sent_hash = decode(["address", "bytes32"], call.data[4:])
        assert sent_hash == self._slot_hash(self.CHAIN)

    def test_returns_none_for_deleted_slot(self):
        # confirmBalance deletes the slot, so zeros are the NORMAL post-confirm
        # state -- never a zeroed dataclass carrying a hash computed over zeros.
        executor, ctx = self._executor_with_slot(
            proposer=ZERO_ADDRESS, value=0, nonce=0
        )

        assert (
            executor.pending_proposals(BALANCE_ACCOUNT, chain_id=self.CHAIN).call(ctx)
            is None
        )

    def test_observe_distinguishes_empty_from_a_failed_decode(self):
        # Pins the docstring against the real simulator: an empty slot reads as
        # None, while return data the decoder cannot read raises.
        populated = encode(
            self.SLOT_TYPES,
            [self.VALUE, PROPOSER, self.PROPOSED_AT, self.NONCE],
        )
        empty = encode(self.SLOT_TYPES, [0, ZERO_ADDRESS, 0, 0])
        executor, _ = self._executor_with_slot()

        observed = _observed(
            executor.pending_proposals(BALANCE_ACCOUNT, chain_id=self.CHAIN), populated
        )
        assert observed is not None
        assert observed.value == Amount(self.VALUE)

        assert (
            _observed(
                executor.pending_proposals(BALANCE_ACCOUNT, chain_id=self.CHAIN), empty
            )
            is None
        )
        with pytest.raises(DecodingError):
            _observed(
                executor.pending_proposals(BALANCE_ACCOUNT, chain_id=self.CHAIN),
                populated[:32],
            )

    def test_none_depends_only_on_the_proposer(self):
        # The sentinel is the zero proposer alone -- a non-zero value and nonce
        # alongside it must not make the slot look occupied.
        executor, ctx = self._executor_with_slot(proposer=ZERO_ADDRESS)

        assert (
            executor.pending_proposals(BALANCE_ACCOUNT, chain_id=self.CHAIN).call(ctx)
            is None
        )

    def test_a_zero_value_proposal_is_real(self):
        # `proposeBalance(account, 0)` is legal on-chain -- a wind-down mark --
        # and `confirmBalance` gates on the proposer alone. A zero value must
        # decode into a proposal, not read as an empty slot.
        executor, ctx = self._executor_with_slot(value=0)

        proposal = executor.pending_proposals(
            BALANCE_ACCOUNT, chain_id=self.CHAIN
        ).call(ctx)

        assert proposal is not None
        assert proposal.value == Amount(0)

    def test_chain_id_is_a_required_keyword(self):
        # The hash is bound to a chain and nothing else supplies one: the
        # wrapper holds no context, and the decoder never sees the one that
        # executes the Call.
        parameter = inspect.signature(
            ExternalStateExecutor.pending_proposals
        ).parameters["chain_id"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty

    @pytest.mark.parametrize("chain_id", [ChainId(8453), ChainId(1)])
    def test_the_hash_follows_the_named_chain_on_every_decode_path(self, chain_id):
        # The same raw bytes, three ways to decode them: `.call(ctx)`, a
        # Multicall3 batch and a simulation observation. Each stamps the hash
        # for the chain the caller named, never the executing context's.
        executor, ctx = self._executor_with_slot()
        ctx.chain_id = ChainId(42161)
        raw = ctx.call.return_value
        call = executor.pending_proposals(BALANCE_ACCOUNT, chain_id=chain_id)
        expected = self._slot_hash(chain_id)

        direct = call.call(ctx)
        batch_ctx = MagicMock()
        batch_ctx.chain_id = ChainId(42161)
        batch_ctx.call.side_effect = multicall_aware(lambda _to, _data: raw)
        (batched,) = Multicall3(batch_ctx).aggregate([call])
        observed = _observed(call, raw)

        assert direct is not None and batched is not None and observed is not None
        assert direct.proposal_hash == batched.proposal_hash == expected
        assert observed.proposal_hash == expected

    def test_accepts_a_lowercase_account(self):
        # The account is checksummed before it reaches the hash; every other
        # test here passes one already checksummed, so nothing else covers it.
        executor, ctx = self._executor_with_slot()
        lowercase = cast(ChecksumAddress, MIXED_CASE.lower())

        proposal = executor.pending_proposals(lowercase, chain_id=self.CHAIN).call(ctx)

        assert proposal is not None
        assert proposal.balance_account == MIXED_CASE
        assert proposal.proposal_hash == self._slot_hash(
            self.CHAIN, balance_account=MIXED_CASE
        )

    def test_decodes_max_width_fields(self):
        # uint64 proposedAt and uint256 value/nonce at their maxima: the decode
        # and the re-hash both have to survive the widths the ABI allows.
        executor, ctx = self._executor_with_slot(
            value=2**256 - 1, nonce=2**256 - 1, proposed_at=2**64 - 1
        )

        proposal = executor.pending_proposals(
            BALANCE_ACCOUNT, chain_id=self.CHAIN
        ).call(ctx)

        assert proposal is not None
        assert proposal.value == Amount(2**256 - 1)
        assert proposal.nonce == 2**256 - 1
        assert proposal.proposed_at == 2**64 - 1

    def test_the_zero_address_refuses_to_stamp_a_hash(self):
        # The hash would bind no contract: a plausible 32 bytes that confirms
        # nothing anywhere. Refused when the Call is built, before any read.
        executor = ExternalStateExecutor(ZERO_ADDRESS)

        with pytest.raises(ValueError, match="executor address required"):
            executor.pending_proposals(BALANCE_ACCOUNT, chain_id=self.CHAIN)


class TestProposalHash:
    def test_golden_vector(self):
        result = ExternalStateExecutor.proposal_hash(
            executor=Web3.to_checksum_address(
                "0x1111111111111111111111111111111111111111"
            ),
            chain_id=ChainId(8453),
            balance_account=Web3.to_checksum_address(
                "0x2222222222222222222222222222222222222222"
            ),
            value=Amount(1_000_000),
            proposer=Web3.to_checksum_address(
                "0x3333333333333333333333333333333333333333"
            ),
            proposed_at=1_700_000_000,
            nonce=7,
        )

        assert (
            result.hex()
            == "ef1e49b3b250bae7f9955bc0eee4e165dbb09cca3674aafb187f447aebf5a204"
        )

    def test_matches_independent_encoding(self):
        result = ExternalStateExecutor.proposal_hash(
            executor=EXECUTOR_ADDR,
            chain_id=ChainId(1),
            balance_account=BALANCE_ACCOUNT,
            value=Amount(500),
            proposer=PROPOSER,
            proposed_at=1_234,
            nonce=3,
        )

        expected = keccak(
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
                [EXECUTOR_ADDR, 1, BALANCE_ACCOUNT, 500, PROPOSER, 1_234, 3],
            )
        )
        assert result == expected

    def test_matches_manual_word_packing(self):
        # Independent of eth_abi: hand-pack each field into a 32-byte big-endian
        # word in the contract's documented order (ExternalStateExecutor.sol
        # `_proposalHash`). Catches a wrong field order/width/encoding in the
        # module without needing a deployed executor.
        def word(value: int) -> bytes:
            return value.to_bytes(32, "big")

        def addr_word(address: str) -> bytes:
            return bytes.fromhex(address[2:]).rjust(32, b"\x00")

        preimage = (
            addr_word(EXECUTOR_ADDR)
            + word(1)  # chainId
            + addr_word(BALANCE_ACCOUNT)
            + word(500)  # value
            + addr_word(PROPOSER)
            + word(1_234)  # proposedAt (uint64, still a full word non-packed)
            + word(3)  # nonce
        )
        expected = keccak(preimage)

        result = ExternalStateExecutor.proposal_hash(
            executor=EXECUTOR_ADDR,
            chain_id=ChainId(1),
            balance_account=BALANCE_ACCOUNT,
            value=Amount(500),
            proposer=PROPOSER,
            proposed_at=1_234,
            nonce=3,
        )
        assert result == expected

    def test_each_field_changes_the_hash(self):
        base = dict(
            executor=EXECUTOR_ADDR,
            chain_id=ChainId(1),
            balance_account=BALANCE_ACCOUNT,
            value=Amount(500),
            proposer=PROPOSER,
            proposed_at=1_234,
            nonce=3,
        )
        baseline = ExternalStateExecutor.proposal_hash(**base)

        # Every one of the 7 preimage fields must independently move the hash --
        # guards against a dropped, duplicated, or reordered field.
        other_addr = Web3.to_checksum_address(
            "0x5555555555555555555555555555555555555555"
        )
        mutations = {
            "executor": other_addr,
            "chain_id": ChainId(2),
            "balance_account": other_addr,
            "value": Amount(501),
            "proposer": other_addr,
            "proposed_at": 1_235,
            "nonce": 4,
        }
        for field, changed in mutations.items():
            assert (
                ExternalStateExecutor.proposal_hash(**{**base, field: changed})
                != baseline
            ), field

    def test_rejects_out_of_range_values(self):
        base = dict(
            executor=EXECUTOR_ADDR,
            chain_id=ChainId(1),
            balance_account=BALANCE_ACCOUNT,
            value=Amount(500),
            proposer=PROPOSER,
            proposed_at=1_234,
            nonce=3,
        )
        # Fail loud (no silent truncation) on an over-wide uint64 or a negative.
        with pytest.raises(ValueOutOfBounds):
            ExternalStateExecutor.proposal_hash(**{**base, "proposed_at": 2**64})
        with pytest.raises(ValueOutOfBounds):
            ExternalStateExecutor.proposal_hash(**{**base, "value": Amount(-1)})
        with pytest.raises(ValueOutOfBounds):
            ExternalStateExecutor.proposal_hash(**{**base, "nonce": -1})


_PROPOSE_SELECTOR = Web3.keccak(text="proposeBalance(address,uint256)")[:4]
_CONFIRM_SELECTOR = Web3.keccak(text="confirmBalance(address,bytes32)")[:4]


class TestProposeBalance:
    def test_encodes_selector_and_args(self):
        executor, _ = _make_executor()

        call = executor.propose_balance(BALANCE_ACCOUNT, Amount(1_000_000))

        assert call.to == EXECUTOR_ADDR
        assert call.data[:4] == _PROPOSE_SELECTOR
        ba, value = decode(["address", "uint256"], call.data[4:])
        assert Web3.to_checksum_address(ba) == BALANCE_ACCOUNT
        assert value == 1_000_000


class TestConfirmBalance:
    def test_encodes_selector_and_args(self):
        executor, _ = _make_executor()
        proposal_hash = b"\x11" * 32

        call = executor.confirm_balance(BALANCE_ACCOUNT, proposal_hash)

        assert call.to == EXECUTOR_ADDR
        assert call.data[:4] == _CONFIRM_SELECTOR
        ba, got = decode(["address", "bytes32"], call.data[4:])
        assert Web3.to_checksum_address(ba) == BALANCE_ACCOUNT
        assert got == proposal_hash


class TestSyncSubstrates:
    def test_encodes_bare_selector(self):
        executor, _ = _make_executor()

        call = executor.sync_substrates()

        assert call.to == EXECUTOR_ADDR
        assert call.data == Web3.keccak(text="syncSubstrates()")[:4]


_BALANCE_PROPOSED_TOPIC = Web3.keccak(text=_BALANCE_PROPOSED_SIG)


# Six distinct values, so a swapped pair of same-typed fields cannot pass.
_EVENT_VALUE = 111
_EVENT_NONCE = 222
_EVENT_PROPOSED_AT = 333


def _consistent_hash(
    chain_id,
    *,
    proposer=PROPOSER,
    executor=EXECUTOR_ADDR,
    value=_EVENT_VALUE,
    balance_account=BALANCE_ACCOUNT,
    nonce=_EVENT_NONCE,
):
    """The hash the executor itself would compute for `_proposed_log`."""
    return ExternalStateExecutor.proposal_hash(
        executor=executor,
        chain_id=chain_id,
        balance_account=balance_account,
        value=Amount(value),
        proposer=proposer,
        proposed_at=_EVENT_PROPOSED_AT,
        nonce=nonce,
    )


# The default has to be self-consistent, not a placeholder: every
# `parse_balance_proposed` call rehashes the log's own fields and rejects a
# mismatch, so a placeholder would make each of those tests fail on the hash
# before reaching its own subject.
_EVENT_HASH = _consistent_hash(EVENT_CHAIN)


def _proposed_log(
    *,
    as_strings: bool,
    address=EXECUTOR_ADDR,
    topic=None,
    balance_account=None,
    proposal_hash=None,
    value=None,
    nonce=None,
):
    """A BalanceProposed log in either wire shape.

    Receipts give HexBytes; eth_simulateV1 and raw JSON-RPC give 0x-strings.

    Whatever fields you vary, the default `proposal_hash` follows them, because
    both readers rehash the log's own fields and reject a mismatch. Pass one
    explicitly only to build a log the executor could not have emitted.
    """
    account = BALANCE_ACCOUNT if balance_account is None else balance_account
    value = _EVENT_VALUE if value is None else value
    nonce = _EVENT_NONCE if nonce is None else nonce
    if proposal_hash is None:
        proposal_hash = _consistent_hash(
            EVENT_CHAIN,
            proposer=PROPOSER,
            value=value,
            balance_account=account,
            nonce=nonce,
        )
    data = encode(
        ["address", "address", "uint256", "uint256", "uint64", "bytes32"],
        [
            account,
            PROPOSER,
            value,
            nonce,
            _EVENT_PROPOSED_AT,
            proposal_hash,
        ],
    )
    topic = _BALANCE_PROPOSED_TOPIC if topic is None else topic
    if as_strings:
        return {
            "address": address.lower(),
            "topics": [topic.to_0x_hex()],
            "data": "0x" + data.hex(),
        }
    return {"address": address, "topics": [HexBytes(topic)], "data": HexBytes(data)}


_EXPECTED_PROPOSAL = BalanceProposal(
    balance_account=BALANCE_ACCOUNT,
    proposer=PROPOSER,
    value=Amount(_EVENT_VALUE),
    nonce=_EVENT_NONCE,
    proposed_at=_EVENT_PROPOSED_AT,
    proposal_hash=_EVENT_HASH,
)


class TestParseBalanceProposed:
    @pytest.mark.parametrize("as_strings", [False, True], ids=["hexbytes", "0x-string"])
    def test_decodes_both_log_shapes(self, as_strings):
        # Full-object equality pins every field AND their order.
        executor, _ = _make_executor()
        log = _proposed_log(as_strings=as_strings)

        assert (
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)
            == _EXPECTED_PROPOSAL
        )

    def test_needs_no_ctx(self):
        # Decoding indexer rows or a get_logs range must not require an RPC:
        # the wrapper plus an explicit chain id verifies.
        executor = ExternalStateExecutor(EXECUTOR_ADDR)

        parsed = executor.parse_balance_proposed(
            _proposed_log(as_strings=True), chain_id=EVENT_CHAIN
        )

        assert parsed.nonce == _EVENT_NONCE

    def test_the_zero_address_refuses_to_match_an_emitter(self):
        # Against the zero address the emitter check would accept a forged
        # row claiming to come from no contract, then verify its hash under
        # executor 0. Refuse before either check runs.
        executor = ExternalStateExecutor(ZERO_ADDRESS)
        log = _proposed_log(as_strings=False, address=ZERO_ADDRESS)

        with pytest.raises(ValueError, match="executor address required"):
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)

    @pytest.mark.parametrize(
        "method", ["parse_balance_proposed", "find_balance_proposed"]
    )
    def test_chain_id_is_a_required_keyword(self, method):
        # Mirrors pending_proposals: the hash is chain-bound and the wrapper
        # holds no context, so the caller names the chain.
        parameter = inspect.signature(
            getattr(ExternalStateExecutor, method)
        ).parameters["chain_id"]
        assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
        assert parameter.default is inspect.Parameter.empty

    def test_rejects_a_log_from_another_emitter(self):
        executor = ExternalStateExecutor(EXECUTOR_ADDR)
        log = _proposed_log(as_strings=False, address=OTHER_EMITTER)

        with pytest.raises(ValueError, match="was not emitted by"):
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)

    def test_rejects_a_different_event(self):
        executor, _ = _make_executor()
        log = _proposed_log(as_strings=False, topic=Web3.keccak(text="Transfer()"))

        with pytest.raises(ValueError, match="not a BalanceProposed event"):
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)

    @pytest.mark.parametrize("topics", [[], None], ids=["empty", "missing"])
    def test_rejects_a_log_without_topics(self, topics):
        executor, _ = _make_executor()
        log = {"address": EXECUTOR_ADDR, "topics": topics, "data": b""}

        with pytest.raises(ValueError, match="not a BalanceProposed event"):
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)

    def test_rejects_a_log_without_data(self):
        # A right-topic log missing `data` must raise ValueError, not KeyError.
        executor, _ = _make_executor()
        log = {"address": EXECUTOR_ADDR, "topics": [_BALANCE_PROPOSED_TOPIC]}

        with pytest.raises(ExternalStateError, match="carries no data"):
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)

    @pytest.mark.parametrize("as_strings", [False, True], ids=["hexbytes", "0x-string"])
    def test_accepts_a_self_consistent_log(self, as_strings):
        # The 0x-string shape is the one verification exists for -- an indexer
        # or get_logs row. The executor is MIXED_CASE because that shape
        # lowercases the address, and only an address with hex letters makes
        # the passing emitter check normalize anything at all.
        executor, _ = _make_executor(MIXED_CASE)
        log = _proposed_log(
            as_strings=as_strings,
            address=MIXED_CASE,
            proposal_hash=_consistent_hash(EVENT_CHAIN, executor=MIXED_CASE),
        )

        parsed = executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)

        assert parsed.value == Amount(_EVENT_VALUE)

    def test_rejects_a_hash_lifted_from_another_proposal(self):
        # The attack the SECURITY note describes: a modest value carrying the
        # hash of a larger proposal, which the on-chain check would then accept
        # against the OTHER proposal's slot.
        executor, _ = _make_executor()
        # Only the value differs, which is the whole attack: same account, same
        # proposer, same nonce, so nothing but the hash betrays the swap.
        other_hash = _consistent_hash(EVENT_CHAIN, value=999_000_000_000)
        log = _proposed_log(as_strings=False, proposal_hash=other_hash)

        with pytest.raises(ExternalStateError, match="another proposal's hash"):
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)

    def test_rejects_a_log_hashed_for_another_chain(self):
        # The hash binds a chain id, so the executor's own log verified against
        # the wrong chain must not pass -- otherwise chain_id is decoration.
        executor, _ = _make_executor()

        with pytest.raises(ExternalStateError, match="different executor or chain id"):
            executor.parse_balance_proposed(
                _proposed_log(as_strings=False), chain_id=ChainId(1)
            )

    def test_rejects_a_self_consistent_forgery(self):
        # proposal_hash is public and pure, so an attacker can build a payload
        # whose hash matches its own fields under OUR executor. Only the emitter
        # check catches it.
        executor, _ = _make_executor()
        log = _proposed_log(
            as_strings=False,
            address=OTHER_EMITTER,
            proposal_hash=_consistent_hash(EVENT_CHAIN),
        )

        with pytest.raises(ValueError, match="was not emitted by"):
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)

    @pytest.mark.parametrize(
        "address", [None, "not-an-address"], ids=["missing", "unreadable"]
    )
    def test_rejects_an_unattributable_log(self, address):
        executor, _ = _make_executor()
        log = dict(_proposed_log(as_strings=False))
        if address is None:
            del log["address"]
        else:
            log["address"] = address

        with pytest.raises(ValueError, match="was not emitted by"):
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)

    @pytest.mark.parametrize("data", [{}, ["0x00"]], ids=["dict", "list"])
    def test_non_hex_data_raises_external_state_error(self, data):
        # A field that is not hex at all is malformed data like any other.
        executor, _ = _make_executor()
        log = {
            "address": EXECUTOR_ADDR,
            "topics": [_BALANCE_PROPOSED_TOPIC],
            "data": data,
        }

        with pytest.raises(ExternalStateError, match="malformed BalanceProposed"):
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)

    @pytest.mark.parametrize("topic", ["0xnothex", {}], ids=["bad-hex", "wrong-type"])
    def test_malformed_topic_reads_as_another_event(self, topic):
        # An unreadable topic0 cannot be ours, so it is rejected as the wrong
        # event rather than leaking a hex-decoding error from three frames down.
        executor, _ = _make_executor()
        log = {"address": EXECUTOR_ADDR, "topics": [topic], "data": b""}

        with pytest.raises(ValueError, match="not a BalanceProposed event"):
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)

    @pytest.mark.parametrize(
        "data", [b"\x01\x02", "0xnothex", 192], ids=["truncated", "bad-hex", "int"]
    )
    def test_rejects_malformed_data(self, data):
        # An int is the subtle one: HexBytes accepts it, so it reaches the
        # decode as a payload far too short for six fields.
        executor, _ = _make_executor()
        log = {
            "address": EXECUTOR_ADDR,
            "topics": [_BALANCE_PROPOSED_TOPIC],
            "data": data,
        }

        with pytest.raises(ValueError):
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)


class TestFindBalanceProposed:
    @pytest.mark.parametrize("as_strings", [False, True], ids=["hexbytes", "0x-string"])
    def test_finds_ours_in_both_log_shapes(self, as_strings):
        executor, _ = _make_executor()
        logs = [_proposed_log(as_strings=as_strings)]

        assert (
            executor.find_balance_proposed(logs, chain_id=EVENT_CHAIN)
            == _EXPECTED_PROPOSAL
        )

    def test_skips_same_event_from_another_contract(self):
        # A foreign emitter AHEAD of ours must not stop the scan.
        executor, _ = _make_executor()
        logs = [
            _proposed_log(as_strings=False, address=OTHER_EMITTER),
            _proposed_log(as_strings=False),
        ]

        found = executor.find_balance_proposed(logs, chain_id=EVENT_CHAIN)

        assert found == _EXPECTED_PROPOSAL

    def test_skips_other_events_from_this_contract(self):
        executor, _ = _make_executor()
        logs = [_proposed_log(as_strings=False, topic=Web3.keccak(text="Transfer()"))]

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.find_balance_proposed(logs, chain_id=EVENT_CHAIN)

    def test_raises_when_only_foreign_emitters_match(self):
        executor, _ = _make_executor()
        logs = [_proposed_log(as_strings=True, address=OTHER_EMITTER)]

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.find_balance_proposed(logs, chain_id=EVENT_CHAIN)

    def test_raises_on_empty_logs(self):
        executor, _ = _make_executor()

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.find_balance_proposed([], chain_id=EVENT_CHAIN)

    def test_skips_logs_without_an_address(self):
        executor, _ = _make_executor()
        log = dict(_proposed_log(as_strings=False))
        del log["address"]

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.find_balance_proposed([log], chain_id=EVENT_CHAIN)

    def test_filters_by_balance_account(self):
        # Over a get_logs range the executor's other accounts show up first;
        # without the filter "first" is whichever the node happened to return.
        executor, _ = _make_executor()
        logs = [
            _proposed_log(as_strings=False, balance_account=OTHER_ACCOUNT),
            _proposed_log(as_strings=False),
        ]

        assert executor.find_balance_proposed(
            logs, BALANCE_ACCOUNT, chain_id=EVENT_CHAIN
        ) == (_EXPECTED_PROPOSAL)
        assert (
            executor.find_balance_proposed(
                logs, OTHER_ACCOUNT, chain_id=EVENT_CHAIN
            ).balance_account
            == OTHER_ACCOUNT
        )

    def test_without_a_filter_the_last_log_wins_across_accounts(self):
        # The documented unfiltered behavior: no `balance_account` means
        # whichever proposal came last, even when it is for another account.
        executor, _ = _make_executor()
        logs = [
            _proposed_log(as_strings=False),
            _proposed_log(as_strings=False, balance_account=OTHER_ACCOUNT),
        ]

        assert (
            executor.find_balance_proposed(logs, chain_id=EVENT_CHAIN).balance_account
            == OTHER_ACCOUNT
        )

    def test_skips_our_log_with_an_unreadable_topic(self):
        # An unreadable topic0 cannot be this event, so the scan passes over it
        # and finds the real one, matching how an unreadable address is treated.
        executor, _ = _make_executor()
        logs = [
            {"address": EXECUTOR_ADDR, "topics": ["0xnothex"], "data": b""},
            _proposed_log(as_strings=False),
        ]

        assert (
            executor.find_balance_proposed(logs, chain_id=EVENT_CHAIN)
            == _EXPECTED_PROPOSAL
        )

    def test_filter_accepts_a_lowercase_account(self):
        # The filter checksums its argument before comparing; addresses reach a
        # caller from config and env vars unchecksummed, and every other test
        # here passes one already checksummed, so nothing else exercises it.
        executor, _ = _make_executor()
        logs = [_proposed_log(as_strings=False, balance_account=MIXED_CASE)]
        # cast, not to_checksum_address: checksumming here would re-do the
        # normalization under test and the assertion would prove nothing.
        lowercase = cast(ChecksumAddress, MIXED_CASE.lower())

        found = executor.find_balance_proposed(logs, lowercase, chain_id=EVENT_CHAIN)

        assert found.balance_account == MIXED_CASE

    def test_raises_when_the_wanted_account_is_absent(self):
        executor, _ = _make_executor()
        # Lowercase, so the message must name the checksummed form.
        absent = cast(ChecksumAddress, MIXED_CASE.lower())

        # The message names the account, so "emitted nothing" and "emitted only
        # for others" are distinguishable when triaging.
        with pytest.raises(ValueError, match=f"for account {MIXED_CASE}"):
            executor.find_balance_proposed(
                [_proposed_log(as_strings=False)], absent, chain_id=EVENT_CHAIN
            )

    def test_returns_the_last_proposal_for_one_account(self):
        # proposeBalance overwrites the pending slot, so among several proposals
        # for one account only the newest can still be confirmed.
        # Distinguished by nonce, which is what actually separates two real
        # proposals -- `proposeBalance` increments it -- and which gives each
        # log a different, self-consistent hash for free.
        executor, _ = _make_executor()
        newest_hash = _consistent_hash(EVENT_CHAIN, nonce=_EVENT_NONCE + 1)
        logs = [
            _proposed_log(as_strings=False),
            _proposed_log(as_strings=False, nonce=_EVENT_NONCE + 1),
        ]

        assert (
            executor.find_balance_proposed(logs, chain_id=EVENT_CHAIN).proposal_hash
            == newest_hash
        )
        assert (
            executor.find_balance_proposed(
                logs, BALANCE_ACCOUNT, chain_id=EVENT_CHAIN
            ).proposal_hash
            == newest_hash
        )

    @pytest.mark.parametrize(
        "address", ["not-an-address", "0xzz", 12345], ids=["garbage", "bad-hex", "int"]
    )
    def test_skips_a_malformed_address(self, address):
        # An unreadable address cannot be ours, so it is a foreign log. One bad
        # row in a batch must not cost the matches that follow it.
        executor, _ = _make_executor()
        bad = dict(_proposed_log(as_strings=False))
        bad["address"] = address
        ours = _proposed_log(as_strings=False)

        assert (
            executor.find_balance_proposed([bad, ours], chain_id=EVENT_CHAIN)
            == _EXPECTED_PROPOSAL
        )

    def test_raises_when_only_malformed_addresses_match(self):
        executor, _ = _make_executor()
        bad = dict(_proposed_log(as_strings=False))
        bad["address"] = "not-an-address"

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.find_balance_proposed([bad], chain_id=EVENT_CHAIN)

    @pytest.mark.parametrize("data", [{}, ["0x00"]], ids=["dict", "list"])
    def test_non_hex_data_on_our_log_raises_external_state_error(self, data):
        # A field that is not hex at all on one of our logs aborts the scan as
        # malformed data, as `parse_balance_proposed` documents.
        executor, _ = _make_executor()
        log = dict(_proposed_log(as_strings=False))
        log["data"] = data

        with pytest.raises(ExternalStateError, match="malformed BalanceProposed"):
            executor.find_balance_proposed([log], chain_id=EVENT_CHAIN)

    def test_a_malformed_log_aborts_even_a_filtered_scan(self):
        # Decoding happens before the account filter, so a bad payload from this
        # executor surfaces regardless of which account was asked for.
        executor, _ = _make_executor()
        bad = dict(_proposed_log(as_strings=False, balance_account=OTHER_ACCOUNT))
        bad["data"] = HexBytes(b"\x01\x02")

        with pytest.raises(ExternalStateError, match="malformed BalanceProposed data"):
            executor.find_balance_proposed(
                [bad, _proposed_log(as_strings=False)],
                BALANCE_ACCOUNT,
                chain_id=EVENT_CHAIN,
            )

    def test_raises_on_our_own_malformed_log(self):
        # A bad payload from THIS executor is a bad assumption, not noise, so it
        # must surface rather than be skipped like a foreign log.
        executor, _ = _make_executor()
        log = dict(_proposed_log(as_strings=False))
        log["data"] = HexBytes(b"\x01\x02")

        with pytest.raises(ExternalStateError, match="malformed BalanceProposed data"):
            executor.find_balance_proposed([log], chain_id=EVENT_CHAIN)

    def test_the_zero_address_refuses_to_scan(self):
        # Same guard as the single-log reader: the zero address matches only
        # a row claiming no emitter, which is never a real one.
        executor = ExternalStateExecutor(ZERO_ADDRESS)

        with pytest.raises(ValueError, match="executor address required"):
            executor.find_balance_proposed(
                [_proposed_log(as_strings=False)], chain_id=EVENT_CHAIN
            )

    def test_rejects_a_match_whose_hash_is_not_its_own(self):
        # Same check `parse_balance_proposed` applies. Unreachable on chain --
        # the executor computes the hash it emits -- so a mismatch means the
        # SDK's formula and the contract's have diverged, which must not be
        # skipped past to an older log.
        executor, _ = _make_executor()
        log = _proposed_log(
            as_strings=False,
            proposal_hash=_consistent_hash(EVENT_CHAIN, value=999_000_000_000),
        )

        with pytest.raises(ExternalStateError, match="another proposal's hash"):
            executor.find_balance_proposed([log], chain_id=EVENT_CHAIN)

    def test_a_bad_hash_on_another_account_does_not_abort_the_scan(self):
        # Only the match is verified. A log for an account you did not ask
        # about is filtered out before the hash check, so it cannot cost you
        # the match -- unlike a malformed payload, which aborts the decode.
        executor, _ = _make_executor()
        other = _proposed_log(
            as_strings=False,
            balance_account=OTHER_ACCOUNT,
            proposal_hash=b"\xcc" * 32,
        )

        found = executor.find_balance_proposed(
            [other, _proposed_log(as_strings=False)],
            BALANCE_ACCOUNT,
            chain_id=EVENT_CHAIN,
        )

        assert found == _EXPECTED_PROPOSAL

    def test_scans_given_a_chain_id(self):
        # The hash check needs a chain; the caller names it, exactly as for
        # the other two readers.
        executor = ExternalStateExecutor(EXECUTOR_ADDR)
        logs = [_proposed_log(as_strings=True)]

        assert executor.find_balance_proposed(logs, chain_id=EVENT_CHAIN) == (
            _EXPECTED_PROPOSAL
        )


class TestReadExternalState:
    BASE = ExternalStateExecutor._EXECUTOR_STORAGE_SLOT

    BLOCKS = {"safe": 4_804_990, "finalized": 4_804_900, "earliest": 0}
    HASH = "0x" + "ab" * 32

    def _ctx(self, words, default_block="latest", head=4_805_000):
        ctx = MagicMock()
        ctx.default_block = default_block
        blocks = dict(self.BLOCKS, latest=head)
        blocks[self.HASH] = 4_700_123
        ctx.web3.eth.get_block.side_effect = lambda ident: {"number": blocks[ident]}
        by_slot = {
            self.BASE + i: HexBytes(w.to_bytes(32, "big")) for i, w in enumerate(words)
        }
        ctx.get_storage_at.side_effect = lambda vault, slot, block=None: by_slot[slot]
        return ctx

    def test_reads_the_four_slots_at_one_resolved_block(self):
        from ipor_fusion import read_external_state

        ctx = self._ctx([int(EXECUTOR_ADDR, 16), 32_928_135, 1_791_458_983, 1])

        state = read_external_state(ctx, VAULT_ADDR)

        assert state.executor == EXECUTOR_ADDR
        assert state.last_total_balance == 32_928_135
        assert state.last_checked_custodian_timestamp == 1_791_458_983
        assert state.paused is True
        assert state.block == 4_805_000  # "latest" resolved once, before the reads
        assert [c.args for c in ctx.get_storage_at.call_args_list] == [
            (VAULT_ADDR, self.BASE + i, 4_805_000) for i in range(4)
        ]

    @pytest.mark.parametrize("tag", ["safe", "finalized", "earliest"])
    def test_a_tag_is_resolved_once_with_its_meaning(self, tag):
        from ipor_fusion import read_external_state

        ctx = self._ctx([0, 0, 0, 0], default_block=tag)

        state = read_external_state(ctx, VAULT_ADDR)

        assert state.block == self.BLOCKS[tag]
        ctx.web3.eth.get_block.assert_called_once_with(tag)
        assert all(
            c.args[2] == self.BLOCKS[tag] for c in ctx.get_storage_at.call_args_list
        )

    def test_a_block_hash_keeps_its_identity_for_the_reads(self):
        from ipor_fusion import read_external_state

        ctx = self._ctx([0, 0, 0, 0], default_block=self.HASH)

        state = read_external_state(ctx, VAULT_ADDR)

        assert state.block == 4_700_123
        assert all(c.args[2] == self.HASH for c in ctx.get_storage_at.call_args_list)

    def test_pending_is_rejected(self):
        from ipor_fusion import read_external_state

        ctx = self._ctx([0, 0, 0, 0], default_block="pending")
        with pytest.raises(ValueError, match="pending"):
            read_external_state(ctx, VAULT_ADDR)

    def test_oversized_words_raise_and_uint256_max_passes(self):
        from ipor_fusion import read_external_state

        for slot in (1, 2):
            ctx = self._ctx([0, 0, 0, 0])
            words = {self.BASE + i: HexBytes(b"") for i in range(4)}
            words[self.BASE + slot] = HexBytes(b"\x01" + b"\x00" * 32)
            ctx.get_storage_at.side_effect = lambda v, s, b=None, w=words: w[s]
            with pytest.raises(ExternalStateError, match="more than a 32-byte slot"):
                read_external_state(ctx, VAULT_ADDR)

        top = read_external_state(
            self._ctx([0, (1 << 256) - 1, (1 << 256) - 1, 0]), VAULT_ADDR
        )
        assert top.last_total_balance == (1 << 256) - 1
        assert top.last_checked_custodian_timestamp == (1 << 256) - 1

    def test_a_pinned_context_block_is_used_as_is(self):
        from ipor_fusion import read_external_state

        ctx = self._ctx([0, 0, 0, 0], default_block=4_700_000)

        state = read_external_state(ctx, VAULT_ADDR)

        assert state.block == 4_700_000
        assert state.executor is None and state.paused is False
        assert (state.last_total_balance, state.last_checked_custodian_timestamp) == (
            0,
            0,
        )
        assert all(c.args[2] == 4_700_000 for c in ctx.get_storage_at.call_args_list)

    def test_tolerates_leading_zero_stripped_words(self):
        from ipor_fusion import read_external_state

        ctx = self._ctx([0, 0, 0, 0])
        short = {
            self.BASE: HexBytes(bytes.fromhex(EXECUTOR_ADDR[2:])),
            self.BASE + 1: HexBytes(b"\x05"),
            self.BASE + 2: HexBytes(b""),
            self.BASE + 3: HexBytes(b"\x01"),
        }
        ctx.get_storage_at.side_effect = lambda vault, slot, block=None: short[slot]

        state = read_external_state(ctx, VAULT_ADDR)

        assert (state.executor, state.last_total_balance, state.paused) == (
            EXECUTOR_ADDR,
            5,
            True,
        )
        assert state.last_checked_custodian_timestamp == 0

    def test_malformed_words_raise_instead_of_passing_as_values(self):
        from ipor_fusion import read_external_state

        with pytest.raises(ExternalStateError, match="malformed executor word"):
            read_external_state(self._ctx([1 << 161, 0, 0, 0]), VAULT_ADDR)
        with pytest.raises(ExternalStateError, match="malformed pause flag"):
            read_external_state(self._ctx([0, 0, 0, 2]), VAULT_ADDR)

    def test_an_rpc_failure_propagates(self):
        from ipor_fusion import read_external_state

        ctx = self._ctx([0, 0, 0, 0])
        ctx.get_storage_at.side_effect = ConnectionError("rpc down")
        with pytest.raises(ConnectionError):
            read_external_state(ctx, VAULT_ADDR)


class TestExecutorViews:
    @pytest.mark.parametrize(
        ("method", "signature"),
        [
            ("staleness_max", "stalenessMax()"),
            ("big_change_bps", "bigChangeBps()"),
            ("dust_threshold", "dustThreshold()"),
            ("min_update_interval", "minUpdateInterval()"),
            ("get_oldest_update_timestamp", "getOldestUpdateTimestamp()"),
        ],
    )
    def test_uint256_views(self, method, signature):
        executor = ExternalStateExecutor(EXECUTOR_ADDR)
        call = getattr(executor, method)()
        assert bytes(call.data)[:4] == Web3.keccak(text=signature)[:4]
        assert call.output_types == ["uint256"]

    def test_balance_fuse_snapshot_decodes_to_the_dataclass(self):
        from ipor_fusion import BalanceFuseSnapshot

        executor = ExternalStateExecutor(EXECUTOR_ADDR)
        call = executor.get_balance_fuse_snapshot()
        assert bytes(call.data)[:4] == Web3.keccak(text="getBalanceFuseSnapshot()")[:4]
        assert call.output_types == ["uint256", "uint256", "uint256"]
        assert call.decoder is not None
        snap = call.decoder((32_928_135, 50_000, 1_791_458_983))
        assert snap == BalanceFuseSnapshot(32_928_135, 50_000, 1_791_458_983)

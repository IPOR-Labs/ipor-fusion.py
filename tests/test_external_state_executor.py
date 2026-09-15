"""Unit tests for the ExternalStateExecutor wrapper -- mock ctx, verify the
executor-address resolution, `nonce()` decode, and the pure `proposal_hash`."""

from typing import cast
from unittest.mock import MagicMock

import pytest
from _simulate import simulate_response
from eth_abi import decode, encode
from eth_abi.exceptions import NonEmptyPaddingBytes, ValueOutOfBounds
from eth_typing import ChecksumAddress
from eth_utils import keccak
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion.core.external_state_executor import (
    BalanceProposal,
    ExternalStateExecutor,
    NavMark,
)
from ipor_fusion.core.simulation import VaultSimulator
from ipor_fusion.fuses.base import ZERO_ADDRESS
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import Amount, ChainId, MarketId

VAULT_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
EXECUTOR_ADDR = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")
BALANCE_ACCOUNT = Web3.to_checksum_address("0x3333333333333333333333333333333333333333")
# A second balance account this executor also tracks, versus an unrelated
# contract that emits the same event signature. Different roles, both needed.
OTHER_ACCOUNT = Web3.to_checksum_address("0x8888888888888888888888888888888888888888")
OTHER_EMITTER = Web3.to_checksum_address("0x7777777777777777777777777777777777777777")
PROPOSER = Web3.to_checksum_address("0x4444444444444444444444444444444444444444")
CONFIRMER = Web3.to_checksum_address("0x6666666666666666666666666666666666666666")
# Hex letters, so its checksummed form differs from its lowercase one. The
# all-digit addresses above are identical in both cases and therefore cannot
# exercise address normalization at all.
MIXED_CASE = Web3.to_checksum_address("0xabcdef0123456789abcdef0123456789abcdef01")
# The chain the fixture logs below are hashed for. `parse_balance_proposed`
# rehashes every log, so a wrapper that parses one has to name a chain.
EVENT_CHAIN = ChainId(8453)


def _slot_bytes(address: str) -> HexBytes:
    """A 32-byte storage slot holding `address` in its low 20 bytes."""
    return HexBytes(b"\x00" * 12 + bytes.fromhex(address[2:]))


def _make_executor(address=EXECUTOR_ADDR):
    ctx = MagicMock()
    # Explicit: `int()` on a bare MagicMock is 1, so the hash would be built
    # for chain 1 and every fixture hash would fail on an unrelated mismatch.
    ctx.chain_id = EVENT_CHAIN
    return ExternalStateExecutor(ctx, address), ctx


def _observed(call, return_data: bytes):
    """Run `call` through the real `VaultSimulator` decode path.

    Feeds a synthetic `eth_simulateV1` response to `_parse_response`, so the
    result is whatever `observe` would actually report -- including its handling
    of a decoder that raises."""
    sim = VaultSimulator(MagicMock(), EXECUTOR_ADDR, EXECUTOR_ADDR)
    sim.observe("slot", call)
    result = sim._parse_response(
        simulate_response(
            returnData=HexBytes(return_data).to_0x_hex(),
            status="0x1",
            gasUsed="0x0",
            logs=[],
        )
    )
    return result.get("slot")


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
            text="BalanceProposed(address,address,uint256,uint256,uint64,bytes32)"
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

        with pytest.raises(ValueError, match="no ExternalStateExecutor deployed"):
            ExternalStateExecutor.for_vault(ctx, VAULT_ADDR)

    @pytest.mark.parametrize("empty", ["0x00", "0x"])
    def test_raises_when_slot_is_empty(self, empty):
        # A 1-byte and a truly zero-length response both mean "slot untouched".
        ctx = MagicMock()
        ctx.get_storage_at.return_value = HexBytes(empty)

        with pytest.raises(ValueError, match="no ExternalStateExecutor deployed"):
            ExternalStateExecutor.for_vault(ctx, VAULT_ADDR)

    def test_raises_on_nonzero_high_bytes(self):
        # Non-zero high 12 bytes are not a clean address-in-slot (wrong/corrupt
        # slot); `decode` rejects it rather than masking the low 20 bytes.
        ctx = MagicMock()
        ctx.get_storage_at.return_value = HexBytes(
            bytes(range(1, 13)) + bytes.fromhex(EXECUTOR_ADDR[2:])
        )

        with pytest.raises(NonEmptyPaddingBytes):
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

        assert executor.balances(BALANCE_ACCOUNT).call() == Amount(1_234_567)


class TestLastUpdated:
    def test_encodes_selector_and_account(self):
        executor, _ = _make_executor()

        call = executor.last_updated(BALANCE_ACCOUNT)

        assert call.to == EXECUTOR_ADDR
        assert call.data[:4] == Web3.keccak(text="lastUpdated(address)")[:4]
        (account,) = decode(["address"], call.data[4:])
        assert Web3.to_checksum_address(account) == BALANCE_ACCOUNT
        assert call.output_types == ["uint256"]
        # A timestamp carries no unit, so no decoder. Pinned because `Amount` is
        # a NewType -- adding one here would change nothing at runtime and pass
        # pyright, leaving this assertion the only thing that would notice.
        assert call.decoder is None

    def test_decodes_uint256(self):
        executor, ctx = _make_executor()
        ctx.call.return_value = encode(["uint256"], [1_700_000_123])

        assert executor.last_updated(BALANCE_ACCOUNT).call() == 1_700_000_123

    def test_zero_stays_zero(self):
        # "never confirmed" is a legitimate timestamp, not a missing value:
        # unlike `pending_proposal`, this read must not sentinel zero into None.
        executor, ctx = _make_executor()
        ctx.call.return_value = encode(["uint256"], [0])

        assert executor.last_updated(BALANCE_ACCOUNT).call() == 0


class TestNonce:
    def test_decodes_uint256(self):
        executor, ctx = _make_executor()
        ctx.call.return_value = encode(["uint256"], [42])

        assert executor.nonce().call() == 42

        to, data = ctx.call.call_args.args
        assert to == EXECUTOR_ADDR
        assert data == Web3.keccak(text="nonce()")[:4]


class TestPendingProposals:
    # Distinct values so a swapped value/proposedAt/nonce cannot pass.
    VALUE = 5_000_000
    PROPOSED_AT = 1_700_000_123
    NONCE = 9
    CHAIN = ChainId(8453)

    def _slot_ctx(self, *, proposer=PROPOSER, value=None, nonce=None, proposed_at=None):
        """A ctx whose eth_call answers with a synthetic pendingProposals slot,
        encoded in the struct's own field order."""
        ctx = MagicMock()
        ctx.chain_id = self.CHAIN
        ctx.call.return_value = encode(
            ["uint256", "address", "uint64", "uint256"],
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
        return ExternalStateExecutor(ctx, EXECUTOR_ADDR), ctx

    def test_encodes_selector_and_account(self):
        executor, _ = self._executor_with_slot()

        call = executor.pending_proposals(BALANCE_ACCOUNT)

        assert call.to == EXECUTOR_ADDR
        assert call.data[:4] == Web3.keccak(text="pendingProposals(address)")[:4]
        (account,) = decode(["address"], call.data[4:])
        assert Web3.to_checksum_address(account) == BALANCE_ACCOUNT
        assert call.output_types == ["uint256", "address", "uint64", "uint256"]

    def test_decodes_slot_into_proposal(self):
        executor, _ = self._executor_with_slot()

        proposal = executor.pending_proposals(BALANCE_ACCOUNT).call()

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
        executor, _ = self._executor_with_slot()
        proposal = executor.pending_proposals(BALANCE_ACCOUNT).call()

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
        executor, _ = self._executor_with_slot(proposer=ZERO_ADDRESS, value=0, nonce=0)

        assert executor.pending_proposals(BALANCE_ACCOUNT).call() is None

    def test_observe_cannot_distinguish_empty_from_failure(self):
        # Pins the docstring's caveat against the real simulator: observe turns
        # a decoder exception into None (simulation.py), the same value an empty
        # slot legitimately produces, so the two are indistinguishable there.
        populated = encode(
            ["uint256", "address", "uint64", "uint256"],
            [self.VALUE, PROPOSER, self.PROPOSED_AT, self.NONCE],
        )
        empty = encode(
            ["uint256", "address", "uint64", "uint256"], [0, ZERO_ADDRESS, 0, 0]
        )
        executor, _ = self._executor_with_slot()

        # Positive control first: without it every assertion below is `is None`
        # and an observe path that always returned None would pass vacuously.
        observed = _observed(executor.pending_proposals(BALANCE_ACCOUNT), populated)
        assert observed is not None
        assert observed.value == Amount(self.VALUE)

        # A genuinely empty slot.
        assert _observed(executor.pending_proposals(BALANCE_ACCOUNT), empty) is None
        # A decode that raised: no ctx, so the chain-bound hash cannot be built.
        ctxless = ExternalStateExecutor.encoder(EXECUTOR_ADDR)
        assert _observed(ctxless.pending_proposals(BALANCE_ACCOUNT), populated) is None

    def test_none_depends_only_on_the_proposer(self):
        # The sentinel is the zero proposer alone -- a non-zero value and nonce
        # alongside it must not make the slot look occupied.
        executor, _ = self._executor_with_slot(proposer=ZERO_ADDRESS)

        assert executor.pending_proposals(BALANCE_ACCOUNT).call() is None

    def test_a_zero_value_proposal_is_real(self):
        # `proposeBalance(account, 0)` is legal on-chain -- a wind-down mark --
        # and `confirmBalance` gates on the proposer alone. A zero value must
        # decode into a proposal, not read as an empty slot.
        executor, _ = self._executor_with_slot(value=0)

        proposal = executor.pending_proposals(BALANCE_ACCOUNT).call()

        assert proposal is not None
        assert proposal.value == Amount(0)

    def test_encoder_instance_still_builds_calldata(self):
        # encoder() promises calldata from any builder; the chain-bound hash is
        # resolved lazily so this method keeps that promise like its siblings.
        call = ExternalStateExecutor.encoder(EXECUTOR_ADDR).pending_proposals(
            BALANCE_ACCOUNT
        )

        assert call.calldata[:4] == Web3.keccak(text="pendingProposals(address)")[:4]

    def test_encoder_instance_raises_only_on_decode(self):
        # ...and without a ctx or an explicit chain_id there is nothing to bind
        # the hash to, so executing it fails.
        executor = ExternalStateExecutor.encoder(EXECUTOR_ADDR)

        with pytest.raises(ValueError, match="Web3Context required"):
            executor.pending_proposals(BALANCE_ACCOUNT).call(self._slot_ctx())

    def test_explicit_chain_id_overrides_the_wrapper(self):
        # The hash must follow the chain the caller names, not the one the
        # wrapper happens to carry -- Call.call(other_ctx) reads elsewhere.
        executor, _ = self._executor_with_slot()
        other_chain = ChainId(1)

        proposal = executor.pending_proposals(
            BALANCE_ACCOUNT, chain_id=other_chain
        ).call()

        assert proposal is not None
        assert proposal.proposal_hash == self._slot_hash(other_chain)

    def test_default_chain_id_follows_the_wrapper_not_the_caller(self):
        # Pins the documented default, which looks like a bug and is not
        # locally fixable: `Call` hands the decoder only the decoded values,
        # never the ctx that executed the call, so the wrapper's chain is the
        # only one available at stamp time. Callers crossing chains pass
        # `chain_id=` (above). Changing this means changing `Call`.
        executor, _ = self._executor_with_slot()
        other_ctx = self._slot_ctx()
        other_ctx.chain_id = ChainId(1)

        proposal = executor.pending_proposals(BALANCE_ACCOUNT).call(other_ctx)

        assert proposal is not None
        assert proposal.proposal_hash == self._slot_hash(self.CHAIN)

    def test_accepts_a_lowercase_account(self):
        # The account is checksummed before it reaches the hash; every other
        # test here passes one already checksummed, so nothing else covers it.
        executor, _ = self._executor_with_slot()
        lowercase = cast(ChecksumAddress, MIXED_CASE.lower())

        proposal = executor.pending_proposals(lowercase).call()

        assert proposal is not None
        assert proposal.balance_account == MIXED_CASE
        assert proposal.proposal_hash == self._slot_hash(
            self.CHAIN, balance_account=MIXED_CASE
        )

    def test_decodes_max_width_fields(self):
        # uint64 proposedAt and uint256 value/nonce at their maxima: the decode
        # and the re-hash both have to survive the widths the ABI allows.
        executor, _ = self._executor_with_slot(
            value=2**256 - 1, nonce=2**256 - 1, proposed_at=2**64 - 1
        )

        proposal = executor.pending_proposals(BALANCE_ACCOUNT).call()

        assert proposal is not None
        assert proposal.value == Amount(2**256 - 1)
        assert proposal.nonce == 2**256 - 1
        assert proposal.proposed_at == 2**64 - 1

    def test_explicit_chain_id_frees_an_encoder_instance(self):
        # Given a chain id there is nothing left to need a ctx for, so a
        # calldata-only wrapper can read through any context.
        executor = ExternalStateExecutor.encoder(EXECUTOR_ADDR)

        proposal = executor.pending_proposals(
            BALANCE_ACCOUNT, chain_id=self.CHAIN
        ).call(self._slot_ctx())

        assert proposal is not None
        assert proposal.nonce == self.NONCE

    def test_an_addressless_encoder_refuses_to_stamp_a_hash(self):
        # The placeholder is the zero address, so the hash would bind no
        # contract: a plausible 32 bytes that confirms nothing anywhere. The
        # chain id gets a raise for the same reason; so does this half.
        executor = ExternalStateExecutor.encoder()

        with pytest.raises(ValueError, match="executor address required"):
            executor.pending_proposals(BALANCE_ACCOUNT, chain_id=self.CHAIN).call(
                self._slot_ctx()
            )

    def test_an_empty_slot_still_reports_a_missing_chain_id(self):
        # Both hash inputs resolve before the empty-slot sentinel, so a
        # misconfigured poller fails on its first read rather than passing
        # every quiet one and dying on the first proposal that exists.
        executor = ExternalStateExecutor.encoder(EXECUTOR_ADDR)

        with pytest.raises(ValueError, match="Web3Context required"):
            executor.pending_proposals(BALANCE_ACCOUNT).call(
                self._slot_ctx(proposer=ZERO_ADDRESS)
            )


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


_BALANCE_PROPOSED_TOPIC = Web3.keccak(
    text="BalanceProposed(address,address,uint256,uint256,uint64,bytes32)"
)


# Six distinct values, so a swapped pair of same-typed fields cannot pass.
_EVENT_VALUE = 111
_EVENT_NONCE = 222
_EVENT_PROPOSED_AT = 333


def _consistent_hash(
    chain_id, *, proposer=PROPOSER, executor=EXECUTOR_ADDR, value=_EVENT_VALUE
):
    """The hash the executor itself would compute for `_proposed_log`."""
    return ExternalStateExecutor.proposal_hash(
        executor=executor,
        chain_id=chain_id,
        balance_account=BALANCE_ACCOUNT,
        value=Amount(value),
        proposer=proposer,
        proposed_at=_EVENT_PROPOSED_AT,
        nonce=_EVENT_NONCE,
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
    proposer=None,
):
    """A BalanceProposed log in either wire shape.

    Receipts give HexBytes; eth_simulateV1 and raw JSON-RPC give 0x-strings.
    """
    data = encode(
        ["address", "address", "uint256", "uint256", "uint64", "bytes32"],
        [
            BALANCE_ACCOUNT if balance_account is None else balance_account,
            PROPOSER if proposer is None else proposer,
            _EVENT_VALUE if value is None else value,
            _EVENT_NONCE,
            _EVENT_PROPOSED_AT,
            _EVENT_HASH if proposal_hash is None else proposal_hash,
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

        assert executor.parse_balance_proposed(log) == _EXPECTED_PROPOSAL

    def test_needs_no_ctx(self):
        # Decoding indexer rows or a get_logs range must not require an RPC: a
        # ctx-less encoder() wrapper plus an explicit chain id still verifies.
        executor = ExternalStateExecutor.encoder(EXECUTOR_ADDR)

        parsed = executor.parse_balance_proposed(
            _proposed_log(as_strings=True), chain_id=EVENT_CHAIN
        )

        assert parsed.nonce == _EVENT_NONCE

    def test_an_addressless_encoder_refuses_to_match_an_emitter(self):
        # Against the zero placeholder the emitter check would accept a forged
        # row claiming to come from no contract, then verify its hash under
        # executor 0. Refuse before either check runs.
        executor = ExternalStateExecutor.encoder()
        log = _proposed_log(as_strings=False, address=ZERO_ADDRESS)

        with pytest.raises(ValueError, match="executor address required"):
            executor.parse_balance_proposed(log, chain_id=EVENT_CHAIN)

    def test_a_ctxless_encoder_needs_an_explicit_chain_id(self):
        # Mirrors pending_proposals: the hash is chain-bound, so a wrapper with
        # neither a ctx nor a chain_id has nothing to verify against and must
        # say so rather than skipping the check.
        executor = ExternalStateExecutor.encoder(EXECUTOR_ADDR)

        with pytest.raises(ValueError, match="Web3Context required"):
            executor.parse_balance_proposed(_proposed_log(as_strings=False))

    def test_the_emitter_check_precedes_the_chain_id_lookup(self):
        # Both conditions hold at once here. The emitter must win: a ctx-less
        # caller debugging a real emitter mismatch would otherwise be sent
        # chasing a missing chain id that is not the actual problem.
        executor = ExternalStateExecutor.encoder(EXECUTOR_ADDR)
        log = _proposed_log(as_strings=False, address=OTHER_EMITTER)

        with pytest.raises(ValueError, match="was not emitted by"):
            executor.parse_balance_proposed(log)

    def test_rejects_a_different_event(self):
        executor, _ = _make_executor()
        log = _proposed_log(as_strings=False, topic=Web3.keccak(text="Transfer()"))

        with pytest.raises(ValueError, match="not a BalanceProposed event"):
            executor.parse_balance_proposed(log)

    @pytest.mark.parametrize("topics", [[], None], ids=["empty", "missing"])
    def test_rejects_a_log_without_topics(self, topics):
        executor, _ = _make_executor()
        log = {"address": EXECUTOR_ADDR, "topics": topics, "data": b""}

        with pytest.raises(ValueError, match="not a BalanceProposed event"):
            executor.parse_balance_proposed(log)

    def test_rejects_a_log_without_data(self):
        # A right-topic log missing `data` must raise ValueError, not KeyError.
        executor, _ = _make_executor()
        log = {"address": EXECUTOR_ADDR, "topics": [_BALANCE_PROPOSED_TOPIC]}

        with pytest.raises(ValueError, match="carries no data"):
            executor.parse_balance_proposed(log)

    @pytest.mark.parametrize("as_strings", [False, True], ids=["hexbytes", "0x-string"])
    def test_accepts_a_self_consistent_log(self, as_strings):
        # The 0x-string shape is the one verification exists for -- an indexer
        # or get_logs row. The executor is MIXED_CASE because that shape
        # lowercases the address, and only an address with hex letters makes
        # the passing emitter check normalize anything at all. No chain_id, so
        # this also pins the default: the wrapper's own ctx.
        executor, _ = _make_executor(MIXED_CASE)
        log = _proposed_log(
            as_strings=as_strings,
            address=MIXED_CASE,
            proposal_hash=_consistent_hash(EVENT_CHAIN, executor=MIXED_CASE),
        )

        parsed = executor.parse_balance_proposed(log)

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

        with pytest.raises(ValueError, match="another proposal's hash"):
            executor.parse_balance_proposed(log)

    def test_rejects_a_log_hashed_for_another_chain(self):
        # The hash binds a chain id, so the executor's own log verified against
        # the wrong chain must not pass -- otherwise chain_id is decoration.
        executor, _ = _make_executor()

        with pytest.raises(ValueError, match="different executor or chain id"):
            executor.parse_balance_proposed(
                _proposed_log(as_strings=False), chain_id=ChainId(1)
            )

    def test_rejects_a_log_hashed_for_the_wrappers_other_chain(self):
        # Same mismatch reached through the default branch rather than the
        # override: a wrapper bound to one chain, fed rows from another. This is
        # the misuse the docstring describes, and the override cannot stand in
        # for it -- there the caller names the wrong chain knowingly.
        executor, ctx = _make_executor()
        ctx.chain_id = ChainId(1)

        with pytest.raises(ValueError, match="different executor or chain id"):
            executor.parse_balance_proposed(_proposed_log(as_strings=False))

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
            executor.parse_balance_proposed(log)

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
            executor.parse_balance_proposed(log)

    @pytest.mark.parametrize("data", [{}, ["0x00"]], ids=["dict", "list"])
    def test_non_hex_data_raises_type_error(self, data):
        # The docstring promises TypeError for a field that is not hex at all.
        executor, _ = _make_executor()
        log = {
            "address": EXECUTOR_ADDR,
            "topics": [_BALANCE_PROPOSED_TOPIC],
            "data": data,
        }

        with pytest.raises(TypeError):
            executor.parse_balance_proposed(log)

    @pytest.mark.parametrize("topic", ["0xnothex", {}], ids=["bad-hex", "wrong-type"])
    def test_malformed_topic_reads_as_another_event(self, topic):
        # An unreadable topic0 cannot be ours, so it is rejected as the wrong
        # event rather than leaking a hex-decoding error from three frames down.
        executor, _ = _make_executor()
        log = {"address": EXECUTOR_ADDR, "topics": [topic], "data": b""}

        with pytest.raises(ValueError, match="not a BalanceProposed event"):
            executor.parse_balance_proposed(log)

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
            executor.parse_balance_proposed(log)


class TestFindBalanceProposed:
    @pytest.mark.parametrize("as_strings", [False, True], ids=["hexbytes", "0x-string"])
    def test_finds_ours_in_both_log_shapes(self, as_strings):
        executor, _ = _make_executor()
        logs = [_proposed_log(as_strings=as_strings)]

        assert executor.find_balance_proposed(logs) == _EXPECTED_PROPOSAL

    def test_skips_same_event_from_another_contract(self):
        # A foreign emitter AHEAD of ours must not stop the scan.
        executor, _ = _make_executor()
        logs = [
            _proposed_log(as_strings=False, address=OTHER_EMITTER),
            _proposed_log(as_strings=False),
        ]

        found = executor.find_balance_proposed(logs)

        assert found == _EXPECTED_PROPOSAL

    def test_skips_other_events_from_this_contract(self):
        executor, _ = _make_executor()
        logs = [_proposed_log(as_strings=False, topic=Web3.keccak(text="Transfer()"))]

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.find_balance_proposed(logs)

    def test_raises_when_only_foreign_emitters_match(self):
        executor, _ = _make_executor()
        logs = [_proposed_log(as_strings=True, address=OTHER_EMITTER)]

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.find_balance_proposed(logs)

    def test_raises_on_empty_logs(self):
        executor, _ = _make_executor()

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.find_balance_proposed([])

    def test_skips_logs_without_an_address(self):
        executor, _ = _make_executor()
        log = dict(_proposed_log(as_strings=False))
        del log["address"]

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.find_balance_proposed([log])

    def test_filters_by_balance_account(self):
        # Over a get_logs range the executor's other accounts show up first;
        # without the filter "first" is whichever the node happened to return.
        executor, _ = _make_executor()
        logs = [
            _proposed_log(as_strings=False, balance_account=OTHER_ACCOUNT),
            _proposed_log(as_strings=False),
        ]

        assert executor.find_balance_proposed(logs, BALANCE_ACCOUNT) == (
            _EXPECTED_PROPOSAL
        )
        assert (
            executor.find_balance_proposed(logs, OTHER_ACCOUNT).balance_account
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

        assert executor.find_balance_proposed(logs).balance_account == OTHER_ACCOUNT

    def test_skips_our_log_with_an_unreadable_topic(self):
        # An unreadable topic0 cannot be this event, so the scan passes over it
        # and finds the real one, matching how an unreadable address is treated.
        executor, _ = _make_executor()
        logs = [
            {"address": EXECUTOR_ADDR, "topics": ["0xnothex"], "data": b""},
            _proposed_log(as_strings=False),
        ]

        assert executor.find_balance_proposed(logs) == _EXPECTED_PROPOSAL

    def test_filter_accepts_a_lowercase_account(self):
        # The filter checksums its argument before comparing; addresses reach a
        # caller from config and env vars unchecksummed, and every other test
        # here passes one already checksummed, so nothing else exercises it.
        executor, _ = _make_executor()
        logs = [_proposed_log(as_strings=False, balance_account=MIXED_CASE)]
        # cast, not to_checksum_address: checksumming here would re-do the
        # normalization under test and the assertion would prove nothing.
        lowercase = cast(ChecksumAddress, MIXED_CASE.lower())

        found = executor.find_balance_proposed(logs, lowercase)

        assert found.balance_account == MIXED_CASE

    def test_raises_when_the_wanted_account_is_absent(self):
        executor, _ = _make_executor()
        absent = Web3.to_checksum_address("0x9999999999999999999999999999999999999999")

        # The message names the account, so "emitted nothing" and "emitted only
        # for others" are distinguishable when triaging.
        with pytest.raises(ValueError, match=f"for account {absent}"):
            executor.find_balance_proposed([_proposed_log(as_strings=False)], absent)

    def test_returns_the_last_proposal_for_one_account(self):
        # proposeBalance overwrites the pending slot, so among several proposals
        # for one account only the newest can still be confirmed.
        executor, _ = _make_executor()
        newest_hash = b"\xee" * 32
        logs = [
            _proposed_log(as_strings=False, proposal_hash=b"\xcc" * 32),
            _proposed_log(as_strings=False, proposal_hash=newest_hash),
        ]

        assert executor.find_balance_proposed(logs).proposal_hash == newest_hash
        assert (
            executor.find_balance_proposed(logs, BALANCE_ACCOUNT).proposal_hash
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

        assert executor.find_balance_proposed([bad, ours]) == _EXPECTED_PROPOSAL

    def test_raises_when_only_malformed_addresses_match(self):
        executor, _ = _make_executor()
        bad = dict(_proposed_log(as_strings=False))
        bad["address"] = "not-an-address"

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.find_balance_proposed([bad])

    @pytest.mark.parametrize("data", [{}, ["0x00"]], ids=["dict", "list"])
    def test_non_hex_data_on_our_log_raises_type_error(self, data):
        # `_decode_balance_proposed` catches DecodingError and ValueError, so a
        # field that is not hex at all surfaces as TypeError here too, matching
        # what `parse_balance_proposed` documents.
        executor, _ = _make_executor()
        log = dict(_proposed_log(as_strings=False))
        log["data"] = data

        with pytest.raises(TypeError):
            executor.find_balance_proposed([log])

    def test_a_malformed_log_aborts_even_a_filtered_scan(self):
        # Decoding happens before the account filter, so a bad payload from this
        # executor surfaces regardless of which account was asked for.
        executor, _ = _make_executor()
        bad = dict(_proposed_log(as_strings=False, balance_account=OTHER_ACCOUNT))
        bad["data"] = HexBytes(b"\x01\x02")

        with pytest.raises(ValueError, match="malformed BalanceProposed data"):
            executor.find_balance_proposed(
                [bad, _proposed_log(as_strings=False)], BALANCE_ACCOUNT
            )

    def test_raises_on_our_own_malformed_log(self):
        # A bad payload from THIS executor is a bad assumption, not noise, so it
        # must surface rather than be skipped like a foreign log.
        executor, _ = _make_executor()
        log = dict(_proposed_log(as_strings=False))
        log["data"] = HexBytes(b"\x01\x02")

        with pytest.raises(ValueError, match="malformed BalanceProposed data"):
            executor.find_balance_proposed([log])

    def test_an_addressless_encoder_refuses_to_scan(self):
        # Same guard as the single-log reader: a zero placeholder matches only
        # a row claiming no emitter, which is never a real one.
        executor = ExternalStateExecutor.encoder()

        with pytest.raises(ValueError, match="executor address required"):
            executor.find_balance_proposed([_proposed_log(as_strings=False)])


class TestMarkNav:
    # The event layout lives in `_proposed_log` alone; these alias its payload.
    # VALUE must match what the receipt records, since mark_nav refuses to
    # confirm a proposal that differs from the one it sent.
    PROPOSED_AT = _EVENT_PROPOSED_AT
    NONCE = _EVENT_NONCE
    VALUE = _EVENT_VALUE
    BLOCK = 111
    CHAIN = EVENT_CHAIN
    # mark_nav checks the hash binds the value and proposer, so the fixture log
    # carries the hash the executor itself would compute, not a placeholder.
    PROPOSAL_HASH = _consistent_hash(CHAIN)

    def _propose_receipt(self, *, address=EXECUTOR_ADDR, proposal_hash=None):
        # A propose receipt carrying the executor's BalanceProposed log, from
        # which mark_nav reads nonce / proposedAt / proposalHash.
        return {
            "blockNumber": self.BLOCK,
            "logs": [
                _proposed_log(
                    as_strings=False,
                    address=address,
                    proposal_hash=self.PROPOSAL_HASH
                    if proposal_hash is None
                    else proposal_hash,
                )
            ],
        }

    def _setup(self, *, proposal_hash=None):
        executor = ExternalStateExecutor(MagicMock(), EXECUTOR_ADDR)

        proposer_ctx = MagicMock()
        proposer_ctx.signer = PROPOSER
        proposer_ctx.chain_id = self.CHAIN
        proposer_ctx.send.return_value = self._propose_receipt(
            proposal_hash=proposal_hash
        )

        confirmer_ctx = MagicMock()
        confirmer_ctx.signer = CONFIRMER
        confirmer_ctx.chain_id = self.CHAIN
        confirmer_ctx.send.return_value = {"status": 1}
        return executor, proposer_ctx, confirmer_ctx

    def test_runs_propose_confirm_and_returns_navmark(self):
        executor, proposer_ctx, confirmer_ctx = self._setup()

        result = executor.mark_nav(
            value=Amount(self.VALUE),
            balance_account=BALANCE_ACCOUNT,
            proposer_ctx=proposer_ctx,
            confirmer_ctx=confirmer_ctx,
        )

        # Propose signed by the proposer context, with the forwarded args.
        prop_to, prop_data = proposer_ctx.send.call_args.args
        assert prop_to == EXECUTOR_ADDR
        assert prop_data[:4] == _PROPOSE_SELECTOR
        prop_ba, prop_value = decode(["address", "uint256"], prop_data[4:])
        assert Web3.to_checksum_address(prop_ba) == BALANCE_ACCOUNT
        assert prop_value == self.VALUE

        # Confirm signed by the confirmer context, carrying the hash the event
        # recorded; the recomputation only checks it, it is never substituted.
        conf_to, conf_data = confirmer_ctx.send.call_args.args
        assert conf_to == EXECUTOR_ADDR
        assert conf_data[:4] == _CONFIRM_SELECTOR
        conf_ba, got_hash = decode(["address", "bytes32"], conf_data[4:])
        assert Web3.to_checksum_address(conf_ba) == BALANCE_ACCOUNT
        assert got_hash == self.PROPOSAL_HASH

        assert result == NavMark(
            proposed_at=self.PROPOSED_AT,
            nonce=self.NONCE,
            proposal_hash=self.PROPOSAL_HASH,
            propose_receipt=proposer_ctx.send.return_value,
            confirm_receipt=confirmer_ctx.send.return_value,
            refresh_receipt=None,
        )

    def test_propose_precedes_confirm(self):
        executor, proposer_ctx, confirmer_ctx = self._setup()
        manager = MagicMock()
        manager.attach_mock(proposer_ctx.send, "propose_send")
        manager.attach_mock(confirmer_ctx.send, "confirm_send")
        proposer_ctx.send.return_value = self._propose_receipt()
        confirmer_ctx.send.return_value = {"status": 1}

        executor.mark_nav(
            value=Amount(self.VALUE),
            balance_account=BALANCE_ACCOUNT,
            proposer_ctx=proposer_ctx,
            confirmer_ctx=confirmer_ctx,
        )

        names = [name for name, _, _ in manager.mock_calls]
        assert names.index("propose_send") < names.index("confirm_send")

    def test_refreshes_when_vault_given(self):
        executor, proposer_ctx, confirmer_ctx = self._setup()
        vault = MagicMock()
        refresh_call = MagicMock()
        vault.update_markets_balances.return_value = refresh_call
        refresh_call.send.return_value = {"status": 1, "refresh": True}

        result = executor.mark_nav(
            value=Amount(self.VALUE),
            balance_account=BALANCE_ACCOUNT,
            proposer_ctx=proposer_ctx,
            confirmer_ctx=confirmer_ctx,
            vault=vault,
        )

        vault.update_markets_balances.assert_called_once_with(
            [MarketId(IporFusionMarkets.EXTERNAL_STATE)]
        )
        refresh_call.send.assert_called_once_with(confirmer_ctx)
        assert result.refresh_receipt == {"status": 1, "refresh": True}

    def test_picks_the_log_for_the_proposed_account(self):
        # The receipt may carry proposals for several accounts; mark_nav must
        # confirm the hash belonging to the one it proposed for.
        executor, proposer_ctx, confirmer_ctx = self._setup()
        proposer_ctx.send.return_value = {
            "blockNumber": self.BLOCK,
            "logs": [
                # A DISTINCT hash, so confirming the wrong log fails the
                # assertion instead of passing by coincidence.
                _proposed_log(
                    as_strings=False,
                    balance_account=OTHER_ACCOUNT,
                    proposal_hash=b"\xcc" * 32,
                ),
                _proposed_log(as_strings=False, proposal_hash=self.PROPOSAL_HASH),
            ],
        }

        result = executor.mark_nav(
            value=Amount(self.VALUE),
            balance_account=BALANCE_ACCOUNT,
            proposer_ctx=proposer_ctx,
            confirmer_ctx=confirmer_ctx,
        )

        assert result.proposal_hash == self.PROPOSAL_HASH
        _, sent_hash = decode(
            ["address", "bytes32"], confirmer_ctx.send.call_args.args[1][4:]
        )
        assert sent_hash == self.PROPOSAL_HASH

    def test_refuses_to_confirm_a_different_value(self):
        # The receipt is node data. A log recording a value we did not send
        # must not be signed off, or the confirm attests a figure this call
        # never chose.
        executor, proposer_ctx, confirmer_ctx = self._setup()
        proposer_ctx.send.return_value = {
            "blockNumber": self.BLOCK,
            "logs": [_proposed_log(as_strings=False, value=self.VALUE + 1)],
        }

        with pytest.raises(ValueError, match="refusing to confirm"):
            executor.mark_nav(
                value=Amount(self.VALUE),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        confirmer_ctx.send.assert_not_called()

    def test_refuses_to_confirm_another_proposers_log(self):
        executor, proposer_ctx, confirmer_ctx = self._setup()
        proposer_ctx.send.return_value = {
            "blockNumber": self.BLOCK,
            "logs": [_proposed_log(as_strings=False, proposer=CONFIRMER)],
        }

        with pytest.raises(ValueError, match="refusing to confirm"):
            executor.mark_nav(
                value=Amount(self.VALUE),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        confirmer_ctx.send.assert_not_called()

    def test_refuses_a_hash_lifted_from_another_proposal(self):
        # Our value and proposer, another proposal's hash: the value and
        # proposer guards pass, and confirming would attest that other
        # proposal's figure. Only the hash check stops it.
        lifted = ExternalStateExecutor.proposal_hash(
            executor=EXECUTOR_ADDR,
            chain_id=self.CHAIN,
            balance_account=BALANCE_ACCOUNT,
            value=Amount(999_000_000_000),
            proposer=PROPOSER,
            proposed_at=self.PROPOSED_AT,
            nonce=self.NONCE,
        )
        executor, proposer_ctx, confirmer_ctx = self._setup(proposal_hash=lifted)

        with pytest.raises(ValueError, match="another proposal's hash"):
            executor.mark_nav(
                value=Amount(self.VALUE),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        confirmer_ctx.send.assert_not_called()

    def test_rejects_custodians_on_different_chains(self):
        # The confirm hash is bound to one chain, so a mismatch could only fail
        # after the propose landed -- and would report the hash as another
        # proposal's, which it is not. Caught before anything is sent.
        executor, proposer_ctx, confirmer_ctx = self._setup()
        confirmer_ctx.chain_id = ChainId(1)

        with pytest.raises(ValueError, match="must sign on the same chain"):
            executor.mark_nav(
                value=Amount(self.VALUE),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        proposer_ctx.send.assert_not_called()

    def test_accepts_a_lowercase_proposer_signer(self):
        # `Web3Context` stores an explicitly-passed signer verbatim, so one read
        # from config can be lowercase. mark_nav checksums it before comparing
        # against the event's proposer; without that the confirm is refused
        # AFTER the propose tx has already landed, stranding a pending proposal.
        executor, proposer_ctx, confirmer_ctx = self._setup()
        proposer_ctx.signer = cast(ChecksumAddress, MIXED_CASE.lower())
        proposer_ctx.send.return_value = {
            "blockNumber": self.BLOCK,
            "logs": [
                _proposed_log(
                    as_strings=False,
                    proposer=MIXED_CASE,
                    proposal_hash=_consistent_hash(self.CHAIN, proposer=MIXED_CASE),
                )
            ],
        }

        executor.mark_nav(
            value=Amount(self.VALUE),
            balance_account=BALANCE_ACCOUNT,
            proposer_ctx=proposer_ctx,
            confirmer_ctx=confirmer_ctx,
        )

        confirmer_ctx.send.assert_called_once()

    def test_aborts_before_confirming_on_a_foreign_emitter(self):
        # A BalanceProposed log from another contract is not ours: no confirm
        # may be sent on the strength of it.
        executor, proposer_ctx, confirmer_ctx = self._setup()
        proposer_ctx.send.return_value = self._propose_receipt(address=OTHER_EMITTER)

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.mark_nav(
                value=Amount(self.VALUE),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        confirmer_ctx.send.assert_not_called()

    def test_raises_when_propose_receipt_has_no_event(self):
        executor, proposer_ctx, confirmer_ctx = self._setup()
        proposer_ctx.send.return_value = {"blockNumber": self.BLOCK, "logs": []}

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.mark_nav(
                value=Amount(self.VALUE),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        confirmer_ctx.send.assert_not_called()

    def test_rejects_same_custodian(self):
        executor, proposer_ctx, confirmer_ctx = self._setup()
        confirmer_ctx.signer = PROPOSER

        with pytest.raises(ValueError, match="different custodians"):
            executor.mark_nav(
                value=Amount(self.VALUE),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        proposer_ctx.send.assert_not_called()
        confirmer_ctx.send.assert_not_called()

    def test_rejects_same_custodian_case_insensitive(self):
        # A signer stored un-checksummed (built via `signer=`) must not slip the
        # guard just because its casing differs.
        executor, proposer_ctx, confirmer_ctx = self._setup()
        same = Web3.to_checksum_address("0xabcdef0123456789abcdef0123456789abcdef01")
        proposer_ctx.signer = same
        confirmer_ctx.signer = same.lower()

        with pytest.raises(ValueError, match="different custodians"):
            executor.mark_nav(
                value=Amount(self.VALUE),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        proposer_ctx.send.assert_not_called()
        confirmer_ctx.send.assert_not_called()

    def test_rejects_missing_proposer_signer(self):
        executor, proposer_ctx, confirmer_ctx = self._setup()
        proposer_ctx.signer = None

        with pytest.raises(ValueError, match="proposer_ctx must have a signer"):
            executor.mark_nav(
                value=Amount(self.VALUE),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        proposer_ctx.send.assert_not_called()
        confirmer_ctx.send.assert_not_called()

    def test_rejects_missing_confirmer_signer(self):
        executor, proposer_ctx, confirmer_ctx = self._setup()
        confirmer_ctx.signer = None

        with pytest.raises(ValueError, match="confirmer_ctx must have a signer"):
            executor.mark_nav(
                value=Amount(self.VALUE),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        proposer_ctx.send.assert_not_called()
        confirmer_ctx.send.assert_not_called()

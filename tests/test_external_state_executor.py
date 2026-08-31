"""Unit tests for the ExternalStateExecutor wrapper -- mock ctx, verify the
executor-address resolution, `nonce()` decode, and the pure `proposal_hash`."""

from unittest.mock import MagicMock

import pytest
from eth_abi import decode, encode
from eth_abi.exceptions import NonEmptyPaddingBytes, ValueOutOfBounds
from eth_utils import keccak
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion.core.external_state_executor import ExternalStateExecutor, NavMark
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import Amount, ChainId, MarketId

VAULT_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
EXECUTOR_ADDR = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")
BALANCE_ACCOUNT = Web3.to_checksum_address("0x3333333333333333333333333333333333333333")
PROPOSER = Web3.to_checksum_address("0x4444444444444444444444444444444444444444")
CONFIRMER = Web3.to_checksum_address("0x6666666666666666666666666666666666666666")


def _slot_bytes(address: str) -> HexBytes:
    """A 32-byte storage slot holding `address` in its low 20 bytes."""
    return HexBytes(b"\x00" * 12 + bytes.fromhex(address[2:]))


def _make_executor():
    ctx = MagicMock()
    return ExternalStateExecutor(ctx, EXECUTOR_ADDR), ctx


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


class TestNonce:
    def test_decodes_uint256(self):
        executor, ctx = _make_executor()
        ctx.call.return_value = encode(["uint256"], [42])

        assert executor.nonce().call() == 42

        to, data = ctx.call.call_args.args
        assert to == EXECUTOR_ADDR
        assert data == Web3.keccak(text="nonce()")[:4]


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


class TestMarkNav:
    PROPOSED_AT = 1_700_000_000
    NONCE = 7
    PROPOSAL_HASH = b"\xab" * 32
    BLOCK = 111

    def _propose_receipt(self, *, address=EXECUTOR_ADDR):
        # A propose receipt carrying the executor's BalanceProposed log, from
        # which mark_nav reads nonce / proposedAt / proposalHash.
        data = encode(
            ["address", "address", "uint256", "uint256", "uint64", "bytes32"],
            [
                BALANCE_ACCOUNT,
                PROPOSER,
                1_000_000,
                self.NONCE,
                self.PROPOSED_AT,
                self.PROPOSAL_HASH,
            ],
        )
        return {
            "blockNumber": self.BLOCK,
            "logs": [
                {"address": address, "topics": [_BALANCE_PROPOSED_TOPIC], "data": data}
            ],
        }

    def _setup(self):
        executor = ExternalStateExecutor(MagicMock(), EXECUTOR_ADDR)

        proposer_ctx = MagicMock()
        proposer_ctx.signer = PROPOSER
        proposer_ctx.send.return_value = self._propose_receipt()

        confirmer_ctx = MagicMock()
        confirmer_ctx.signer = CONFIRMER
        confirmer_ctx.send.return_value = {"status": 1}
        return executor, proposer_ctx, confirmer_ctx

    def test_runs_propose_confirm_and_returns_navmark(self):
        executor, proposer_ctx, confirmer_ctx = self._setup()

        result = executor.mark_nav(
            value=Amount(1_000_000),
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
        assert prop_value == 1_000_000

        # Confirm signed by the confirmer context, carrying the hash FROM THE
        # EVENT (not recomputed).
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
            value=Amount(1_000_000),
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
            value=Amount(1_000_000),
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

    def test_raises_when_propose_receipt_has_no_event(self):
        executor, proposer_ctx, confirmer_ctx = self._setup()
        proposer_ctx.send.return_value = {"blockNumber": self.BLOCK, "logs": []}

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.mark_nav(
                value=Amount(1_000_000),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        confirmer_ctx.send.assert_not_called()

    def test_ignores_event_from_other_contract(self):
        # A BalanceProposed log emitted by a different address is not ours.
        executor, proposer_ctx, confirmer_ctx = self._setup()
        other = Web3.to_checksum_address("0x7777777777777777777777777777777777777777")
        proposer_ctx.send.return_value = self._propose_receipt(address=other)

        with pytest.raises(ValueError, match="no BalanceProposed log"):
            executor.mark_nav(
                value=Amount(1_000_000),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        confirmer_ctx.send.assert_not_called()

    def test_scans_past_foreign_log_to_find_event(self):
        # A foreign log ahead of ours must be skipped, not stop the scan. Give it
        # a DISTINCT payload so the assertions fail if the wrong log is picked.
        executor, proposer_ctx, confirmer_ctx = self._setup()
        real_log = self._propose_receipt()["logs"][0]
        other = Web3.to_checksum_address("0x7777777777777777777777777777777777777777")
        foreign_data = encode(
            ["address", "address", "uint256", "uint256", "uint64", "bytes32"],
            [BALANCE_ACCOUNT, PROPOSER, 1_000_000, 999, self.PROPOSED_AT, b"\xcc" * 32],
        )
        foreign_log = {
            "address": other,
            "topics": [_BALANCE_PROPOSED_TOPIC],
            "data": foreign_data,
        }
        proposer_ctx.send.return_value = {
            "blockNumber": self.BLOCK,
            "logs": [foreign_log, real_log],
        }

        result = executor.mark_nav(
            value=Amount(1_000_000),
            balance_account=BALANCE_ACCOUNT,
            proposer_ctx=proposer_ctx,
            confirmer_ctx=confirmer_ctx,
        )

        # The real log's values, not the foreign log's (nonce 999 / hash 0xcc..).
        assert result.nonce == self.NONCE
        assert result.proposal_hash == self.PROPOSAL_HASH
        _, got_hash = decode(
            ["address", "bytes32"], confirmer_ctx.send.call_args.args[1][4:]
        )
        assert got_hash == self.PROPOSAL_HASH

    def test_rejects_same_custodian(self):
        executor, proposer_ctx, confirmer_ctx = self._setup()
        confirmer_ctx.signer = PROPOSER

        with pytest.raises(ValueError, match="different custodians"):
            executor.mark_nav(
                value=Amount(1_000_000),
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
                value=Amount(1_000_000),
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
                value=Amount(1_000_000),
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
                value=Amount(1_000_000),
                balance_account=BALANCE_ACCOUNT,
                proposer_ctx=proposer_ctx,
                confirmer_ctx=confirmer_ctx,
            )
        proposer_ctx.send.assert_not_called()
        confirmer_ctx.send.assert_not_called()

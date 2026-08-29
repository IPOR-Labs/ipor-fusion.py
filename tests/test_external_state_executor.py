"""Unit tests for the ExternalStateExecutor wrapper -- mock ctx, verify the
executor-address resolution, `nonce()` decode, and the pure `proposal_hash`."""

from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_abi.exceptions import NonEmptyPaddingBytes, ValueOutOfBounds
from eth_utils import keccak
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion.core.external_state_executor import ExternalStateExecutor
from ipor_fusion.types import Amount, ChainId

VAULT_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
EXECUTOR_ADDR = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")
BALANCE_ACCOUNT = Web3.to_checksum_address("0x3333333333333333333333333333333333333333")
PROPOSER = Web3.to_checksum_address("0x4444444444444444444444444444444444444444")


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

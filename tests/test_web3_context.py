"""Unit tests for Web3Context — mock Web3 calls, no network required."""

from unittest.mock import MagicMock, patch

import pytest
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion.core.context import Web3Context
from ipor_fusion.errors import TransactionError
from ipor_fusion.types import ChainId

ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
TO_ADDR = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")

# A valid Ethereum private key (32 bytes, hex-encoded)
PRIVATE_KEY = "0x" + "ab" * 32


def _make_ctx(
    signer=None,
    private_key=None,
    gas_multiplier=1.25,
):
    """Build a Web3Context with a mocked Web3 instance."""
    web3 = MagicMock(spec=Web3)
    web3.eth = MagicMock()
    return Web3Context(
        web3=web3,
        chain_id=ChainId(1),
        signer=signer,
        private_key=private_key,
        gas_multiplier=gas_multiplier,
    )


# ── Constructor branches ────────────────────────────────────────────────


class TestConstructor:
    def test_signer_from_explicit_address(self):
        ctx = _make_ctx(signer=ADDR)
        assert ctx.signer == ADDR

    def test_signer_from_private_key(self):
        ctx = _make_ctx(private_key=PRIVATE_KEY)
        assert ctx.signer is not None
        # The derived address should be a valid checksum address
        assert Web3.is_checksum_address(ctx.signer)

    def test_no_signer_when_neither_provided(self):
        ctx = _make_ctx()
        assert ctx.signer is None

    def test_properties(self):
        ctx = _make_ctx(signer=ADDR)
        assert ctx.chain_id == ChainId(1)
        assert ctx.web3 is not None


# ── from_url ────────────────────────────────────────────────────────────


class TestFromUrl:
    @patch("ipor_fusion.core.context.Web3")
    def test_from_url_creates_context(self, mock_web3_cls):
        mock_provider = MagicMock()
        mock_web3_cls.HTTPProvider.return_value = mock_provider

        mock_web3_instance = MagicMock()
        mock_web3_instance.eth.chain_id = 42161
        mock_web3_cls.return_value = mock_web3_instance

        # to_checksum_address is used when private_key is set
        mock_web3_cls.to_checksum_address = Web3.to_checksum_address

        ctx = Web3Context.from_url(
            "http://localhost:8545",
            private_key=PRIVATE_KEY,
            gas_multiplier=1.5,
        )

        mock_web3_cls.HTTPProvider.assert_called_once_with("http://localhost:8545")
        mock_web3_cls.assert_called_once_with(mock_provider)
        assert ctx.chain_id == ChainId(42161)
        assert ctx.signer is not None

    @patch("ipor_fusion.core.context.Web3")
    def test_from_url_without_private_key(self, mock_web3_cls):
        mock_web3_instance = MagicMock()
        mock_web3_instance.eth.chain_id = 1
        mock_web3_cls.return_value = mock_web3_instance

        ctx = Web3Context.from_url("http://localhost:8545")

        assert ctx.signer is None


# ── send ────────────────────────────────────────────────────────────────


class TestSend:
    def test_send_raises_without_private_key(self):
        ctx = _make_ctx()
        with pytest.raises(ValueError, match="Private key required"):
            ctx.send(TO_ADDR, b"\x01\x02")

    def test_send_raises_with_signer_but_no_key(self):
        """Signer set explicitly but no private key — send should still fail."""
        ctx = _make_ctx(signer=ADDR)
        with pytest.raises(ValueError, match="Private key required"):
            ctx.send(TO_ADDR, b"\x01\x02")

    def test_send_success(self):
        ctx = _make_ctx(signer=ADDR, private_key=PRIVATE_KEY)
        web3 = ctx.web3

        # Mock chain of calls in send
        web3.eth.get_transaction_count.return_value = 5
        web3.eth.gas_price = 20_000_000_000
        web3.eth.estimate_gas.return_value = 21000

        signed = MagicMock()
        signed.raw_transaction = b"\xf8"
        web3.eth.account.sign_transaction.return_value = signed

        tx_hash = HexBytes(b"\xaa" * 32)
        web3.eth.send_raw_transaction.return_value = tx_hash
        web3.eth.wait_for_transaction_receipt.return_value = {"status": 1}

        receipt = ctx.send(TO_ADDR, b"\x01\x02")
        assert receipt["status"] == 1
        web3.eth.send_raw_transaction.assert_called_once_with(b"\xf8")


# ── _handle_receipt ─────────────────────────────────────────────────────


class TestHandleReceipt:  # pylint: disable=protected-access
    def test_successful_receipt_returned(self):
        ctx = _make_ctx(signer=ADDR)
        receipt = {"status": 1, "blockNumber": 100}
        result = ctx._handle_receipt(HexBytes(b"\xaa" * 32), receipt)
        assert result is receipt

    @patch("ipor_fusion.core.context.get_revert_reason")
    def test_failed_receipt_raises_transaction_error(self, mock_get_reason):
        ctx = _make_ctx(signer=ADDR)
        mock_get_reason.return_value = 'Error("Insufficient balance")'

        tx_hash = HexBytes(b"\xbb" * 32)
        receipt = {"status": 0, "blockNumber": 100}

        with pytest.raises(TransactionError) as exc_info:
            ctx._handle_receipt(tx_hash, receipt)

        assert exc_info.value.tx_hash == tx_hash.hex()
        assert exc_info.value.revert_reason == 'Error("Insufficient balance")'
        mock_get_reason.assert_called_once_with(ctx.web3, tx_hash, receipt)

    @patch("ipor_fusion.core.context.get_revert_reason")
    def test_failed_receipt_with_no_revert_reason(self, mock_get_reason):
        ctx = _make_ctx(signer=ADDR)
        mock_get_reason.return_value = None

        tx_hash = HexBytes(b"\xcc" * 32)
        receipt = {"status": 0, "blockNumber": 100}

        with pytest.raises(TransactionError):
            ctx._handle_receipt(tx_hash, receipt)


# ── send with failed receipt ────────────────────────────────────────────


class TestSendFailedReceipt:
    @patch("ipor_fusion.core.context.get_revert_reason")
    def test_send_raises_on_failed_transaction(self, mock_get_reason):
        mock_get_reason.return_value = "execution reverted"

        ctx = _make_ctx(signer=ADDR, private_key=PRIVATE_KEY)
        web3 = ctx.web3

        web3.eth.get_transaction_count.return_value = 0
        web3.eth.gas_price = 10_000_000_000
        web3.eth.estimate_gas.return_value = 21000

        signed = MagicMock()
        signed.raw_transaction = b"\xf8"
        web3.eth.account.sign_transaction.return_value = signed

        tx_hash = HexBytes(b"\xdd" * 32)
        web3.eth.send_raw_transaction.return_value = tx_hash
        web3.eth.wait_for_transaction_receipt.return_value = {
            "status": 0,
            "blockNumber": 42,
        }

        with pytest.raises(TransactionError):
            ctx.send(TO_ADDR, b"\x01")

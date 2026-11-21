# pyright: reportAttributeAccessIssue=false
"""Unit tests for Web3Context — mock Web3 calls, no network required."""

import json
import socket
import threading
import time
from collections.abc import Generator
from concurrent.futures import ThreadPoolExecutor
from contextlib import contextmanager
from typing import Any
from unittest.mock import MagicMock, PropertyMock, patch

import pytest
import requests
from eth_abi import encode
from eth_account import Account
from hexbytes import HexBytes
from requests.exceptions import (
    ChunkedEncodingError,
    ConnectTimeout,
    HTTPError,
    ReadTimeout,
    Timeout,
)
from requests.exceptions import ConnectionError as RequestsConnectionError
from urllib3.exceptions import (
    MaxRetryError,
    NameResolutionError,
    NewConnectionError,
    ProtocolError,
    SSLError,
)
from web3 import Web3
from web3.eth import Eth
from web3.exceptions import (
    BadResponseFormat,
    ContractLogicError,
    TimeExhausted,
    Web3RPCError,
)
from web3.providers.rpc import HTTPProvider
from web3.providers.rpc.utils import ExceptionRetryConfiguration
from web3.types import Nonce

from ipor_fusion import InFlightLimit
from ipor_fusion.core.context import (
    _NOT_RETRIED,
    _RETRY_POLICY,
    Web3Context,
    _no_connection_made,
    _provider_retries_post,
)
from ipor_fusion.core.contract import Call
from ipor_fusion.core.oracle import PriceOracleMiddleware, price_oracle_middleware
from ipor_fusion.errors import (
    LogSourceUnavailable,
    TransactionError,
    TransactionOutcomeUnknown,
    TransactionTimeout,
)
from ipor_fusion.types import ChainId, parse_tx_hash

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

        mock_web3_cls.HTTPProvider.assert_called_once_with(
            "http://localhost:8545",
            request_kwargs={"timeout": Web3Context.DEFAULT_RPC_TIMEOUT_S},
            exception_retry_configuration=_RETRY_POLICY,
        )
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

    @patch("ipor_fusion.core.context.Web3")
    def test_from_url_passes_custom_request_timeout(self, mock_web3_cls):
        mock_web3_instance = MagicMock()
        mock_web3_instance.eth.chain_id = 1
        mock_web3_cls.return_value = mock_web3_instance

        Web3Context.from_url("http://localhost:8545", request_timeout_s=10.0)

        mock_web3_cls.HTTPProvider.assert_called_once_with(
            "http://localhost:8545",
            request_kwargs={"timeout": 10.0},
            exception_retry_configuration=_RETRY_POLICY,
        )

    def test_retries_skip_get_logs_and_raw_transactions(self):
        assert _NOT_RETRIED == {"eth_getLogs", "eth_sendRawTransaction"}
        assert _NOT_RETRIED.isdisjoint(_RETRY_POLICY.method_allowlist)
        assert "eth_call" in _RETRY_POLICY.method_allowlist

    @pytest.mark.parametrize(
        ("method", "posts"),
        [("eth_sendRawTransaction", 1), ("eth_call", _RETRY_POLICY.retries)],
    )
    def test_timed_out_posts_follow_the_retry_policy(self, method, posts):
        provider = HTTPProvider(
            "http://localhost:8545", exception_retry_configuration=_RETRY_POLICY
        )
        with (
            patch.object(
                provider._request_session_manager,
                "make_post_request",
                side_effect=Timeout,
            ) as post,
            patch("web3.providers.rpc.rpc.time.sleep"),
            pytest.raises(Timeout),
        ):
            provider.make_request(method, ["0x"])

        assert post.call_count == posts

    def test_reads_do_not_refetch_the_chain_id(self):
        """web3's validation middleware re-fetches eth_chainId around every
        eth_call, tripling the round trips of each read; from_url drops it."""
        methods: list[str] = []

        def make_request(_provider, method, _params):
            methods.append(method)
            result = "0x1" + "00" * 32
            return {"jsonrpc": "2.0", "id": 1, "result": result}

        with patch.object(HTTPProvider, "make_request", make_request):
            ctx = Web3Context.from_url("http://localhost:8545")
            target = Web3.to_checksum_address("0x" + "11" * 20)
            ctx.call(target, b"\x12\x34\x56\x78")
            ctx.call(target, b"\x12\x34\x56\x78")

        assert methods == ["eth_chainId", "eth_call", "eth_call"]

    def test_reads_a_block_with_long_extra_data(self):
        """Avalanche block extraData can be 66 bytes; without the POA
        middleware web3 raises ValueError on get_block."""
        extra = (
            "0x00000000000184e29e3690047cd74c4b287eb04eed1d0d5852d7b84fa1457da9"
            "a13266378ca800000001020000000000000000000000000000000000000500000000"
        )
        block = {
            "number": "0x10",
            "hash": "0x" + "ab" * 32,
            "parentHash": "0x" + "cd" * 32,
            "timestamp": "0x6700000a",
            "extraData": extra,
            "transactions": [],
        }

        def make_request(_provider, method, _params):
            result = "0xa86a" if method == "eth_chainId" else block
            return {"jsonrpc": "2.0", "id": 1, "result": result}

        with patch.object(HTTPProvider, "make_request", make_request):
            ctx = Web3Context.from_url("http://localhost:8545")
            got = ctx.web3.eth.get_block(16)

        assert got["timestamp"] == 0x6700000A
        assert got["proofOfAuthorityData"] == HexBytes(extra)


# ── RPC error shapes ────────────────────────────────────────────────────


def _context_answering_with(error: dict, private_key: str | None = None) -> Web3Context:
    def make_request(_provider, method, _params):
        if method == "eth_chainId":
            return {"jsonrpc": "2.0", "id": 1, "result": "0x3e7"}
        return {"jsonrpc": "2.0", "id": 1, "error": error}

    with patch.object(HTTPProvider, "make_request", make_request):
        return Web3Context.from_url("http://localhost:8545", private_key=private_key)


REVM_REVERT = {
    "code": -32603,
    "message": "Failed to call: InvalidTransaction(Revert(RevertError { output: None }))",
}


class TestCallErrorShapes:
    def test_revm_style_revert_raises_contract_logic_error(self):
        ctx = _context_answering_with(REVM_REVERT)

        with pytest.raises(ContractLogicError) as info:
            ctx.call(TO_ADDR, b"\x31\x1d\x5c\x62")

        assert isinstance(info.value.__cause__, Web3RPCError)

    def test_oracle_probe_treats_revm_style_revert_as_current_middleware(self):
        ctx = _context_answering_with(REVM_REVERT)

        oracle = price_oracle_middleware(ctx, TO_ADDR)

        assert type(oracle) is PriceOracleMiddleware

    def test_other_internal_errors_are_not_treated_as_reverts(self):
        ctx = _context_answering_with({"code": -32603, "message": "header not found"})

        with pytest.raises(Web3RPCError) as info:
            ctx.call(TO_ADDR, b"\x31\x1d\x5c\x62")

        assert not isinstance(info.value, ContractLogicError)


class TestEstimateGasErrorShapes:
    def test_revm_style_revert_raises_contract_logic_error(self):
        ctx = _context_answering_with(REVM_REVERT, private_key=PRIVATE_KEY)

        with pytest.raises(ContractLogicError) as info:
            ctx.estimate_gas(TO_ADDR, b"\x31\x1d\x5c\x62")

        assert isinstance(info.value.__cause__, Web3RPCError)


# ── send ────────────────────────────────────────────────────────────────


SIGNED_HASH = HexBytes(b"\xee" * 32)


def _sending_ctx(signer=ADDR) -> Web3Context:
    ctx = _make_ctx(signer=signer, private_key=PRIVATE_KEY)
    web3 = ctx.web3
    web3.eth.get_transaction_count.return_value = 0
    web3.eth.gas_price = 10_000_000_000
    web3.eth.get_block.return_value = {"baseFeePerGas": 8_000_000_000}
    web3.eth.estimate_gas.return_value = 21000
    signed = MagicMock()
    signed.raw_transaction = b"\xf8"
    signed.hash = SIGNED_HASH
    web3.eth.account.sign_transaction.return_value = signed
    web3.eth.send_raw_transaction.return_value = SIGNED_HASH
    web3.eth.wait_for_transaction_receipt.return_value = {"status": 1}
    return ctx


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
        ctx = _sending_ctx()
        receipt = ctx.send(TO_ADDR, b"\x01\x02")
        assert receipt["status"] == 1
        ctx.web3.eth.send_raw_transaction.assert_called_once_with(b"\xf8")

    def test_send_signs_the_given_nonce(self):
        ctx = _sending_ctx()
        ctx.send(TO_ADDR, b"\x01\x02", nonce=41)
        signed_tx = ctx.web3.eth.account.sign_transaction.call_args.args[0]
        assert signed_tx["nonce"] == 41
        ctx.web3.eth.get_transaction_count.assert_not_called()


# ── maxFeePerGas construction ────────────────────────────────────────────


GWEI = 1_000_000_000


def _build_tx(ctx, *, gas_price, block):
    """Drive build_transaction with stubbed nonce/gas_price/block/gas."""
    web3 = ctx.web3
    web3.eth.get_transaction_count.return_value = 0
    web3.eth.gas_price = gas_price
    web3.eth.get_block.return_value = block
    web3.eth.estimate_gas.return_value = 21000
    return ctx.build_transaction(TO_ADDR, b"\x01\x02")


class TestMaxFeePerGas:
    def test_eip1559_ceiling_uses_base_fee_headroom(self):
        ctx = _make_ctx(signer=ADDR)
        tx = _build_tx(ctx, gas_price=20 * GWEI, block={"baseFeePerGas": 15 * GWEI})

        # priority = min(2 gwei default, gas_price // 10 = 2 gwei) = 2 gwei
        # ceiling = base_fee * 2 + priority
        assert tx["maxPriorityFeePerGas"] == 2 * GWEI
        assert tx["maxFeePerGas"] == 15 * GWEI * 2 + 2 * GWEI

    def test_priority_fee_folded_into_ceiling_is_capped(self):
        ctx = _make_ctx(signer=ADDR)
        # gas_price // 10 (10 gwei) exceeds the 2 gwei cap, so the priority fee
        # in the ceiling must be the cap, not the raw gas_price // 10.
        tx = _build_tx(ctx, gas_price=100 * GWEI, block={"baseFeePerGas": 15 * GWEI})

        cap = Web3Context.DEFAULT_TRANSACTION_MAX_PRIORITY_FEE
        assert tx["maxPriorityFeePerGas"] == cap
        assert tx["maxFeePerGas"] == 15 * GWEI * 2 + cap

    def test_zero_base_fee_still_takes_the_eip1559_branch(self):
        ctx = _make_ctx(signer=ADDR)
        # base_fee 0 is falsy but not None: the branch keys on `is None`, so a
        # zero base fee collapses the ceiling to just the priority fee.
        tx = _build_tx(ctx, gas_price=20 * GWEI, block={"baseFeePerGas": 0})

        assert tx["maxFeePerGas"] == tx["maxPriorityFeePerGas"]

    def test_falls_back_to_gas_price_margin_without_base_fee(self):
        ctx = _make_ctx(signer=ADDR)
        # A pre-EIP-1559 block carries no baseFeePerGas key.
        tx = _build_tx(ctx, gas_price=20 * GWEI, block={})

        assert tx["maxFeePerGas"] == 20 * GWEI + (20 * GWEI * 25 // 100)

    def test_ceiling_clears_a_base_fee_that_the_old_formula_would_miss(self):
        # Regression guard: on Arbitrum-like fees the pre-fix ceiling
        # (gas_price + 25%) fell below a base fee that ticked up before
        # broadcast; the base-fee-relative ceiling clears it.
        ctx = _make_ctx(signer=ADDR)
        gas_price = 10_000_000  # 0.01 gwei, Arbitrum floor at read time
        tx = _build_tx(ctx, gas_price=gas_price, block={"baseFeePerGas": gas_price})

        bumped_base_fee = 15_000_000  # +50% by broadcast time
        old_ceiling = gas_price + gas_price * 25 // 100
        priority = gas_price // 10  # below the 2 gwei cap at this gas price
        assert old_ceiling < bumped_base_fee  # would have been rejected
        assert tx["maxFeePerGas"] == gas_price * 2 + priority  # exact new ceiling
        assert int(tx["maxFeePerGas"]) >= bumped_base_fee  # now clears it


# ── _handle_receipt ─────────────────────────────────────────────────────


class TestHandleReceipt:
    def test_successful_receipt_returned(self):
        ctx = _make_ctx(signer=ADDR)
        receipt = {"status": 1, "blockNumber": 100}
        result = ctx._handle_receipt(parse_tx_hash(b"\xaa" * 32), receipt)
        assert result is receipt

    @patch("ipor_fusion.core.context.get_revert_reason")
    def test_failed_receipt_raises_transaction_error(self, mock_get_reason):
        ctx = _make_ctx(signer=ADDR)
        mock_get_reason.return_value = 'Error("Insufficient balance")'

        tx_hash = parse_tx_hash(b"\xbb" * 32)
        receipt = {"status": 0, "blockNumber": 100}

        with pytest.raises(TransactionError) as exc_info:
            ctx._handle_receipt(tx_hash, receipt)

        assert exc_info.value.tx_hash == tx_hash
        assert exc_info.value.revert_reason == 'Error("Insufficient balance")'
        mock_get_reason.assert_called_once_with(ctx.web3, tx_hash, receipt)

    @patch("ipor_fusion.core.context.get_revert_reason")
    def test_failed_receipt_with_no_revert_reason(self, mock_get_reason):
        ctx = _make_ctx(signer=ADDR)
        mock_get_reason.return_value = None

        tx_hash = parse_tx_hash(b"\xcc" * 32)
        receipt = {"status": 0, "blockNumber": 100}

        with pytest.raises(TransactionError):
            ctx._handle_receipt(tx_hash, receipt)


# ── send with failed receipt ────────────────────────────────────────────


class TestSendFailedReceipt:
    @patch("ipor_fusion.core.context.get_revert_reason")
    def test_send_raises_on_failed_transaction(self, mock_get_reason):
        mock_get_reason.return_value = "execution reverted"
        ctx = _sending_ctx()
        ctx.web3.eth.wait_for_transaction_receipt.return_value = {
            "status": 0,
            "blockNumber": 42,
        }

        with pytest.raises(TransactionError) as exc_info:
            ctx.send(TO_ADDR, b"\x01")
        assert exc_info.value.tx_hash == SIGNED_HASH.to_0x_hex()


# ── send: transport failures ────────────────────────────────────────────


def _connection_error(reason: Exception) -> RequestsConnectionError:
    """How requests reports a urllib3 failure that exhausted its retries."""
    return RequestsConnectionError(MaxRetryError(None, "/", reason))


def _refused() -> RequestsConnectionError:
    return _connection_error(NewConnectionError(None, "Connection refused"))


def _dns_failure() -> RequestsConnectionError:
    return _connection_error(
        NameResolutionError("node.invalid", None, socket.gaierror(-2, "Name unknown"))
    )


def _reset() -> RequestsConnectionError:
    return RequestsConnectionError(ProtocolError("Connection aborted."))


_NEVER_LEFT = [ConnectTimeout, _refused, _dns_failure]

_MAYBE_SENT = [
    ReadTimeout,
    _reset,
    lambda: HTTPError("502 Bad Gateway"),
    ChunkedEncodingError,
    lambda: json.JSONDecodeError("Expecting value", "", 0),
    lambda: BadResponseFormat("no result"),
    lambda: OSError("IPC socket closed"),
]


@patch("ipor_fusion.core.context.time.sleep")
class TestSendTransport:
    def test_waits_for_the_signed_hash(self, _sleep):
        ctx = _sending_ctx()
        ctx.send(TO_ADDR, b"\x01")
        ctx.web3.eth.wait_for_transaction_receipt.assert_called_once_with(
            SIGNED_HASH.to_0x_hex()
        )

    @pytest.mark.parametrize("never_left", _NEVER_LEFT)
    def test_repeats_a_post_that_never_left(self, sleep, never_left):
        ctx = _sending_ctx()
        post = ctx.web3.eth.send_raw_transaction
        post.side_effect = [never_left(), never_left(), SIGNED_HASH]
        assert ctx.send(TO_ADDR, b"\x01") == {"status": 1}
        assert post.call_count == 3
        assert [c.args[0] for c in sleep.call_args_list] == [0.25, 0.5]

    @pytest.mark.parametrize("never_left", _NEVER_LEFT)
    def test_raises_the_transport_error_once_attempts_run_out(self, sleep, never_left):
        ctx = _sending_ctx()
        post = ctx.web3.eth.send_raw_transaction
        error = never_left()
        post.side_effect = error
        with pytest.raises(type(error)) as exc_info:
            ctx.send(TO_ADDR, b"\x01")
        assert exc_info.value is error
        assert post.call_count == 3
        assert [c.args[0] for c in sleep.call_args_list] == [0.25, 0.5]

    def test_a_provider_that_repeats_the_post_proves_nothing(self, sleep):
        ctx = _sending_ctx()
        ctx.web3.provider.exception_retry_configuration = ExceptionRetryConfiguration(
            errors=(RequestsConnectionError,)
        )
        post = ctx.web3.eth.send_raw_transaction
        post.side_effect = _refused()
        with pytest.raises(TransactionOutcomeUnknown):
            ctx.send(TO_ADDR, b"\x01")
        assert post.call_count == 1
        sleep.assert_not_called()

    def test_a_provider_that_repeats_the_post_cannot_prove_a_rejection(self, sleep):
        ctx = _sending_ctx()
        ctx.web3.provider.exception_retry_configuration = ExceptionRetryConfiguration(
            errors=(RequestsConnectionError,)
        )
        post = ctx.web3.eth.send_raw_transaction
        error = Web3RPCError("already known")
        post.side_effect = error
        with pytest.raises(TransactionOutcomeUnknown) as exc_info:
            ctx.send(TO_ADDR, b"\x01", nonce=41)
        assert exc_info.value.tx_hash == SIGNED_HASH.to_0x_hex()
        assert exc_info.value.nonce == 41
        assert exc_info.value.__cause__ is error
        assert post.call_count == 1
        sleep.assert_not_called()

    def test_a_retried_post_that_may_have_left_has_an_unknown_outcome(self, sleep):
        ctx = _sending_ctx()
        post = ctx.web3.eth.send_raw_transaction
        post.side_effect = [_refused(), ReadTimeout()]
        with pytest.raises(TransactionOutcomeUnknown):
            ctx.send(TO_ADDR, b"\x01")
        assert post.call_count == 2
        assert [c.args[0] for c in sleep.call_args_list] == [0.25]

    def test_a_rejection_after_a_repeated_post_propagates_unchanged(self, sleep):
        ctx = _sending_ctx()
        ctx.web3.provider.exception_retry_configuration = _RETRY_POLICY
        post = ctx.web3.eth.send_raw_transaction
        error = Web3RPCError("nonce too low")
        post.side_effect = [_refused(), error]
        with pytest.raises(Web3RPCError) as exc_info:
            ctx.send(TO_ADDR, b"\x01")
        assert exc_info.value is error
        assert post.call_count == 2
        assert [c.args[0] for c in sleep.call_args_list] == [0.25]

    @pytest.mark.parametrize("maybe_sent", _MAYBE_SENT)
    def test_a_post_that_may_have_left_has_an_unknown_outcome(self, sleep, maybe_sent):
        ctx = _sending_ctx()
        post = ctx.web3.eth.send_raw_transaction
        error = maybe_sent()
        post.side_effect = error
        with pytest.raises(TransactionOutcomeUnknown) as exc_info:
            ctx.send(TO_ADDR, b"\x01")
        assert exc_info.value.tx_hash == SIGNED_HASH.to_0x_hex()
        assert exc_info.value.nonce == 0
        assert exc_info.value.__cause__ is error
        assert post.call_count == 1
        sleep.assert_not_called()

    def test_an_unknown_outcome_carries_the_given_nonce(self, _sleep):
        ctx = _sending_ctx()
        ctx.web3.eth.send_raw_transaction.side_effect = ReadTimeout()
        with pytest.raises(TransactionOutcomeUnknown) as exc_info:
            ctx.send(TO_ADDR, b"\x01", nonce=41)
        assert exc_info.value.nonce == 41
        ctx.web3.eth.get_transaction_count.assert_not_called()

    def test_a_rejected_post_propagates_unchanged(self, _sleep):
        ctx = _sending_ctx()
        ctx.web3.provider.exception_retry_configuration = _RETRY_POLICY
        post = ctx.web3.eth.send_raw_transaction
        error = Web3RPCError("nonce too low")
        post.side_effect = error
        with pytest.raises(Web3RPCError) as exc_info:
            ctx.send(TO_ADDR, b"\x01")
        assert exc_info.value is error
        assert post.call_count == 1

    def test_a_receipt_wait_timeout_is_a_transaction_timeout(self, _sleep):
        ctx = _sending_ctx()
        ctx.web3.eth.wait_for_transaction_receipt.side_effect = TimeExhausted("120 s")
        with pytest.raises(TimeExhausted) as exc_info:
            ctx.send(TO_ADDR, b"\x01")
        assert isinstance(exc_info.value, TransactionTimeout)
        assert exc_info.value.tx_hash == SIGNED_HASH.to_0x_hex()
        assert exc_info.value.nonce == 0

    @pytest.mark.parametrize(
        "failure",
        [*_MAYBE_SENT, _refused, lambda: Web3RPCError("internal error")],
    )
    def test_a_failed_receipt_poll_has_an_unknown_outcome(self, _sleep, failure):
        ctx = _sending_ctx()
        error = failure()
        ctx.web3.eth.wait_for_transaction_receipt.side_effect = error
        with pytest.raises(TransactionOutcomeUnknown) as exc_info:
            ctx.send(TO_ADDR, b"\x01")
        assert not isinstance(exc_info.value, TransactionTimeout)
        assert exc_info.value.tx_hash == SIGNED_HASH.to_0x_hex()
        assert exc_info.value.nonce == 0
        assert exc_info.value.__cause__ is error

    def test_waits_for_the_hash_of_the_posted_bytes(self, _sleep):
        signer = Account.from_key(PRIVATE_KEY)
        ctx = _sending_ctx(signer=signer.address)
        ctx.web3.eth.account = Account
        ctx.send(TO_ADDR, b"\x01")
        raw = ctx.web3.eth.send_raw_transaction.call_args.args[0]
        ctx.web3.eth.wait_for_transaction_receipt.assert_called_once_with(
            Web3.keccak(raw).to_0x_hex()
        )


@pytest.mark.parametrize(
    "error",
    [
        RequestsConnectionError(),
        _reset(),
        _connection_error(SSLError("handshake")),
        ReadTimeout(),
        ChunkedEncodingError(),
    ],
)
def test_a_connection_may_have_been_made(error):
    assert not _no_connection_made(error)


@pytest.mark.parametrize("never_left", _NEVER_LEFT)
def test_no_connection_was_made(never_left):
    assert _no_connection_made(never_left())


def _retrying(*allowlist: str) -> ExceptionRetryConfiguration:
    return ExceptionRetryConfiguration(
        errors=(RequestsConnectionError,), method_allowlist=list(allowlist)
    )


_URL = "http://127.0.0.1:1"


@pytest.mark.parametrize(
    ("provider", "retries"),
    [
        (object(), False),
        (HTTPProvider(_URL, exception_retry_configuration=None), False),
        (HTTPProvider(_URL, exception_retry_configuration=_RETRY_POLICY), False),
        (HTTPProvider(_URL), True),
        (
            HTTPProvider(
                _URL,
                exception_retry_configuration=_retrying("eth_sendRawTransaction"),
            ),
            True,
        ),
        (HTTPProvider(_URL, exception_retry_configuration=_retrying("eth")), True),
    ],
    ids=[
        "no-retry-attribute",
        "retries-off",
        "from-url-policy",
        "web3-default",
        "the-method",
        "eth-root",
    ],
)
def test_whether_a_provider_repeats_the_post(provider, retries):
    assert _provider_retries_post(provider) is retries


@contextmanager
def _refusing_port() -> Generator[int]:
    """A local port that refuses connections: bound, never listening, so no
    other process can take it while the test runs."""
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        yield probe.getsockname()[1]


def test_a_refused_connection_is_no_connection_made():
    """Pins the requests/urllib3 mapping `_no_connection_made` relies on."""
    session = requests.Session()
    session.trust_env = False  # a proxy from the environment would answer instead
    with _refusing_port() as port, pytest.raises(RequestsConnectionError) as exc_info:
        session.post(f"http://127.0.0.1:{port}", timeout=5)
    assert _no_connection_made(exc_info.value)


@pytest.fixture
def _no_proxy(monkeypatch: pytest.MonkeyPatch) -> None:
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.delenv(name, raising=False)


@pytest.mark.usefixtures("_no_proxy")
@patch("web3.providers.rpc.rpc.time.sleep")
@patch("ipor_fusion.core.context.time.sleep")
@patch.object(Eth, "chain_id", new_callable=PropertyMock, return_value=1)
class TestRefusedPostThroughHttpProvider:
    """The real provider stack: web3 must hand the refusal through unwrapped."""

    @contextmanager
    def _posts(self, ctx: Web3Context) -> Generator[MagicMock]:
        manager = ctx.web3.provider._request_session_manager
        with patch.object(
            manager, "make_post_request", wraps=manager.make_post_request
        ) as posts:
            yield posts

    def test_from_url_repeats_the_post_then_raises_the_refusal(self, *_patches):
        with _refusing_port() as port:
            ctx = Web3Context.from_url(f"http://127.0.0.1:{port}")
            with self._posts(ctx) as posts, pytest.raises(RequestsConnectionError):
                ctx._post_raw_transaction(b"\xf8", parse_tx_hash(SIGNED_HASH), Nonce(0))
        assert posts.call_count == 3

    def test_a_retrying_provider_has_an_unknown_outcome(self, *_patches):
        with _refusing_port() as port:
            web3 = Web3(Web3.HTTPProvider(f"http://127.0.0.1:{port}"))
            ctx = Web3Context(web3, ChainId(1))
            with self._posts(ctx) as posts, pytest.raises(TransactionOutcomeUnknown):
                ctx._post_raw_transaction(b"\xf8", parse_tx_hash(SIGNED_HASH), Nonce(0))
        # web3's default retries the builtin ConnectionError, which requests'
        # is not, so the refusal itself is posted once; the SDK adds no repeat.
        assert posts.call_count == 1


class TestGetStorageAt:
    def test_delegates_with_default_block(self):
        ctx = _make_ctx()
        slot_value = HexBytes(b"\x00" * 31 + b"\x2a")
        ctx.web3.eth.get_storage_at.return_value = slot_value

        result = ctx.get_storage_at(ADDR, 0x1234)

        assert result == slot_value
        ctx.web3.eth.get_storage_at.assert_called_once_with(
            ADDR, 0x1234, block_identifier="latest"
        )

    def test_honors_explicit_block(self):
        ctx = _make_ctx()
        ctx.web3.eth.get_storage_at.return_value = HexBytes(b"\x00" * 32)

        ctx.get_storage_at(ADDR, 7, block=123)

        ctx.web3.eth.get_storage_at.assert_called_once_with(
            ADDR, 7, block_identifier=123
        )


# ── build_transaction / estimate_gas preview (item 4) ───────────────────────


class TestPreview:
    def test_build_transaction_returns_tx_without_sending(self):
        ctx = _make_ctx(signer=ADDR)
        web3 = ctx.web3
        web3.eth.get_transaction_count.return_value = 7
        web3.eth.gas_price = 20 * GWEI
        web3.eth.get_block.return_value = {"baseFeePerGas": 15 * GWEI}
        web3.eth.estimate_gas.return_value = 21000

        tx = ctx.build_transaction(TO_ADDR, b"\x01\x02")

        # Same dict send() would submit — including the base-fee-relative ceiling.
        assert tx["to"] == TO_ADDR
        assert tx["from"] == ADDR
        assert tx["nonce"] == 7
        assert tx["maxFeePerGas"] == 15 * GWEI * 2 + 2 * GWEI
        assert tx["gas"] == int(1.25 * 21000)
        # from/to/data reach estimate_gas intact (0x-prefixed calldata).
        web3.eth.estimate_gas.assert_called_once_with(
            {"to": TO_ADDR, "from": ADDR, "data": "0x0102"}, block_identifier="latest"
        )
        web3.eth.get_transaction_count.assert_called_once_with(
            ADDR, block_identifier="latest"
        )
        web3.eth.get_block.assert_called_once_with("latest")
        web3.eth.send_raw_transaction.assert_not_called()

    def test_build_transaction_pins_nonce_base_fee_and_estimate_to_block(self):
        # A pinned build reads nonce, base fee and gas at the same block, and
        # ignores default_block: only the explicit argument pins it.
        ctx = _make_ctx(signer=ADDR).at_block(99)
        web3 = ctx.web3
        web3.eth.get_transaction_count.return_value = 3
        web3.eth.gas_price = 20 * GWEI
        web3.eth.get_block.return_value = {"baseFeePerGas": 15 * GWEI}
        web3.eth.estimate_gas.return_value = 21000

        tx = ctx.build_transaction(TO_ADDR, b"\x01\x02", block=123)

        assert tx["nonce"] == 3
        web3.eth.get_transaction_count.assert_called_once_with(
            ADDR, block_identifier=123
        )
        web3.eth.get_block.assert_called_once_with(123)
        web3.eth.estimate_gas.assert_called_once_with(
            {"to": TO_ADDR, "from": ADDR, "data": "0x0102"}, block_identifier=123
        )

    def test_build_transaction_ignores_default_block(self):
        ctx = _make_ctx(signer=ADDR).at_block(99)
        _build_tx(ctx, gas_price=20 * GWEI, block={"baseFeePerGas": 15 * GWEI})
        ctx.web3.eth.get_transaction_count.assert_called_once_with(
            ADDR, block_identifier="latest"
        )
        ctx.web3.eth.get_block.assert_called_once_with("latest")

    def test_build_transaction_takes_a_given_nonce_without_reading_it(self):
        ctx = _make_ctx(signer=ADDR)
        web3 = ctx.web3
        web3.eth.gas_price = 20 * GWEI
        web3.eth.get_block.return_value = {"baseFeePerGas": 15 * GWEI}
        web3.eth.estimate_gas.return_value = 21000

        tx = ctx.build_transaction(TO_ADDR, b"\x01\x02", block=123, nonce=0)

        assert tx["nonce"] == 0
        web3.eth.get_transaction_count.assert_not_called()
        web3.eth.get_block.assert_called_once_with(123)

    def test_build_transaction_rejects_a_negative_nonce_before_any_read(self):
        ctx = _make_ctx(signer=ADDR)
        with pytest.raises(ValueError, match="nonce must be non-negative"):
            ctx.build_transaction(TO_ADDR, b"\x01\x02", nonce=-1)
        ctx.web3.eth.get_transaction_count.assert_not_called()
        ctx.web3.eth.estimate_gas.assert_not_called()

    def test_build_transaction_requires_signer(self):
        ctx = _make_ctx()  # no signer
        with pytest.raises(ValueError, match="Signer required"):
            ctx.build_transaction(TO_ADDR, b"\x01\x02")

    def test_build_transaction_propagates_revert(self):
        # Gas estimation executes the call, so a would-revert tx raises here too.
        # The earlier fee reads need real values for the path to reach it.
        ctx = _make_ctx(signer=ADDR)
        web3 = ctx.web3
        web3.eth.get_transaction_count.return_value = 0
        web3.eth.gas_price = 20 * GWEI
        web3.eth.get_block.return_value = {"baseFeePerGas": 15 * GWEI}
        web3.eth.estimate_gas.side_effect = ValueError("execution reverted")
        with pytest.raises(ValueError, match="execution reverted"):
            ctx.build_transaction(TO_ADDR, b"\x01\x02")

    def test_estimate_gas_applies_multiplier(self):
        # A non-default multiplier proves the value is read from the ctx rather
        # than hardcoded.
        ctx = _make_ctx(signer=ADDR, gas_multiplier=2.0)
        ctx.web3.eth.estimate_gas.return_value = 21000

        assert ctx.estimate_gas(TO_ADDR, b"\x01\x02") == int(2.0 * 21000)
        ctx.web3.eth.estimate_gas.assert_called_once_with(
            {"to": TO_ADDR, "from": ADDR, "data": "0x0102"}, block_identifier="latest"
        )
        # The cheap path skips the nonce / base-fee reads build_transaction makes.
        ctx.web3.eth.get_transaction_count.assert_not_called()
        ctx.web3.eth.get_block.assert_not_called()
        ctx.web3.eth.send_raw_transaction.assert_not_called()

    def test_estimate_gas_requires_signer(self):
        ctx = _make_ctx()  # no signer
        with pytest.raises(ValueError, match="Signer required"):
            ctx.estimate_gas(TO_ADDR, b"\x01\x02")

    def test_estimate_gas_propagates_revert(self):
        # A would-revert tx surfaces the underlying revert rather than a number.
        ctx = _make_ctx(signer=ADDR)
        ctx.web3.eth.estimate_gas.side_effect = ValueError("execution reverted")
        with pytest.raises(ValueError, match="execution reverted"):
            ctx.estimate_gas(TO_ADDR, b"\x01\x02")


class TestCallPreviewDelegation:
    def test_estimate_gas_delegates_to_ctx(self):
        ctx = MagicMock(spec=Web3Context)
        ctx.estimate_gas.return_value = 12345
        call = Call(to=TO_ADDR, data=b"\x01\x02")

        assert call.estimate_gas(ctx) == 12345
        ctx.estimate_gas.assert_called_once_with(TO_ADDR, b"\x01\x02", block="latest")

    def test_estimate_gas_forwards_the_pinned_block(self):
        ctx = MagicMock(spec=Web3Context)
        call = Call(to=TO_ADDR, data=b"\x01\x02")

        call.estimate_gas(ctx, block=77)
        ctx.estimate_gas.assert_called_once_with(TO_ADDR, b"\x01\x02", block=77)

    def test_build_transaction_delegates_to_ctx(self):
        ctx = MagicMock(spec=Web3Context)
        sentinel = {"gas": 1}
        ctx.build_transaction.return_value = sentinel
        call = Call(to=TO_ADDR, data=b"\x03")

        assert call.build_transaction(ctx) is sentinel
        ctx.build_transaction.assert_called_once_with(
            TO_ADDR, b"\x03", block="latest", nonce=None
        )

    def test_build_transaction_forwards_the_pinned_block_and_nonce(self):
        ctx = MagicMock(spec=Web3Context)
        call = Call(to=TO_ADDR, data=b"\x03")

        call.build_transaction(ctx, block=77, nonce=5)
        ctx.build_transaction.assert_called_once_with(
            TO_ADDR, b"\x03", block=77, nonce=5
        )

    def test_send_forwards_the_nonce(self):
        ctx = MagicMock(spec=Web3Context)
        call = Call(to=TO_ADDR, data=b"\x03")

        call.send(ctx, nonce=5)
        ctx.send.assert_called_once_with(TO_ADDR, b"\x03", nonce=5)


# ── get_logs through a LogSource ─────────────────────────────────────────

GRANTED = "0x" + "aa" * 32
REVOKED = "0x" + "bb" * 32


class TestLogSource:
    @staticmethod
    def _ctx(source):
        ctx = _make_ctx()
        ctx.web3.eth.block_number = 500
        ctx.log_source = source
        return ctx

    def test_a_topic0_filter_is_answered_by_the_source(self):
        source = MagicMock()
        source.get_logs.return_value = ["log"]
        ctx = self._ctx(source)
        with patch("ipor_fusion.core.context.get_logs_adaptive") as scan:
            assert ctx.get_logs(ADDR, [[GRANTED, REVOKED]]) == ["log"]
        source.get_logs.assert_called_once_with(1, ADDR, [GRANTED, REVOKED], 0, 500)
        scan.assert_not_called()

    def test_the_range_is_resolved_before_the_source_is_asked(self):
        source = MagicMock()
        source.get_logs.return_value = []
        ctx = self._ctx(source)
        ctx.get_logs(ADDR, [GRANTED], from_block=10, to_block=200)
        source.get_logs.assert_called_once_with(1, ADDR, [GRANTED], 10, 200)

    def test_an_unavailable_source_falls_back_to_the_rpc_scan(self):
        source = MagicMock()
        source.get_logs.side_effect = LogSourceUnavailable("index down")
        ctx = self._ctx(source)
        with patch(
            "ipor_fusion.core.context.get_logs_adaptive", return_value=["rpc"]
        ) as scan:
            assert ctx.get_logs(ADDR, [GRANTED]) == ["rpc"]
        assert scan.call_args.args[1:5] == (ADDR, [GRANTED], None, 500)

    def test_any_other_source_error_is_not_swallowed(self):
        source = MagicMock()
        source.get_logs.side_effect = KeyError("bug")
        ctx = self._ctx(source)
        with (
            patch("ipor_fusion.core.context.get_logs_adaptive") as scan,
            pytest.raises(KeyError),
        ):
            ctx.get_logs(ADDR, [GRANTED])
        scan.assert_not_called()

    def test_a_filter_past_topic0_skips_the_source(self):
        source = MagicMock()
        ctx = self._ctx(source)
        with patch("ipor_fusion.core.context.get_logs_adaptive", return_value=[]):
            ctx.get_logs(ADDR, [GRANTED, "0x" + "00" * 32])
        source.get_logs.assert_not_called()

    def test_without_a_source_the_rpc_scan_runs(self):
        ctx = self._ctx(None)
        with patch(
            "ipor_fusion.core.context.get_logs_adaptive", return_value=[]
        ) as scan:
            ctx.get_logs(ADDR, [GRANTED])
        scan.assert_called_once()


class TestPinnedContext:
    def test_at_block_pins_block_reads_without_touching_the_original(self):
        web3 = MagicMock()
        web3.eth.get_block.side_effect = lambda block: {"number": block}
        ctx = Web3Context(web3, ChainId(1))
        pinned = ctx.at_block(123)

        assert pinned.get_block()["number"] == 123
        assert ctx.get_block()["number"] == "latest"
        assert ctx.default_block == "latest"
        assert pinned.web3 is ctx.web3

    def test_call_pins_one_read_and_keeps_the_two_argument_default(self):
        ctx = MagicMock()
        ctx.call.return_value = encode(["uint256"], [7])
        call = Call(to=ADDR, data=b"\x01", output_types=["uint256"])

        assert call.call(ctx) == 7
        assert ctx.call.call_args.args == (ADDR, b"\x01")
        call.call(ctx, block=55)
        assert ctx.call.call_args.args == (ADDR, b"\x01", 55)


class TestConcurrencyLimit:
    def _peak(self, limit: int | None) -> int:
        in_flight = 0
        peak = 0
        lock = threading.Lock()

        def make_request(provider, method, params):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.02)
            with lock:
                in_flight -= 1
            result = "0x1"
            return {"jsonrpc": "2.0", "id": 1, "result": result}

        with patch.object(HTTPProvider, "make_request", make_request):
            ctx = Web3Context.from_url(
                "http://localhost:8545", max_concurrent_requests=limit
            )
            with ThreadPoolExecutor(16) as pool:
                list(pool.map(lambda _: ctx.web3.eth.block_number, range(32)))
        return peak

    def test_requests_in_flight_never_exceed_the_limit(self):
        assert self._peak(3) <= 3

    def test_none_leaves_requests_unbounded(self):
        assert self._peak(None) > 3

    def test_a_nested_middleware_request_does_not_deadlock_at_limit_one(self):
        """web3's gas price strategy reads a block while handling
        eth_sendTransaction; that nested request must not wait on the slot the
        outer one would hold."""
        block = {
            "number": "0x1",
            "hash": "0x" + "11" * 32,
            "parentHash": "0x" + "22" * 32,
            "timestamp": "0x1",
            "baseFeePerGas": "0x1",
            "transactions": [],
        }

        def make_request(provider, method, params):
            result: Any = {
                "eth_chainId": "0x1",
                "eth_getBlockByNumber": block,
                "eth_sendTransaction": "0x" + "ab" * 32,
            }.get(method, "0x1")
            return {"jsonrpc": "2.0", "id": 1, "result": result}

        with patch.object(HTTPProvider, "make_request", make_request):
            ctx = Web3Context.from_url(
                "http://localhost:8545", max_concurrent_requests=1
            )
            done = threading.Event()

            def send():
                ctx.web3.eth.send_transaction(
                    {"from": ADDR, "to": ADDR, "gas": 21_000, "gasPrice": 1}
                )
                done.set()

            worker = threading.Thread(target=send, daemon=True)
            worker.start()
            worker.join(timeout=2)
        assert done.is_set()

    def _batch_peak(self, limit: int) -> int:
        in_flight = 0
        peak = 0
        lock = threading.Lock()

        def make_request(provider, method, params):
            return {"jsonrpc": "2.0", "id": 1, "result": "0x1"}

        def make_batch_request(provider, requests):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.02)
            with lock:
                in_flight -= 1
            return [
                {"jsonrpc": "2.0", "id": i, "result": "0x1"}
                for i, _ in enumerate(requests)
            ]

        with (
            patch.object(HTTPProvider, "make_request", make_request),
            patch.object(HTTPProvider, "make_batch_request", make_batch_request),
        ):
            ctx = Web3Context.from_url(
                "http://localhost:8545", max_concurrent_requests=limit
            )

            def one_batch(_):
                with ctx.web3.batch_requests() as batch:
                    batch.add(ctx.web3.eth.get_block_number())
                    return batch.execute()

            with ThreadPoolExecutor(8) as pool:
                list(pool.map(one_batch, range(16)))
        return peak

    def test_batches_take_one_slot_each_within_the_limit(self):
        assert self._batch_peak(2) <= 2

    def test_a_failed_request_releases_its_slot(self):
        calls = 0

        def make_request(provider, method, params):
            nonlocal calls
            calls += 1
            if calls == 2:
                raise ConnectionError("dropped")
            return {"jsonrpc": "2.0", "id": 1, "result": "0x1"}

        with patch.object(HTTPProvider, "make_request", make_request):
            ctx = Web3Context.from_url(
                "http://localhost:8545", max_concurrent_requests=1
            )
            with pytest.raises(ConnectionError):
                ctx.web3.eth.block_number  # noqa: B018
            assert ctx.web3.eth.block_number == 1

    def test_contexts_sharing_a_limit_stay_within_it_together(self):
        in_flight = 0
        peak = 0
        lock = threading.Lock()

        def make_request(provider, method, params):
            nonlocal in_flight, peak
            with lock:
                in_flight += 1
                peak = max(peak, in_flight)
            time.sleep(0.02)
            with lock:
                in_flight -= 1
            return {"jsonrpc": "2.0", "id": 1, "result": "0x1"}

        shared = InFlightLimit(3)
        with patch.object(HTTPProvider, "make_request", make_request):
            contexts = [
                Web3Context.from_url(
                    "http://localhost:8545", max_concurrent_requests=shared
                )
                for _ in range(2)
            ]
            installed = Web3(HTTPProvider("http://localhost:8545"))
            shared.install(installed)
            injected = Web3(HTTPProvider("http://localhost:8545"))
            injected.middleware_onion.inject(shared.middleware, layer=0)
            web3s = [ctx.web3 for ctx in contexts] + [installed, injected]
            with ThreadPoolExecutor(24) as pool:
                list(pool.map(lambda i: web3s[i % 4].eth.block_number, range(48)))
        assert 1 < peak <= 3

    def test_a_limit_below_one_is_rejected(self):
        with (
            patch.object(HTTPProvider, "make_request"),
            pytest.raises(ValueError, match="at least 1"),
        ):
            Web3Context.from_url("http://localhost:8545", max_concurrent_requests=0)
        with pytest.raises(ValueError, match="at least 1"):
            InFlightLimit(0)

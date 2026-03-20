"""Unit tests for WithdrawManager — mocked context, no blockchain needed."""

from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_typing import BlockNumber, ChecksumAddress
from web3 import Web3
from web3.exceptions import ContractPanicError

from ipor_fusion.core.withdraw_manager import WithdrawManager, WithdrawRequestInfo
from ipor_fusion.types import Amount, Fee, Period, Shares

FAKE_ADDRESS: ChecksumAddress = Web3.to_checksum_address(
    "0x0000000000000000000000000000000000000001"
)
FAKE_ACCOUNT: ChecksumAddress = Web3.to_checksum_address(
    "0x0000000000000000000000000000000000000002"
)


@pytest.fixture()
def ctx():
    return MagicMock()


@pytest.fixture()
def wm(ctx):
    return WithdrawManager(ctx=ctx, address=FAKE_ADDRESS)


# ── basic properties ─────────────────────────────────────────────────────────


def test_address_returns_configured_address(wm):
    assert wm.address == FAKE_ADDRESS


# ── request ──────────────────────────────────────────────────────────────────


def test_request(wm, ctx):
    ctx.send.return_value = {"status": 1}
    receipt = wm.request(Amount(1000))
    ctx.send.assert_called_once()
    assert receipt == {"status": 1}


# ── request_shares ───────────────────────────────────────────────────────────


def test_request_shares(wm, ctx):
    ctx.send.return_value = {"status": 1}
    receipt = wm.request_shares(Shares(500))
    ctx.send.assert_called_once()
    assert receipt == {"status": 1}


# ── update_withdraw_window ───────────────────────────────────────────────────


def test_update_withdraw_window(wm, ctx):
    ctx.send.return_value = {"status": 1}
    receipt = wm.update_withdraw_window(Period(3600))
    ctx.send.assert_called_once()
    assert receipt == {"status": 1}


# ── update_plasma_vault_address ──────────────────────────────────────────────


def test_update_plasma_vault_address(wm, ctx):
    ctx.send.return_value = {"status": 1}
    new_vault = Web3.to_checksum_address("0x0000000000000000000000000000000000000099")
    receipt = wm.update_plasma_vault_address(new_vault)
    ctx.send.assert_called_once()
    assert receipt == {"status": 1}


# ── release_funds branches ───────────────────────────────────────────────────


def test_release_funds_no_args(wm, ctx):
    ctx.send.return_value = {"status": 1}
    receipt = wm.release_funds()
    ctx.send.assert_called_once()
    assert receipt == {"status": 1}


def test_release_funds_timestamp_only(wm, ctx):
    ctx.send.return_value = {"status": 1}
    receipt = wm.release_funds(timestamp=1000)
    ctx.send.assert_called_once()
    assert receipt == {"status": 1}


def test_release_funds_timestamp_and_shares(wm, ctx):
    ctx.send.return_value = {"status": 1}
    receipt = wm.release_funds(timestamp=1000, shares=Shares(500))
    ctx.send.assert_called_once()
    assert receipt == {"status": 1}


def test_release_funds_shares_without_timestamp_raises(wm):
    with pytest.raises(ValueError, match="timestamp is required"):
        wm.release_funds(shares=Shares(500))


# ── read-only getters ────────────────────────────────────────────────────────


def test_get_withdraw_window(wm, ctx):
    ctx.call.return_value = encode(["uint256"], [3600])
    result = wm.get_withdraw_window()
    assert result == Period(3600)


def test_get_last_release_funds_timestamp(wm, ctx):
    ctx.call.return_value = encode(["uint256"], [12345])
    result = wm.get_last_release_funds_timestamp()
    assert result == 12345


def test_get_shares_to_release(wm, ctx):
    ctx.call.return_value = encode(["uint256"], [999])
    result = wm.get_shares_to_release()
    assert result == Shares(999)


def test_get_request_fee(wm, ctx):
    ctx.call.return_value = encode(["uint256"], [50])
    result = wm.get_request_fee()
    assert result == Fee(50)


# ── request_info ─────────────────────────────────────────────────────────────


def test_request_info(wm, ctx):
    ctx.call.return_value = encode(
        ["uint256", "uint256", "bool", "uint256"],
        [1000, 2000, True, 3600],
    )
    info = wm.request_info(FAKE_ACCOUNT)
    assert isinstance(info, WithdrawRequestInfo)
    assert info.shares == 1000
    assert info.end_withdraw_window_timestamp == 2000
    assert info.can_withdraw is True
    assert info.withdraw_window_in_seconds == 3600


# ── get_pending_requests_info ────────────────────────────────────────────────


def _event(account: str, amount: int, end_window: int) -> dict:
    """Build a fake log event with encoded data."""
    return {
        "data": encode(["address", "uint256", "uint32"], [account, amount, end_window])
    }


def test_pending_requests_aggregates_active(wm, ctx):
    current_ts = 5000
    ctx.get_block.return_value = {"timestamp": current_ts}
    ctx.get_logs.return_value = [_event(FAKE_ACCOUNT, 100, current_ts + 1000)]
    ctx.call.return_value = encode(
        ["uint256", "uint256", "bool", "uint256"],
        [200, current_ts + 500, True, 3600],
    )

    requested, ts = wm.get_pending_requests_info()
    assert requested == Shares(200)
    assert ts == current_ts - 1


def test_pending_requests_skips_expired_events(wm, ctx):
    current_ts = 5000
    ctx.get_block.return_value = {"timestamp": current_ts}
    ctx.get_logs.return_value = [_event(FAKE_ACCOUNT, 100, current_ts - 1)]

    requested, ts = wm.get_pending_requests_info()
    assert requested == Shares(0)
    assert ts == current_ts - 1


def test_pending_requests_skips_zero_amount_events(wm, ctx):
    current_ts = 5000
    ctx.get_block.return_value = {"timestamp": current_ts}
    ctx.get_logs.return_value = [_event(FAKE_ACCOUNT, 0, current_ts + 1000)]

    requested, _ = wm.get_pending_requests_info()
    assert requested == Shares(0)


def test_pending_requests_deduplicates_accounts(wm, ctx):
    current_ts = 5000
    ctx.get_block.return_value = {"timestamp": current_ts}
    ctx.get_logs.return_value = [
        _event(FAKE_ACCOUNT, 100, current_ts + 1000),
        _event(FAKE_ACCOUNT, 200, current_ts + 2000),
    ]
    ctx.call.return_value = encode(
        ["uint256", "uint256", "bool", "uint256"],
        [300, current_ts + 500, True, 3600],
    )

    requested, _ = wm.get_pending_requests_info()
    assert ctx.call.call_count == 1
    assert requested == Shares(300)


def test_pending_requests_handles_contract_panic(wm, ctx):
    current_ts = 5000
    ctx.get_block.return_value = {"timestamp": current_ts}
    ctx.get_logs.return_value = [_event(FAKE_ACCOUNT, 100, current_ts + 1000)]
    ctx.call.side_effect = ContractPanicError("arithmetic overflow")

    requested, ts = wm.get_pending_requests_info()
    assert requested == Shares(0)
    assert ts == current_ts - 1


def test_pending_requests_skips_expired_request_info(wm, ctx):
    current_ts = 5000
    ctx.get_block.return_value = {"timestamp": current_ts}
    ctx.get_logs.return_value = [_event(FAKE_ACCOUNT, 100, current_ts + 1000)]
    ctx.call.return_value = encode(
        ["uint256", "uint256", "bool", "uint256"],
        [200, current_ts - 100, False, 3600],
    )

    requested, _ = wm.get_pending_requests_info()
    assert requested == Shares(0)


def test_pending_requests_passes_from_block(wm, ctx):
    current_ts = 5000
    ctx.get_block.return_value = {"timestamp": current_ts}
    ctx.get_logs.return_value = []

    wm.get_pending_requests_info(from_block=BlockNumber(1234))
    call_kwargs = ctx.get_logs.call_args
    assert call_kwargs[1]["from_block"] == BlockNumber(1234)

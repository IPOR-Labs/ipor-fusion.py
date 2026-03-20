"""Unit tests for RewardsManager — mock _call()/_send(), verify return values."""

from unittest.mock import MagicMock

from eth_abi import encode
from web3 import Web3

from ipor_fusion.core.rewards_manager import RewardsManager
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.types import Amount

CONTRACT_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
TOKEN_A = Web3.to_checksum_address("0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa")
RECIPIENT = Web3.to_checksum_address("0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC")
FUSE_ADDR = Web3.to_checksum_address("0xdDdDddDdDdddDDddDDddDDDDdDdDDdDDdDDDDDDd")


def _make_manager():
    ctx = MagicMock()
    return RewardsManager(ctx, CONTRACT_ADDR), ctx


class TestBalanceOf:
    def test_returns_decoded_amount(self):
        mgr, ctx = _make_manager()
        ctx.call.return_value = encode(["uint256"], [42_000])

        result = mgr.balance_of()

        assert result == Amount(42_000)

    def test_returns_zero_balance(self):
        mgr, ctx = _make_manager()
        ctx.call.return_value = encode(["uint256"], [0])

        result = mgr.balance_of()

        assert result == Amount(0)


class TestGetVestingData:
    def test_decodes_all_fields(self):
        mgr, ctx = _make_manager()
        ctx.call.return_value = encode(
            ["(uint32,uint32,uint128,uint128)"],
            [(1000, 2000, 500_000, 300_000)],
        )

        vesting = mgr.get_vesting_data()

        assert vesting.vesting_time == 1000
        assert vesting.update_balance_timestamp == 2000
        assert vesting.transferred_tokens == 500_000
        assert vesting.last_update_balance == 300_000

    def test_decodes_zero_values(self):
        mgr, ctx = _make_manager()
        ctx.call.return_value = encode(
            ["(uint32,uint32,uint128,uint128)"],
            [(0, 0, 0, 0)],
        )

        vesting = mgr.get_vesting_data()

        assert vesting.vesting_time == 0
        assert vesting.update_balance_timestamp == 0
        assert vesting.transferred_tokens == 0
        assert vesting.last_update_balance == 0


class TestIsRewardFuseSupported:
    def test_returns_true(self):
        mgr, ctx = _make_manager()
        ctx.call.return_value = encode(["bool"], [True])

        assert mgr.is_reward_fuse_supported(FUSE_ADDR) is True

    def test_returns_false(self):
        mgr, ctx = _make_manager()
        ctx.call.return_value = encode(["bool"], [False])

        assert mgr.is_reward_fuse_supported(FUSE_ADDR) is False


class TestUpdateBalance:
    def test_delegates_to_send(self):
        mgr, ctx = _make_manager()
        mock_receipt = {"status": 1}
        ctx.send.return_value = mock_receipt

        result = mgr.update_balance()

        assert result == mock_receipt
        ctx.send.assert_called_once()


class TestTransfer:
    def test_delegates_to_send(self):
        mgr, ctx = _make_manager()
        mock_receipt = {"status": 1}
        ctx.send.return_value = mock_receipt

        result = mgr.transfer(TOKEN_A, RECIPIENT, Amount(1000))

        assert result == mock_receipt
        ctx.send.assert_called_once()


class TestGetRewardsFuses:
    def test_returns_checksum_addresses(self):
        mgr, ctx = _make_manager()
        ctx.call.return_value = encode(
            ["address[]"],
            [[TOKEN_A, FUSE_ADDR]],
        )

        result = mgr.get_rewards_fuses()

        assert len(result) == 2
        assert result[0] == TOKEN_A
        assert result[1] == FUSE_ADDR

    def test_returns_empty_list(self):
        mgr, ctx = _make_manager()
        ctx.call.return_value = encode(["address[]"], [[]])

        result = mgr.get_rewards_fuses()

        assert result == []


class TestClaimRewards:
    def test_delegates_to_ctx_send(self):
        mgr, ctx = _make_manager()
        mock_receipt = {"status": 1}
        ctx.send.return_value = mock_receipt

        action = FuseAction(fuse=FUSE_ADDR, data=b"\x01\x02\x03")
        result = mgr.claim_rewards([action])

        assert result == mock_receipt
        ctx.send.assert_called_once()
        call_args = ctx.send.call_args
        assert call_args[0][0] == CONTRACT_ADDR

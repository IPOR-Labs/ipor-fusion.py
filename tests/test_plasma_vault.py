"""Unit tests for PlasmaVault — mock Web3Context, verify encoding and decoding."""

from unittest.mock import MagicMock

from eth_abi import encode
from web3 import Web3

from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.types import Amount, Shares, Decimals, MarketId

VAULT_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
USER_ADDR = Web3.to_checksum_address("0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa")
TOKEN_ADDR = Web3.to_checksum_address("0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB")
FUSE_ADDR = Web3.to_checksum_address("0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC")
FUSE_ADDR_2 = Web3.to_checksum_address("0xdDdDddDdDdddDDddDDddDDDDdDdDDdDDdDDDDDDd")
ACCESS_MANAGER = Web3.to_checksum_address("0x3333333333333333333333333333333333333333")
REWARDS_MANAGER = Web3.to_checksum_address("0x4444444444444444444444444444444444444444")
PRICE_ORACLE = Web3.to_checksum_address("0x5555555555555555555555555555555555555555")
WITHDRAW_MANAGER = Web3.to_checksum_address(
    "0x6666666666666666666666666666666666666666"
)


def _make_vault() -> tuple[PlasmaVault, MagicMock]:
    ctx = MagicMock()
    vault = PlasmaVault(ctx, VAULT_ADDR)
    return vault, ctx


class TestPlasmaVaultSendMethods:
    """Methods that delegate to _send (write transactions)."""

    def test_execute(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}
        action = FuseAction(fuse=FUSE_ADDR, data=b"\x01\x02\x03")

        result = vault.execute([action])

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_deposit(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.deposit(Amount(1000), USER_ADDR)

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_mint(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.mint(Shares(500), USER_ADDR)

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_withdraw(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.withdraw(Amount(2000), USER_ADDR, USER_ADDR)

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_redeem(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.redeem(Shares(300), USER_ADDR, USER_ADDR)

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_redeem_from_request(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.redeem_from_request(Shares(100), USER_ADDR, USER_ADDR)

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_add_fuses(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}
        fuses = [FUSE_ADDR, FUSE_ADDR_2]

        result = vault.add_fuses(fuses)

        assert result == {"status": 1}
        ctx.send.assert_called_once()
        sent_to, _ = ctx.send.call_args[0]
        assert sent_to == VAULT_ADDR

    def test_set_total_supply_cap(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.set_total_supply_cap(Amount(500_000))

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_transfer(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.transfer(USER_ADDR, Amount(750))

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_approve(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.approve(USER_ADDR, Amount(999))

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_transfer_from(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.transfer_from(FUSE_ADDR, USER_ADDR, Amount(400))

        assert result == {"status": 1}
        ctx.send.assert_called_once()


class TestPlasmaVaultCallMethods:
    """Methods that delegate to _call (read-only)."""

    def test_underlying_asset_address(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address"], [TOKEN_ADDR])

        result = vault.underlying_asset_address()

        assert result == TOKEN_ADDR

    def test_get_access_manager_address(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address"], [ACCESS_MANAGER])

        result = vault.get_access_manager_address()

        assert result == ACCESS_MANAGER

    def test_get_rewards_claim_manager_address(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address"], [REWARDS_MANAGER])

        result = vault.get_rewards_claim_manager_address()

        assert result == REWARDS_MANAGER

    def test_get_price_oracle_middleware_address(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address"], [PRICE_ORACLE])

        result = vault.get_price_oracle_middleware_address()

        assert result == PRICE_ORACLE

    def test_get_fuses(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address[]"], [[FUSE_ADDR, FUSE_ADDR_2]])

        result = vault.get_fuses()

        assert result == [FUSE_ADDR, FUSE_ADDR_2]

    def test_get_fuses_empty(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address[]"], [[]])

        result = vault.get_fuses()

        assert result == []

    def test_get_instant_withdrawal_fuses(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address[]"], [[FUSE_ADDR]])

        result = vault.get_instant_withdrawal_fuses()

        assert result == [FUSE_ADDR]

    def test_get_instant_withdrawal_fuses_params(self):
        vault, ctx = _make_vault()
        param1 = b"\x01" * 32
        param2 = b"\x02" * 32
        ctx.call.return_value = encode(["bytes32[]"], [[param1, param2]])

        result = vault.get_instant_withdrawal_fuses_params(FUSE_ADDR, 0)

        assert len(result) == 2
        assert result[0] == param1
        assert result[1] == param2

    def test_get_market_substrates(self):
        vault, ctx = _make_vault()
        sub1 = b"\xaa" * 32
        sub2 = b"\xbb" * 32
        ctx.call.return_value = encode(["bytes32[]"], [[sub1, sub2]])

        result = vault.get_market_substrates(MarketId(42))

        assert len(result) == 2
        assert result[0] == sub1
        assert result[1] == sub2

    def test_decimals(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [18])

        result = vault.decimals()

        assert result == Decimals(18)

    def test_total_assets(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [1_000_000])

        result = vault.total_assets()

        assert result == Amount(1_000_000)

    def test_total_assets_in_market(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [500_000])

        result = vault.total_assets_in_market(MarketId(7))

        assert result == Amount(500_000)

    def test_balance_of(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [42_000])

        result = vault.balance_of(USER_ADDR)

        assert result == Amount(42_000)

    def test_get_total_supply_cap(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [10_000_000])

        result = vault.get_total_supply_cap()

        assert result == Amount(10_000_000)

    def test_max_withdraw(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [5_000])

        result = vault.max_withdraw(USER_ADDR)

        assert result == Amount(5_000)

    def test_convert_to_shares(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [999])

        result = vault.convert_to_shares(Amount(1000))

        assert result == Shares(999)

    def test_convert_to_assets(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [1001])

        result = vault.convert_to_assets(Shares(1000))

        assert result == Amount(1001)


class TestPlasmaVaultEventDecoding:
    """Methods that decode log events."""

    def test_get_balance_fuses(self):
        vault, ctx = _make_vault()
        event1_data = encode(["uint256", "address"], [1, FUSE_ADDR])
        event2_data = encode(["uint256", "address"], [2, FUSE_ADDR_2])
        ctx.get_logs.return_value = [
            {"data": event1_data},
            {"data": event2_data},
        ]

        result = vault.get_balance_fuses()

        assert len(result) == 2
        assert result[0].market_id == 1
        assert result[0].fuse == FUSE_ADDR
        assert result[1].market_id == 2
        assert result[1].fuse == FUSE_ADDR_2

    def test_get_balance_fuses_empty(self):
        vault, ctx = _make_vault()
        ctx.get_logs.return_value = []

        result = vault.get_balance_fuses()

        assert not result

    def test_withdraw_manager_address_returns_latest(self):
        vault, ctx = _make_vault()
        old_addr = Web3.to_checksum_address(
            "0x7777777777777777777777777777777777777777"
        )
        event1_data = encode(["address"], [old_addr])
        event2_data = encode(["address"], [WITHDRAW_MANAGER])
        ctx.get_logs.return_value = [
            {"data": event1_data, "blockNumber": 100},
            {"data": event2_data, "blockNumber": 200},
        ]

        result = vault.withdraw_manager_address()

        assert result == WITHDRAW_MANAGER

    def test_withdraw_manager_address_no_events(self):
        vault, ctx = _make_vault()
        ctx.get_logs.return_value = []

        result = vault.withdraw_manager_address()

        assert result is None

    def test_withdraw_manager_address_single_event(self):
        vault, ctx = _make_vault()
        event_data = encode(["address"], [WITHDRAW_MANAGER])
        ctx.get_logs.return_value = [
            {"data": event_data, "blockNumber": 50},
        ]

        result = vault.withdraw_manager_address()

        assert result == WITHDRAW_MANAGER

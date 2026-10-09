"""Unit tests for PlasmaVault — mock Web3Context, verify encoding and decoding."""

from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.types import Timestamp

from ipor_fusion.core.plasma_vault import (
    ManagementFeeData,
    PerformanceFeeData,
    PlasmaVault,
)
from ipor_fusion.errors import UnsupportedVaultVersionError
from ipor_fusion.fuses.base import ZERO_ADDRESS, FuseAction
from ipor_fusion.types import Amount, Decimals, Fee, MarketId, Shares

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
FEE_ACCOUNT = Web3.to_checksum_address("0x7777777777777777777777777777777777777777")


def _make_vault() -> tuple[PlasmaVault, MagicMock]:
    ctx = MagicMock()
    vault = PlasmaVault(ctx, VAULT_ADDR)
    return vault, ctx


def _make_legacy_vault() -> tuple[PlasmaVault, MagicMock]:
    """A vault that predates getActiveMarketsInBalanceFuses and keeps no
    address in the WithdrawManager slot: both reads fall back to events."""
    vault, ctx = _make_vault()
    ctx.call.return_value = b""
    ctx.get_storage_at.return_value = b"\x00" * 32
    return vault, ctx


def _word(address: str) -> bytes:
    return encode(["address"], [address])


class TestPlasmaVaultSendMethods:
    """Methods that delegate to _send (write transactions)."""

    def test_execute(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}
        action = FuseAction(fuse=FUSE_ADDR, data=b"\x01\x02\x03")

        result = vault.execute([action]).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_deposit(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.deposit(Amount(1000), USER_ADDR).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_mint(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.mint(Shares(500), USER_ADDR).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_withdraw(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.withdraw(Amount(2000), USER_ADDR, USER_ADDR).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_redeem(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.redeem(Shares(300), USER_ADDR, USER_ADDR).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_redeem_from_request(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.redeem_from_request(Shares(100), USER_ADDR, USER_ADDR).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_add_fuses(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}
        fuses = [FUSE_ADDR, FUSE_ADDR_2]

        result = vault.add_fuses(fuses).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()
        sent_to, _ = ctx.send.call_args[0]
        assert sent_to == VAULT_ADDR

    def test_update_dependency_balance_graphs(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.update_dependency_balance_graphs(
            [MarketId(1202), MarketId(41)], [[MarketId(7)], [MarketId(7)]]
        ).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()
        sent_to, sent_data = ctx.send.call_args[0]
        assert sent_to == VAULT_ADDR
        selector = Web3.keccak(
            text="updateDependencyBalanceGraphs(uint256[],uint256[][])"
        )[:4]
        assert sent_data == selector + encode(
            ["uint256[]", "uint256[][]"], [[1202, 41], [[7], [7]]]
        )

    def test_update_dependency_balance_graphs_empty(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.update_dependency_balance_graphs([], []).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()
        sent_to, sent_data = ctx.send.call_args[0]
        assert sent_to == VAULT_ADDR
        selector = Web3.keccak(
            text="updateDependencyBalanceGraphs(uint256[],uint256[][])"
        )[:4]
        assert sent_data == selector + encode(["uint256[]", "uint256[][]"], [[], []])

    def test_update_callback_handler(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}
        selector = bytes.fromhex("150b7a02")

        result = vault.update_callback_handler(FUSE_ADDR, TOKEN_ADDR, selector).send()

        assert result == {"status": 1}
        sent_to, sent_data = ctx.send.call_args[0]
        assert sent_to == VAULT_ADDR
        expected_selector = Web3.keccak(
            text="updateCallbackHandler(address,address,bytes4)"
        )[:4]
        assert sent_data == expected_selector + encode(
            ["address", "address", "bytes4"], [FUSE_ADDR, TOKEN_ADDR, selector]
        )

    def test_update_callback_handler_rejects_invalid_selector(self):
        vault, _ = _make_vault()

        for selector in (b"", b"\x01\x02\x03", b"\x01\x02\x03\x04\x05"):
            try:
                vault.update_callback_handler(FUSE_ADDR, TOKEN_ADDR, selector)
            except ValueError as error:
                assert "selector must be exactly 4 bytes" in str(error)
            else:
                raise AssertionError(f"accepted invalid selector {selector!r}")

        try:
            vault.update_callback_handler(  # type: ignore[arg-type]
                FUSE_ADDR, TOKEN_ADDR, "0x150b7a02"
            )
        except TypeError as error:
            assert str(error) == "selector must be bytes-like"
        else:
            raise AssertionError("accepted a string selector")

    def test_set_total_supply_cap(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.set_total_supply_cap(Amount(500_000)).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_convert_to_public_vault(self):
        """No-args setter: calldata is exactly the 4-byte selector."""
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        call = vault.convert_to_public_vault()
        # Selector-only, no args → 4 bytes total.
        assert len(call.calldata) == 4
        assert (
            call.calldata.hex() == Web3.keccak(text="convertToPublicVault()")[:4].hex()
        )

        result = call.send()
        assert result == {"status": 1}
        ctx.send.assert_called_once()
        sent_to, sent_data = ctx.send.call_args[0]
        assert sent_to == VAULT_ADDR
        assert sent_data == call.calldata

    def test_transfer(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.transfer(USER_ADDR, Amount(750)).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_approve(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.approve(USER_ADDR, Amount(999)).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_transfer_from(self):
        vault, ctx = _make_vault()
        ctx.send.return_value = {"status": 1}

        result = vault.transfer_from(FUSE_ADDR, USER_ADDR, Amount(400)).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()


class TestPlasmaVaultCallMethods:
    """Methods that delegate to _call (read-only)."""

    def test_underlying_asset_address(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address"], [TOKEN_ADDR])

        result = vault.underlying_asset_address().call()

        assert result == TOKEN_ADDR

    def test_get_access_manager_address(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address"], [ACCESS_MANAGER])

        result = vault.get_access_manager_address().call()

        assert result == ACCESS_MANAGER

    def test_get_rewards_claim_manager_address(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address"], [REWARDS_MANAGER])

        result = vault.get_rewards_claim_manager_address().call()

        assert result == REWARDS_MANAGER

    def test_get_price_oracle_middleware_address(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address"], [PRICE_ORACLE])

        result = vault.get_price_oracle_middleware_address().call()

        assert result == PRICE_ORACLE

    def test_price_oracle_address_prefers_the_middleware_getter(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address"], [PRICE_ORACLE])

        assert vault.price_oracle_address() == PRICE_ORACLE
        assert ctx.call.call_count == 1

    def test_price_oracle_address_falls_back_to_pre_audit_getter(self):
        vault, ctx = _make_vault()
        ctx.call.side_effect = [b"", encode(["address"], [PRICE_ORACLE])]

        assert vault.price_oracle_address() == PRICE_ORACLE
        selectors = [bytes(c.args[1][:4]) for c in ctx.call.call_args_list]
        assert selectors == [
            function_signature_to_4byte_selector("getPriceOracleMiddleware()"),
            function_signature_to_4byte_selector("getPriceOracle()"),
        ]

    def test_price_oracle_address_without_either_getter(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = b""

        with pytest.raises(UnsupportedVaultVersionError, match="getPriceOracle"):
            vault.price_oracle_address()

    def test_price_oracle_address_revert_propagates(self):
        vault, ctx = _make_vault()
        ctx.call.side_effect = ContractLogicError("execution reverted")

        with pytest.raises(ContractLogicError):
            vault.price_oracle_address()

    def test_get_fuses(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address[]"], [[FUSE_ADDR, FUSE_ADDR_2]])

        result = vault.get_fuses().call()

        assert result == [FUSE_ADDR, FUSE_ADDR_2]

    def test_get_fuses_empty(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address[]"], [[]])

        result = vault.get_fuses().call()

        assert result == []

    def test_get_instant_withdrawal_fuses(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["address[]"], [[FUSE_ADDR]])

        result = vault.get_instant_withdrawal_fuses().call()

        assert result == [FUSE_ADDR]

    def test_get_instant_withdrawal_fuses_params(self):
        vault, ctx = _make_vault()
        param1 = b"\x01" * 32
        param2 = b"\x02" * 32
        ctx.call.return_value = encode(["bytes32[]"], [[param1, param2]])

        result = vault.get_instant_withdrawal_fuses_params(FUSE_ADDR, 0).call()

        assert len(result) == 2
        assert result[0] == param1
        assert result[1] == param2

    def test_get_market_substrates(self):
        vault, ctx = _make_vault()
        sub1 = b"\xaa" * 32
        sub2 = b"\xbb" * 32
        ctx.call.return_value = encode(["bytes32[]"], [[sub1, sub2]])

        result = vault.get_market_substrates(MarketId(42)).call()

        assert len(result) == 2
        assert result[0] == sub1
        assert result[1] == sub2

    def test_decimals(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [18])

        result = vault.decimals().call()

        assert result == Decimals(18)

    def test_total_assets(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [1_000_000])

        result = vault.total_assets().call()

        assert result == Amount(1_000_000)

    def test_total_assets_in_market(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [500_000])

        result = vault.total_assets_in_market(MarketId(7)).call()

        assert result == Amount(500_000)

    def test_balance_of(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [42_000])

        result = vault.balance_of(USER_ADDR).call()

        assert result == Amount(42_000)

    def test_get_total_supply_cap(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [10_000_000])

        result = vault.get_total_supply_cap().call()

        assert result == Amount(10_000_000)

    def test_max_withdraw(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [5_000])

        result = vault.max_withdraw(USER_ADDR).call()

        assert result == Amount(5_000)

    def test_convert_to_shares(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [999])

        result = vault.convert_to_shares(Amount(1000)).call()

        assert result == Shares(999)

    def test_convert_to_assets(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [1001])

        result = vault.convert_to_assets(Shares(1000)).call()

        assert result == Amount(1001)


def _balance_fuse_logs(added: list[dict], removed: list[dict]) -> list[dict]:
    """Tag each log with its event's topic0, as the single OR-filtered
    eth_getLogs behind `get_balance_fuses` returns them."""
    added_topic = Web3.keccak(text="BalanceFuseAdded(uint256,address)")
    removed_topic = Web3.keccak(text="BalanceFuseRemoved(uint256,address)")
    return [{**log, "topics": [added_topic]} for log in added] + [
        {**log, "topics": [removed_topic]} for log in removed
    ]


class TestPlasmaVaultEventDecoding:
    """Methods that decode log events."""

    def test_get_balance_fuses(self):
        vault, ctx = _make_legacy_vault()
        added = [
            {
                "data": encode(["uint256", "address"], [1, FUSE_ADDR]),
                "blockNumber": 10,
                "logIndex": 0,
            },
            {
                "data": encode(["uint256", "address"], [2, FUSE_ADDR_2]),
                "blockNumber": 11,
                "logIndex": 0,
            },
        ]
        ctx.get_logs.return_value = _balance_fuse_logs(added, [])

        result = vault.get_balance_fuses()

        assert len(result) == 2
        by_market = {bf.market_id: bf.fuse for bf in result}
        assert by_market == {1: FUSE_ADDR, 2: FUSE_ADDR_2}

    def test_get_balance_fuses_nets_removed_entries(self):
        vault, ctx = _make_legacy_vault()
        added = [
            {
                "data": encode(["uint256", "address"], [1, FUSE_ADDR]),
                "blockNumber": 10,
                "logIndex": 0,
            },
            {
                "data": encode(["uint256", "address"], [2, FUSE_ADDR_2]),
                "blockNumber": 11,
                "logIndex": 0,
            },
        ]
        removed = [
            {
                "data": encode(["uint256", "address"], [1, FUSE_ADDR]),
                "blockNumber": 12,
                "logIndex": 0,
            }
        ]
        ctx.get_logs.return_value = _balance_fuse_logs(added, removed)

        result = vault.get_balance_fuses()

        assert len(result) == 1
        assert result[0].market_id == 2
        assert result[0].fuse == FUSE_ADDR_2

    def test_get_balance_fuses_deduplicates_per_market(self):
        vault, ctx = _make_legacy_vault()
        added = [
            {
                "data": encode(["uint256", "address"], [1, FUSE_ADDR]),
                "blockNumber": 10,
                "logIndex": 0,
            },
            {
                "data": encode(["uint256", "address"], [1, FUSE_ADDR_2]),
                "blockNumber": 20,
                "logIndex": 0,
            },
        ]
        ctx.get_logs.return_value = _balance_fuse_logs(added, [])

        result = vault.get_balance_fuses()

        assert len(result) == 1
        assert result[0].market_id == 1
        assert result[0].fuse == FUSE_ADDR_2

    def test_get_balance_fuses_picks_latest_on_unsorted_added(self):
        """Provider may return logs out of order; chronological replay must win."""
        vault, ctx = _make_legacy_vault()
        added = [
            {
                "data": encode(["uint256", "address"], [1, FUSE_ADDR_2]),
                "blockNumber": 20,
                "logIndex": 0,
            },
            {
                "data": encode(["uint256", "address"], [1, FUSE_ADDR]),
                "blockNumber": 10,
                "logIndex": 0,
            },
        ]
        ctx.get_logs.return_value = _balance_fuse_logs(added, [])

        result = vault.get_balance_fuses()

        assert len(result) == 1
        assert result[0].market_id == 1
        assert result[0].fuse == FUSE_ADDR_2

    def test_get_balance_fuses_readded_after_removal(self):
        """Add -> Remove -> Add of the same fuse must result in active fuse."""
        vault, ctx = _make_legacy_vault()
        added = [
            {
                "data": encode(["uint256", "address"], [1, FUSE_ADDR]),
                "blockNumber": 10,
                "logIndex": 0,
            },
            {
                "data": encode(["uint256", "address"], [1, FUSE_ADDR]),
                "blockNumber": 30,
                "logIndex": 0,
            },
        ]
        removed = [
            {
                "data": encode(["uint256", "address"], [1, FUSE_ADDR]),
                "blockNumber": 20,
                "logIndex": 0,
            }
        ]
        ctx.get_logs.return_value = _balance_fuse_logs(added, removed)

        result = vault.get_balance_fuses()

        assert len(result) == 1
        assert result[0].market_id == 1
        assert result[0].fuse == FUSE_ADDR

    def test_get_balance_fuses_same_block_logindex_tiebreak(self):
        """Events in the same block are ordered by logIndex."""
        vault, ctx = _make_legacy_vault()
        added = [
            {
                "data": encode(["uint256", "address"], [1, FUSE_ADDR_2]),
                "blockNumber": 10,
                "logIndex": 2,
            },
            {
                "data": encode(["uint256", "address"], [1, FUSE_ADDR]),
                "blockNumber": 10,
                "logIndex": 1,
            },
        ]
        ctx.get_logs.return_value = _balance_fuse_logs(added, [])

        result = vault.get_balance_fuses()

        assert len(result) == 1
        assert result[0].fuse == FUSE_ADDR_2

    def test_get_balance_fuses_reads_both_events_in_one_query(self):
        vault, ctx = _make_legacy_vault()
        ctx.get_logs.return_value = []

        vault.get_balance_fuses()

        ctx.get_logs.assert_called_once()
        (topic0_alternatives,) = ctx.get_logs.call_args.kwargs["topics"]
        assert topic0_alternatives == [
            Web3.keccak(text="BalanceFuseAdded(uint256,address)").to_0x_hex(),
            Web3.keccak(text="BalanceFuseRemoved(uint256,address)").to_0x_hex(),
        ]

    def test_get_balance_fuses_empty(self):
        vault, ctx = _make_legacy_vault()
        ctx.get_logs.return_value = _balance_fuse_logs([], [])

        result = vault.get_balance_fuses()

        assert not result

    def test_withdraw_manager_address_returns_latest(self):
        vault, ctx = _make_legacy_vault()
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
        vault, ctx = _make_legacy_vault()
        ctx.get_logs.return_value = []

        result = vault.withdraw_manager_address()

        assert result is None

    def test_withdraw_manager_address_single_event(self):
        vault, ctx = _make_legacy_vault()
        event_data = encode(["address"], [WITHDRAW_MANAGER])
        ctx.get_logs.return_value = [
            {"data": event_data, "blockNumber": 50},
        ]

        result = vault.withdraw_manager_address()

        assert result == WITHDRAW_MANAGER

    def test_withdraw_manager_address_zero_address_means_unset(self):
        # Legacy vaults emit WithdrawManagerChanged(address(0)) at init;
        # the zero address must not be reported as a queryable manager.
        vault, ctx = _make_legacy_vault()
        event_data = encode(["address"], [ZERO_ADDRESS])
        ctx.get_logs.return_value = [
            {"data": event_data, "blockNumber": 50},
        ]

        result = vault.withdraw_manager_address()

        assert result is None


class TestPlasmaVaultStorageReads:
    def test_balance_fuses_from_storage(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256[]"], [[3, 7, 3]])
        slots = {
            int.from_bytes(
                Web3.keccak(encode(["uint256", "uint256"], [market_id, base]))
            ): fuse
            for market_id, fuse in ((3, FUSE_ADDR), (7, FUSE_ADDR_2))
            for base in [
                0x150144DD6AF711BAC4392499881EC6649090601BD196A5ECE5174C1400B1F700
            ]
        }
        ctx.get_storage_at.side_effect = lambda _addr, slot: _word(slots[slot])

        result = vault.get_balance_fuses()

        assert [(bf.market_id, bf.fuse) for bf in result] == [
            (3, FUSE_ADDR),
            (7, FUSE_ADDR_2),
        ]
        ctx.get_logs.assert_not_called()

    def test_balance_fuses_without_markets(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256[]"], [[]])

        assert vault.get_balance_fuses() == []
        ctx.get_logs.assert_not_called()

    def test_balance_fuses_view_revert_falls_back_to_events(self):
        vault, ctx = _make_vault()
        ctx.call.side_effect = ContractLogicError("execution reverted")
        ctx.get_logs.return_value = []

        assert vault.get_balance_fuses() == []
        ctx.get_logs.assert_called_once()

    def test_withdraw_manager_from_storage(self):
        vault, ctx = _make_vault()
        ctx.get_storage_at.return_value = _word(WITHDRAW_MANAGER)

        assert vault.withdraw_manager_address() == WITHDRAW_MANAGER
        (_, slot), _ = ctx.get_storage_at.call_args
        assert slot == (
            0x465D2FF0062318FE6F4C7E9AC78CFCD70BC86A1D992722875EF83A9770513100
        )
        ctx.get_logs.assert_not_called()


LEGACY_WM_SLOT = 0xB37E8684757599DA669B8AEA811EE2B3693B2582D2C730FAB3F4965FA2EC3E11


def _legacy_slot_vault(owner: str) -> tuple[PlasmaVault, MagicMock]:
    """Empty WithdrawManager slot, WITHDRAW_MANAGER in the legacy slot, and
    `getPlasmaVaultAddress()` there answering ``owner``."""
    vault, ctx = _make_vault()
    ctx.get_storage_at.side_effect = lambda _addr, slot: (
        _word(WITHDRAW_MANAGER) if slot == LEGACY_WM_SLOT else b"\x00" * 32
    )
    ctx.call.return_value = _word(owner)
    return vault, ctx


class TestWithdrawManagerLegacySlot:
    def test_bound_manager_in_the_legacy_slot(self):
        vault, ctx = _legacy_slot_vault(VAULT_ADDR)

        assert vault.withdraw_manager_address() == WITHDRAW_MANAGER
        ctx.get_logs.assert_not_called()
        (to, data), _ = ctx.call.call_args
        assert to == WITHDRAW_MANAGER
        assert bytes(data) == function_signature_to_4byte_selector(
            "getPlasmaVaultAddress()"
        )

    def test_foreign_value_in_the_legacy_slot_falls_back_to_events(self):
        vault, ctx = _legacy_slot_vault(USER_ADDR)
        ctx.get_logs.return_value = []

        assert vault.withdraw_manager_address() is None
        ctx.get_logs.assert_called_once()

    def test_non_manager_in_the_legacy_slot_falls_back_to_events(self):
        vault, ctx = _legacy_slot_vault(VAULT_ADDR)
        ctx.call.return_value = b""
        ctx.get_logs.return_value = [
            {"data": _word(FEE_ACCOUNT), "blockNumber": 1},
        ]

        assert vault.withdraw_manager_address() == FEE_ACCOUNT


class TestPlasmaVaultFeeData:
    """Governance fee-data getters (structs decoded to dataclasses)."""

    def test_get_performance_fee_data(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["(address,uint16)"], [(FEE_ACCOUNT, 1000)])

        result = vault.get_performance_fee_data().call()

        assert result == PerformanceFeeData(
            fee_account=FEE_ACCOUNT, fee_in_percentage=Fee(1000)
        )

    def test_get_management_fee_data(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(
            ["(address,uint16,uint32)"], [(FEE_ACCOUNT, 100, 1_750_000_000)]
        )

        result = vault.get_management_fee_data().call()

        assert result == ManagementFeeData(
            fee_account=FEE_ACCOUNT,
            fee_in_percentage=Fee(100),
            last_update_timestamp=Timestamp(1_750_000_000),
        )

    def test_get_unrealized_management_fee(self):
        vault, ctx = _make_vault()
        ctx.call.return_value = encode(["uint256"], [123_456])

        result = vault.get_unrealized_management_fee().call()

        assert result == Amount(123_456)

    def test_selectors(self):
        encoder = PlasmaVault.encoder()
        for call, signature in [
            (encoder.get_performance_fee_data(), "getPerformanceFeeData()"),
            (encoder.get_management_fee_data(), "getManagementFeeData()"),
            (
                encoder.get_unrealized_management_fee(),
                "getUnrealizedManagementFee()",
            ),
        ]:
            assert call.calldata == function_signature_to_4byte_selector(signature), (
                signature
            )

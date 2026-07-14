"""Unit tests for AccessManager role-account queries — mock Web3Context."""

from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from web3 import Web3
from web3.exceptions import ContractLogicError

# Top-level imports on purpose — they also exercise the __init__ exports.
from ipor_fusion import (
    AccessManager,
    ContractNotFoundError,
    NotPlasmaVaultError,
    RoleAccount,
    resolve_access_manager,
    role_account_sort_key,
)
from ipor_fusion.types import Period, RoleId

MANAGER_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
VAULT_ADDR = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")
ALICE = Web3.to_checksum_address("0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa")
BOB = Web3.to_checksum_address("0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB")


def _grant_event(role_id: int, account: str) -> dict:
    return {
        "topics": [
            b"\x00" * 32,  # event signature hash — not read by the decoder
            encode(["uint64"], [role_id]),
            encode(["address"], [account]),
        ]
    }


def _manager_with(
    events: list[dict], is_member: bool = True, execution_delay: int = 0
) -> AccessManager:
    ctx = MagicMock()
    ctx.get_logs.return_value = events
    ctx.call.return_value = encode(["bool", "uint32"], [is_member, execution_delay])
    return AccessManager(ctx, MANAGER_ADDR)


class TestGetAllRoleAccounts:
    def test_returns_confirmed_members(self):
        manager = _manager_with([_grant_event(1, ALICE), _grant_event(100, BOB)])

        accounts = manager.get_all_role_accounts()

        assert [(ra.role_id, ra.account) for ra in accounts] == [
            (1, ALICE),
            (100, BOB),
        ]

    def test_skips_revoked_members(self):
        manager = _manager_with([_grant_event(1, ALICE)], is_member=False)

        assert manager.get_all_role_accounts() == []

    def test_execution_delay_passthrough(self):
        manager = _manager_with([_grant_event(1, ALICE)], execution_delay=3600)

        (account,) = manager.get_all_role_accounts()

        assert account.execution_delay == 3600

    def test_role_name_property(self):
        manager = _manager_with([_grant_event(100, ALICE), _grant_event(1234, BOB)])

        atomist, unknown = manager.get_all_role_accounts()

        assert atomist.role_name == "ATOMIST_ROLE"
        assert unknown.role_name == "UNKNOWN_ROLE_1234"

    def test_deduplicates_repeated_grants(self):
        # A re-granted (role, account) emits two RoleGranted events but is one
        # current membership.
        manager = _manager_with([_grant_event(1, ALICE), _grant_event(1, ALICE)])

        accounts = manager.get_all_role_accounts()

        assert len(accounts) == 1


class TestGetAccountsWithRole:
    def test_filters_by_role(self):
        manager = _manager_with([_grant_event(1, ALICE), _grant_event(100, BOB)])

        accounts = manager.get_accounts_with_role(100)

        assert [(ra.role_id, ra.account) for ra in accounts] == [(100, BOB)]

    def test_deduplicates_repeated_grants(self):
        manager = _manager_with([_grant_event(100, BOB), _grant_event(100, BOB)])

        assert len(manager.get_accounts_with_role(100)) == 1


class TestRoleAccountHelpers:
    @staticmethod
    def _role_account(role_id: int, account: str, delay: int = 0) -> RoleAccount:
        return RoleAccount(
            account=account,  # type: ignore[arg-type]
            role_id=RoleId(role_id),
            is_member=True,
            execution_delay=Period(delay),
        )

    def test_to_dict_is_the_canonical_row(self):
        assert self._role_account(100, ALICE, delay=60).to_dict() == {
            "account": ALICE,
            "role_id": 100,
            "role_name": "ATOMIST_ROLE",
            "is_member": True,
            "execution_delay": 60,
        }

    def test_sort_key_is_case_insensitive_then_by_role(self):
        accounts = [
            self._role_account(1, "0xBBBB"),
            self._role_account(2, "0xaaaa"),
            self._role_account(1, "0xaaaa"),
        ]

        ordered = sorted(accounts, key=role_account_sort_key)

        assert [(ra.account, ra.role_id) for ra in ordered] == [
            ("0xaaaa", 1),
            ("0xaaaa", 2),
            ("0xBBBB", 1),
        ]


class TestResolveAccessManager:
    @staticmethod
    def _ctx(bytecode: bytes = b"\x60\x80") -> MagicMock:
        ctx = MagicMock()
        ctx.default_block = "latest"
        ctx.web3.eth.get_code.return_value = bytecode
        return ctx

    def test_no_contract_raises(self):
        ctx = self._ctx(bytecode=b"")

        with pytest.raises(ContractNotFoundError, match="No contract found"):
            resolve_access_manager(ctx, VAULT_ADDR)

    def test_get_code_respects_pinned_block(self):
        ctx = self._ctx(bytecode=b"")
        ctx.default_block = 12345

        with pytest.raises(ContractNotFoundError, match="at block 12345"):
            resolve_access_manager(ctx, VAULT_ADDR)
        ctx.web3.eth.get_code.assert_called_once_with(
            VAULT_ADDR, block_identifier=12345
        )

    def test_empty_return_raises_not_a_vault(self):
        # Fallback-bearing contracts answer the unknown selector with empty
        # data; the real eth_abi decode raises InsufficientDataBytes.
        ctx = self._ctx()
        ctx.call.return_value = b""

        with pytest.raises(NotPlasmaVaultError, match="does not appear"):
            resolve_access_manager(ctx, VAULT_ADDR)

    def test_revert_raises_not_a_vault(self):
        # Contracts without a fallback revert on the unknown selector.
        ctx = self._ctx()
        ctx.call.side_effect = ContractLogicError("execution reverted")

        with pytest.raises(NotPlasmaVaultError, match="does not appear"):
            resolve_access_manager(ctx, VAULT_ADDR)

    def test_message_lookalike_propagates(self):
        # Detection is typed, not string-matched: a generic exception whose
        # text mimics the decode error must NOT be misclassified.
        ctx = self._ctx()
        ctx.call.side_effect = Exception("Tried to read 32 bytes, only got 0 bytes.")

        with pytest.raises(Exception, match="Tried to read") as excinfo:
            resolve_access_manager(ctx, VAULT_ADDR)
        assert not isinstance(excinfo.value, NotPlasmaVaultError)

    def test_other_errors_propagate_unchanged(self):
        ctx = self._ctx()
        ctx.call.side_effect = RuntimeError("rpc down")

        with pytest.raises(RuntimeError, match="rpc down"):
            resolve_access_manager(ctx, VAULT_ADDR)

    def test_happy_path_returns_manager(self):
        ctx = self._ctx()
        ctx.call.return_value = encode(["address"], [MANAGER_ADDR])

        manager = resolve_access_manager(ctx, VAULT_ADDR)

        assert isinstance(manager, AccessManager)
        assert manager.address == MANAGER_ADDR

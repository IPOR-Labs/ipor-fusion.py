"""Unit tests for AccessManager role-account queries — mock Web3Context."""

from unittest.mock import MagicMock

from eth_abi import encode
from web3 import Web3

from ipor_fusion.core.access import AccessManager

MANAGER_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
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

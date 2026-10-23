"""Unit tests for AccessManager role-account queries — mock Web3Context."""

from unittest.mock import MagicMock

import pytest
from _multicall import decode_aggregate3, is_aggregate3, multicall_aware
from eth_abi import decode, encode
from web3 import Web3
from web3.exceptions import ContractLogicError

# Top-level imports on purpose — they also exercise the __init__ exports.
from ipor_fusion import (
    AccessManager,
    CallPermission,
    ContractNotFoundError,
    NotPlasmaVaultError,
    OwnerNotFoundError,
    PlasmaVault,
    RoleAccount,
    Roles,
    parse_access_manager_address,
    parse_plasma_vault_address,
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
) -> tuple[AccessManager, MagicMock]:
    ctx = MagicMock()
    ctx.get_logs.return_value = events
    ctx.call.side_effect = multicall_aware(
        lambda _to, _data: encode(["bool", "uint32"], [is_member, execution_delay])
    )
    return AccessManager(MANAGER_ADDR), ctx


class TestGetAllRoleAccounts:
    def test_returns_confirmed_members(self):
        manager, ctx = _manager_with([_grant_event(1, ALICE), _grant_event(100, BOB)])

        accounts = manager.read_all_role_accounts(ctx)

        assert [(ra.role_id, ra.account) for ra in accounts] == [
            (1, ALICE),
            (100, BOB),
        ]

    def test_skips_revoked_members(self):
        manager, ctx = _manager_with([_grant_event(1, ALICE)], is_member=False)

        assert manager.read_all_role_accounts(ctx) == []

    def test_execution_delay_passthrough(self):
        manager, ctx = _manager_with([_grant_event(1, ALICE)], execution_delay=3600)

        (account,) = manager.read_all_role_accounts(ctx)

        assert account.execution_delay == 3600

    def test_role_name_property(self):
        manager, ctx = _manager_with(
            [_grant_event(100, ALICE), _grant_event(1234, BOB)]
        )

        atomist, unknown = manager.read_all_role_accounts(ctx)

        assert atomist.role_name == "ATOMIST_ROLE"
        assert unknown.role_name == "UNKNOWN_ROLE_1234"

    def test_deduplicates_repeated_grants(self):
        # A re-granted (role, account) emits two RoleGranted events but is one
        # current membership.
        manager, ctx = _manager_with([_grant_event(1, ALICE), _grant_event(1, ALICE)])

        accounts = manager.read_all_role_accounts(ctx)

        assert len(accounts) == 1

    def test_resolves_all_candidates_in_one_multicall(self):
        manager, ctx = _manager_with(
            [_grant_event(1, ALICE), _grant_event(100, BOB), _grant_event(1, ALICE)]
        )

        manager.read_all_role_accounts(ctx)

        ((to, data), _kwargs) = ctx.call.call_args
        assert ctx.call.call_count == 1
        assert is_aggregate3(to, data)
        role_checks = [
            decode(["uint64", "address"], calldata[4:])
            for _target, calldata in decode_aggregate3(data)
        ]
        assert role_checks == [(1, ALICE.lower()), (100, BOB.lower())]

    def test_keeps_members_and_drops_revoked_per_account(self):
        ctx = MagicMock()
        ctx.get_logs.return_value = [_grant_event(1, ALICE), _grant_event(1, BOB)]

        def has_role(_to: str, data: bytes) -> bytes:
            (_role, account) = decode(["uint64", "address"], data[4:])
            is_member = Web3.to_checksum_address(account) == BOB
            return encode(["bool", "uint32"], [is_member, 0])

        ctx.call.side_effect = multicall_aware(has_role)

        accounts = AccessManager(MANAGER_ADDR).read_all_role_accounts(ctx)

        assert [ra.account for ra in accounts] == [BOB]

    def test_no_grants_skips_the_rpc(self):
        manager, ctx = _manager_with([])

        assert manager.read_all_role_accounts(ctx) == []
        ctx.call.assert_not_called()


class TestGetAccountsWithRole:
    def test_filters_by_role(self):
        manager, ctx = _manager_with([_grant_event(1, ALICE), _grant_event(100, BOB)])

        accounts = manager.read_accounts_with_role(ctx, 100)

        assert [(ra.role_id, ra.account) for ra in accounts] == [(100, BOB)]

    def test_deduplicates_repeated_grants(self):
        manager, ctx = _manager_with([_grant_event(100, BOB), _grant_event(100, BOB)])

        assert len(manager.read_accounts_with_role(ctx, 100)) == 1


class TestReadOwner:
    def test_returns_the_first_owner(self):
        manager, ctx = _manager_with([_grant_event(1, ALICE), _grant_event(1, BOB)])

        assert manager.read_owner(ctx) == ALICE

    def test_raises_when_no_account_holds_the_owner_role(self):
        manager, ctx = _manager_with([_grant_event(1, ALICE)], is_member=False)

        with pytest.raises(OwnerNotFoundError, match="OWNER_ROLE"):
            manager.read_owner(ctx)


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


SELECTOR = bytes.fromhex("6e553f65")  # deposit(uint256,address)


class TestPermissionReads:
    manager = AccessManager(parse_access_manager_address(MANAGER_ADDR))

    def test_can_call_encodes_and_decodes(self):
        call = self.manager.can_call(ALICE, VAULT_ADDR, SELECTOR)

        selector = Web3.keccak(text="canCall(address,address,bytes4)")[:4]
        assert call.calldata == selector + encode(
            ["address", "address", "bytes4"], [ALICE, VAULT_ADDR, SELECTOR]
        )
        assert call.decode(encode(["bool", "uint32"], [True, 0])) == CallPermission(
            immediate=True, delay=Period(0)
        )

    @pytest.mark.parametrize(
        ("immediate", "delay", "allowed"),
        [(True, 0, True), (False, 3600, True), (False, 0, False)],
    )
    def test_allowed_covers_scheduled_calls(self, immediate, delay, allowed):
        permission = self.manager.can_call(ALICE, VAULT_ADDR, SELECTOR).decode(
            encode(["bool", "uint32"], [immediate, delay])
        )

        assert permission.delay == delay
        assert permission.allowed is allowed

    def test_is_target_closed(self):
        call = self.manager.is_target_closed(VAULT_ADDR)

        assert call.calldata[:4] == Web3.keccak(text="isTargetClosed(address)")[:4]
        assert call.decode(encode(["bool"], [True])) is True

    def test_get_account_lock_time(self):
        call = self.manager.get_account_lock_time(ALICE)

        assert call.calldata == Web3.keccak(text="getAccountLockTime(address)")[
            :4
        ] + encode(["address"], [ALICE])
        assert call.decode(encode(["uint256"], [1_700_000_000])) == 1_700_000_000


def test_hash_operation_matches_the_deployed_manager():
    # hashOperation(caller, vault, execute([]) ++ 0x0102) as the Base manager of
    # vault 0xc4c00d8b... answered it, read with eth_call on 2026-10-23.
    vault = Web3.to_checksum_address("0xc4c00d8b323f37527eeda27c87412378be9f68ec")
    caller = Web3.to_checksum_address("0x48d3615d78B152819ea0367adF7b9944e399ac9a")
    data = bytes.fromhex(
        "baae8abf"  # execute((address,bytes)[])
        "0000000000000000000000000000000000000000000000000000000000000020"
        "0000000000000000000000000000000000000000000000000000000000000000"
        "0102"
    )
    assert AccessManager.hash_operation(caller, vault, data).hex() == (
        "449c1b0678d9666078c86be94b86c7f4bfe8fe012bdacb56246db911e78b7ebb"
    )


class TestCallPlan:
    manager = AccessManager(parse_access_manager_address(MANAGER_ADDR))
    target_call = PlasmaVault(parse_plasma_vault_address(VAULT_ADDR)).execute([])
    NOW = 10_000

    def _ctx(self, *, immediate: bool, delay: int, scheduled: int = 0):
        def handler(_to: str, data: bytes) -> bytes:
            selector = bytes(data[:4])
            if selector == Web3.keccak(text="canCall(address,address,bytes4)")[:4]:
                return encode(["bool", "uint32"], [immediate, delay])
            assert selector == Web3.keccak(text="getSchedule(bytes32)")[:4]
            return encode(["uint48"], [scheduled])

        ctx = MagicMock()
        ctx.default_block = "finalized"
        ctx.at_block.return_value = ctx
        ctx.call.side_effect = multicall_aware(handler)
        ctx.web3.eth.get_block.return_value = {"number": 777, "timestamp": self.NOW}
        return ctx

    def _plan(self, *, via_manager: bool = False, **kwargs):
        return self.manager.read_call_plan(
            self._ctx(**kwargs), self.target_call, caller=ALICE, via_manager=via_manager
        )

    def test_immediate_sends_the_call_itself(self):
        plan = self._plan(immediate=True, delay=0)
        assert plan.kind == "immediate"
        assert plan.next_call is not None
        assert (plan.next_call.to, plan.next_call.data) == (
            self.target_call.to,
            self.target_call.data,
        )

    def test_the_block_the_context_names_is_resolved_once_and_pinned(self):
        ctx = self._ctx(immediate=False, delay=3600, scheduled=self.NOW)
        self.manager.read_call_plan(ctx, self.target_call, caller=ALICE)
        ctx.web3.eth.get_block.assert_called_once_with("finalized")
        ctx.at_block.assert_called_once_with(777)

    def test_forbidden_sends_nothing(self):
        plan = self._plan(immediate=False, delay=0)
        assert (plan.kind, plan.next_call) == ("forbidden", None)

    def test_a_delayed_caller_schedules_first(self):
        plan = self._plan(immediate=False, delay=3600)
        assert plan.kind == "schedule"
        assert plan.next_call is not None
        assert plan.next_call.data == Web3.keccak(
            text="schedule(address,bytes,uint48)"
        )[:4] + encode(
            ["address", "bytes", "uint48"],
            [self.target_call.to, self.target_call.data, 0],
        )
        assert plan.operation == AccessManager.hash_operation(
            ALICE, self.target_call.to, self.target_call.data
        )

    def test_waiting_then_ready_through_the_manager(self):
        waiting = self._plan(immediate=False, delay=3600, scheduled=self.NOW + 1)
        assert (waiting.kind, waiting.scheduled_at, waiting.next_call) == (
            "waiting",
            self.NOW + 1,
            None,
        )
        ready = self._plan(immediate=False, delay=3600, scheduled=self.NOW)
        assert ready.kind == "ready"
        assert ready.next_call is not None
        # Sent as the call itself, so the target consumes the schedule and still
        # sees the caller as the sender.
        assert (ready.next_call.to, ready.next_call.data) == (
            self.target_call.to,
            self.target_call.data,
        )
        relayed = self._plan(
            immediate=False, delay=3600, scheduled=self.NOW, via_manager=True
        )
        assert relayed.next_call is not None
        assert relayed.next_call.to == MANAGER_ADDR
        assert relayed.next_call.data == Web3.keccak(text="execute(address,bytes)")[
            :4
        ] + encode(["address", "bytes"], [self.target_call.to, self.target_call.data])

    def test_calls_on_the_manager_itself_are_not_planned(self):
        grant = self.manager.grant_role(Roles.ALPHA_ROLE, ALICE, Period(0))
        with pytest.raises(ValueError, match="access manager itself"):
            self.manager.read_call_plan(MagicMock(), grant, caller=ALICE)

    def test_cancel_encodes_caller_target_and_data(self):
        call = self.manager.cancel(ALICE, VAULT_ADDR, b"\x01")
        assert call.data == Web3.keccak(text="cancel(address,address,bytes)")[
            :4
        ] + encode(["address", "address", "bytes"], [ALICE, VAULT_ADDR, b"\x01"])

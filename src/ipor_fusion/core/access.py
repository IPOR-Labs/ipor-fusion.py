from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import TypedDict

from eth_abi import decode, encode
from eth_abi.exceptions import InsufficientDataBytes
from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.types import LogReceipt, Timestamp

from ipor_fusion.config.roles import Roles
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.errors import (
    ContractNotFoundError,
    EmptyCallResultError,
    NotPlasmaVaultError,
    OwnerNotFoundError,
)
from ipor_fusion.types import (
    AccessManagerAddress,
    ChainId,
    Period,
    PlasmaVaultAddress,
    RoleId,
    ScheduleId,
)


class RoleAccountJson(TypedDict):
    """`RoleAccount.to_dict`: one role membership as JSON."""

    account: ChecksumAddress
    role_id: RoleId
    role_name: str
    is_member: bool
    execution_delay: Period


@dataclass(slots=True)
class RoleStatus:
    """Result of a role membership check for an account."""

    is_member: bool
    execution_delay: Period


@dataclass(frozen=True, slots=True)
class CallPermission:
    """`AccessManager.canCall`: whether ``caller`` may call ``selector`` on ``target``.

    ``immediate`` is a call allowed now; a member with an execution delay gets
    ``immediate=False`` and that ``delay`` (it must schedule the call first). A closed
    target or a non-member answers ``(False, 0)``. This is the role check only: the
    IPOR manager's redemption lock and the target's own logic can still revert.
    """

    immediate: bool
    delay: Period

    @property
    def allowed(self) -> bool:
        """Allowed now or after scheduling."""
        return self.immediate or self.delay > 0


def _call_permission_decoder(values: tuple[bool, int]) -> CallPermission:
    immediate, delay = values
    return CallPermission(immediate=immediate, delay=Period(delay))


@dataclass(slots=True)
class RoleAccount:
    """Account-role membership record returned by AccessManager queries."""

    account: ChecksumAddress
    role_id: RoleId
    is_member: bool
    execution_delay: Period

    @property
    def role_name(self) -> str:
        return Roles.get_name(self.role_id)

    def to_dict(self) -> RoleAccountJson:
        """Canonical JSON-ready row shape, shared by the CLI surfaces."""
        return {
            "account": self.account,
            "role_id": self.role_id,
            "role_name": self.role_name,
            "is_member": self.is_member,
            "execution_delay": self.execution_delay,
        }


def role_account_sort_key(role_account: RoleAccount) -> tuple[str, int]:
    """Canonical presentation order: account (case-insensitive), then role id."""
    return (role_account.account.lower(), role_account.role_id)


class RoleAccountsJson(TypedDict):
    """The ``fusion vault role-accounts --json`` document and the
    ``vault_role_accounts`` MCP tool output (`build_role_accounts_json`)."""

    vault: PlasmaVaultAddress
    access_manager: AccessManagerAddress
    chain_id: ChainId
    role_filter: str | None
    accounts: list[RoleAccountJson]


def build_role_accounts_json(
    accounts: Sequence[RoleAccount],
    *,
    vault: PlasmaVaultAddress,
    access_manager: AccessManagerAddress,
    chain_id: ChainId,
    role: RoleId | Roles | None = None,
) -> RoleAccountsJson:
    """Role holders as the one document both tools publish, in
    `role_account_sort_key` order; ``role`` is the filter applied, if any."""
    return {
        "vault": vault,
        "access_manager": access_manager,
        "chain_id": chain_id,
        "role_filter": Roles.get_name(role) if role is not None else None,
        "accounts": [
            ra.to_dict() for ra in sorted(accounts, key=role_account_sort_key)
        ],
    }


def _role_status_decoder(values: tuple[bool, int]) -> RoleStatus:
    is_member, execution_delay = values
    return RoleStatus(is_member=is_member, execution_delay=Period(execution_delay))


class AccessManager(ContractWrapper):
    """Manages role-based access control for PlasmaVault operations."""

    def __init__(self, address: AccessManagerAddress):
        super().__init__(address)

    @property
    def address(self) -> AccessManagerAddress:
        return AccessManagerAddress(self._address)

    def grant_role(
        self, role_id: RoleId | Roles, account: ChecksumAddress, execution_delay: Period
    ) -> Call[None]:
        return self._write(
            "grantRole(uint64,address,uint32)", role_id, account, execution_delay
        )

    def revoke_role(
        self, role_id: RoleId | Roles, account: ChecksumAddress
    ) -> Call[None]:
        return self._write("revokeRole(uint64,address)", role_id, account)

    def get_target_function_role(
        self, target: ChecksumAddress, selector: bytes
    ) -> Call[RoleId]:
        """Role id required to call the 4-byte `selector` on `target`."""
        return self._view(
            "getTargetFunctionRole(address,bytes4)",
            target,
            selector,
            output_types=["uint64"],
            decoder=RoleId,
        )

    def has_role(
        self, role_id: RoleId | Roles, account: ChecksumAddress
    ) -> Call[RoleStatus]:
        return self._view(
            "hasRole(uint64,address)",
            role_id,
            account,
            output_types=["bool", "uint32"],
            decoder=_role_status_decoder,
        )

    def get_schedule(self, operation: ScheduleId) -> Call[Timestamp]:
        """When the scheduled ``operation`` becomes executable; 0 when it is not
        scheduled, was executed or cancelled, or expired (a week after that time)."""
        return self._view(
            "getSchedule(bytes32)",
            operation,
            output_types=["uint48"],
            decoder=Timestamp,
        )

    @staticmethod
    def hash_operation(
        caller: ChecksumAddress, target: ChecksumAddress, data: bytes
    ) -> ScheduleId:
        """The id `schedule` and `get_schedule` use for ``caller`` calling
        ``target`` with ``data``, computed offline as the manager does."""
        return ScheduleId(
            Web3.keccak(encode(["address", "address", "bytes"], [caller, target, data]))
        )

    def can_call(
        self, caller: ChecksumAddress, target: ChecksumAddress, selector: bytes
    ) -> Call[CallPermission]:
        """`canCall(caller, target, selector)`: the closed-target check, the
        function's role and the caller's membership and delay in one read."""
        return self._view(
            "canCall(address,address,bytes4)",
            caller,
            target,
            selector,
            output_types=["bool", "uint32"],
            decoder=_call_permission_decoder,
        )

    def is_target_closed(self, target: ChecksumAddress) -> Call[bool]:
        """Whether the admin closed ``target``: every restricted call to it fails."""
        return self._view(
            "isTargetClosed(address)", target, output_types=["bool"], decoder=bool
        )

    def get_account_lock_time(self, account: ChecksumAddress) -> Call[Timestamp]:
        """Unix time until which ``account``, as the caller, cannot withdraw, redeem
        or transfer shares (IPOR redemption delay). A deposit, mint or
        ``depositWithPermit`` sets it for its caller, not for the share receiver;
        a time ``<= block.timestamp`` is unlocked."""
        return self._view(
            "getAccountLockTime(address)",
            account,
            output_types=["uint256"],
            decoder=Timestamp,
        )

    # ── Composite reads on ``ctx``: event replay + batched hasRole reads ───

    def read_owner(self, ctx: Web3Context) -> ChecksumAddress:
        """The first of :meth:`read_owners`; raises `OwnerNotFoundError` when no
        account holds ``OWNER_ROLE``."""
        owners = self.read_owners(ctx)
        if not owners:
            raise OwnerNotFoundError(
                f"access manager {self._address} has no account with OWNER_ROLE"
            )
        return owners[0]

    def read_owners(self, ctx: Web3Context) -> list[ChecksumAddress]:
        return [
            role_account.account
            for role_account in self.read_accounts_with_role(ctx, Roles.OWNER_ROLE)
        ]

    def read_atomists(self, ctx: Web3Context) -> list[ChecksumAddress]:
        return [
            role_account.account
            for role_account in self.read_accounts_with_role(ctx, Roles.ATOMIST_ROLE)
        ]

    def read_accounts_with_role(
        self, ctx: Web3Context, role_id: RoleId | Roles
    ) -> list[RoleAccount]:
        return self._resolve_role_accounts(
            ctx,
            self._get_grant_role_events(ctx),
            predicate=lambda rid, _: rid == role_id,
        )

    def read_all_role_accounts(self, ctx: Web3Context) -> list[RoleAccount]:
        return self._resolve_role_accounts(
            ctx,
            self._get_grant_role_events(ctx),
            predicate=lambda _rid, _acc: True,
        )

    def _resolve_role_accounts(
        self,
        ctx: Web3Context,
        events: list[LogReceipt],
        predicate: "Callable[[RoleId, ChecksumAddress], bool]",
    ) -> list[RoleAccount]:
        # A re-granted (role, account) emits multiple RoleGranted events; the
        # dict keeps each candidate once, in first-grant order.
        candidates: dict[tuple[RoleId, ChecksumAddress], None] = {}
        for event in events:
            (raw_role,) = decode(["uint64"], event["topics"][1])
            (raw_account,) = decode(["address"], event["topics"][2])
            role_id = RoleId(raw_role)
            account = Web3.to_checksum_address(raw_account)
            if predicate(role_id, account):
                candidates[(role_id, account)] = None

        statuses = Multicall3(ctx).aggregate(
            [self.has_role(role_id, account) for role_id, account in candidates]
        )
        return [
            RoleAccount(
                account=account,
                role_id=role_id,
                is_member=status.is_member,
                execution_delay=status.execution_delay,
            )
            for (role_id, account), status in zip(candidates, statuses, strict=True)
            if status.is_member
        ]

    def _get_grant_role_events(self, ctx: Web3Context) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="RoleGranted(uint64,address,uint32,uint48,bool)")
        ).to_0x_hex()
        return list(
            ctx.get_logs(contract_address=self._address, topics=[event_signature_hash])
        )


def resolve_access_manager(ctx: Web3Context, vault: ChecksumAddress) -> AccessManager:
    """Resolve a vault address to its AccessManager, with typed guards. An
    eager resolver: it reads on ``ctx`` and returns a context-free wrapper.

    Raises ContractNotFoundError when no code is deployed at the address (as
    of ctx.default_block), and NotPlasmaVaultError when the contract does not
    expose getAccessManagerAddress(): an empty eth_call return
    (EmptyCallResultError), return data too short to decode
    (InsufficientDataBytes) or a revert (ContractLogicError).
    Provider/transport errors propagate unchanged.
    """
    checksum = Web3.to_checksum_address(vault)
    # Same block as the eth_call below, or a pre-deployment pin would pass
    # this guard and get misdiagnosed as "not a vault".
    code = ctx.web3.eth.get_code(checksum, block_identifier=ctx.default_block)
    if code in {b"", b"\x00"}:
        block_note = (
            f" at block {ctx.default_block}" if ctx.default_block != "latest" else ""
        )
        raise ContractNotFoundError(
            f"No contract found at {checksum} on chain {ctx.chain_id}{block_note}."
        )
    try:
        manager_address = (
            PlasmaVault(PlasmaVaultAddress(checksum))
            .get_access_manager_address()
            .call(ctx)
        )
    except (EmptyCallResultError, InsufficientDataBytes, ContractLogicError) as exc:
        raise NotPlasmaVaultError(
            f"Address {checksum} on chain {ctx.chain_id} does not appear "
            f"to be a Plasma Vault."
        ) from exc
    return AccessManager(manager_address)

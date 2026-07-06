from collections.abc import Callable
from dataclasses import dataclass

from eth_abi import decode
from eth_abi.exceptions import InsufficientDataBytes
from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.types import LogReceipt

from ipor_fusion.config.roles import Roles
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.errors import ContractNotFoundError, NotAPlasmaVaultError
from ipor_fusion.types import Period, RoleId


@dataclass(slots=True)
class RoleStatus:
    """Result of a role membership check for an account."""

    is_member: bool
    execution_delay: Period


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

    def to_dict(self) -> dict[str, str | int | bool]:
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


def _role_status_decoder(values: tuple) -> RoleStatus:
    is_member, execution_delay = values
    return RoleStatus(is_member=is_member, execution_delay=execution_delay)


class AccessManager(ContractWrapper):
    """Manages role-based access control for PlasmaVault operations."""

    def grant_role(
        self, role_id: int, account: ChecksumAddress, execution_delay: Period
    ) -> Call[None]:
        return self._write(
            "grantRole(uint64,address,uint32)", role_id, account, execution_delay
        )

    def has_role(self, role_id: int, account: ChecksumAddress) -> Call[RoleStatus]:
        return self._view(
            "hasRole(uint64,address)",
            role_id,
            account,
            output_types=["bool", "uint32"],
            decoder=_role_status_decoder,
        )

    # ── Compound methods: event replay + per-account hasRole reads ─────────
    # These don't fit the single-eth_call `Call` shape, so they stay as
    # immediate-execute methods.

    def owner(self) -> ChecksumAddress:
        return self.owners()[0]

    def owners(self) -> list[ChecksumAddress]:
        return [
            role_account.account
            for role_account in self.get_accounts_with_role(Roles.OWNER_ROLE)
        ]

    def atomists(self) -> list[ChecksumAddress]:
        return [
            role_account.account
            for role_account in self.get_accounts_with_role(Roles.ATOMIST_ROLE)
        ]

    def get_accounts_with_role(self, role_id: int) -> list[RoleAccount]:
        return self._resolve_role_accounts(
            self._get_grant_role_events(),
            predicate=lambda rid, _: rid == role_id,
        )

    def get_all_role_accounts(self) -> list[RoleAccount]:
        return self._resolve_role_accounts(
            self._get_grant_role_events(),
            predicate=lambda _rid, _acc: True,
        )

    def _resolve_role_accounts(
        self,
        events: list[LogReceipt],
        predicate: "Callable[[int, str], bool]",
    ) -> list[RoleAccount]:
        # N+1 RPC: each candidate requires a has_role() call; multicall would fix
        # this but is out of scope.
        role_accounts: list[RoleAccount] = []
        seen: set[tuple[int, str]] = set()
        for event in events:
            (role_id,) = decode(["uint64"], event["topics"][1])
            (account,) = decode(["address"], event["topics"][2])
            if not predicate(role_id, account):
                continue
            # A re-granted (role, account) emits multiple RoleGranted events.
            if (role_id, account) in seen:
                continue
            seen.add((role_id, account))
            role_status = self.has_role(role_id, account).call()
            if role_status.is_member:
                role_accounts.append(
                    RoleAccount(
                        account=Web3.to_checksum_address(account),
                        role_id=role_id,
                        is_member=role_status.is_member,
                        execution_delay=role_status.execution_delay,
                    )
                )
        return role_accounts

    def _get_grant_role_events(self) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="RoleGranted(uint64,address,uint32,uint48,bool)")
        ).to_0x_hex()
        return list(
            self._ctx.get_logs(
                contract_address=self._address, topics=[event_signature_hash]
            )
        )


def resolve_access_manager(
    ctx: Web3Context, vault_address: ChecksumAddress
) -> AccessManager:
    """Resolve a vault address to its AccessManager, with typed guards.

    Raises ContractNotFoundError when no code is deployed at the address (as
    of ctx.default_block), and NotAPlasmaVaultError when the contract does not
    expose getAccessManagerAddress() — either an empty eth_call return
    (InsufficientDataBytes on decode) or a revert (ContractLogicError).
    Provider/transport errors propagate unchanged.
    """
    checksum = Web3.to_checksum_address(vault_address)
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
        manager_address = PlasmaVault(ctx, checksum).get_access_manager_address().call()
    except (InsufficientDataBytes, ContractLogicError) as exc:
        raise NotAPlasmaVaultError(
            f"Address {checksum} on chain {ctx.chain_id} does not appear "
            f"to be a Plasma Vault."
        ) from exc
    return AccessManager(ctx, manager_address)

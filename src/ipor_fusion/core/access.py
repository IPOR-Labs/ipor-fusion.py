from collections.abc import Callable
from dataclasses import dataclass

from eth_abi import decode, encode as abi_encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.types import LogReceipt

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.config.roles import Roles
from ipor_fusion.types import RoleId, Period


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


def _role_status_decoder(values: tuple) -> RoleStatus:
    is_member, execution_delay = values
    return RoleStatus(is_member=is_member, execution_delay=execution_delay)


class AccessManager(ContractWrapper):
    """Manages role-based access control for PlasmaVault operations."""

    @staticmethod
    def encode_grant_role_calldata(
        role_id: int, account: ChecksumAddress, execution_delay: Period = 0
    ) -> bytes:
        """Selector + ABI-encoded args for `grantRole(uint64,address,uint32)`.

        Pure helper — no ctx required. Use when handing calldata to an external
        signer (HTTP signing service, multisig flow) instead of `Call.send()`.
        """
        selector = function_signature_to_4byte_selector(
            "grantRole(uint64,address,uint32)"
        )
        return selector + abi_encode(
            ["uint64", "address", "uint32"],
            [int(role_id), account, int(execution_delay)],
        )

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
        for event in events:
            (role_id,) = decode(["uint64"], event["topics"][1])
            (account,) = decode(["address"], event["topics"][2])
            if not predicate(role_id, account):
                continue
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

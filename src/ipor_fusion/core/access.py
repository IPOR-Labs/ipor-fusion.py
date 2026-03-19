from dataclasses import dataclass
from eth_abi import decode
from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.types import TxReceipt, LogReceipt

from ipor_fusion.core.contract import ContractWrapper
from ipor_fusion.config.roles import Roles


@dataclass
class RoleAccount:
    """Account-role membership record returned by AccessManager queries."""

    account: ChecksumAddress
    role_id: int
    is_member: bool
    execution_delay: int


class AccessManager(ContractWrapper):
    """Manages role-based access control for PlasmaVault operations."""

    def grant_role(
        self, role_id: int, account: ChecksumAddress, execution_delay: int
    ) -> TxReceipt:
        return self._send(
            "grantRole(uint64,address,uint32)", role_id, account, execution_delay
        )

    def has_role(self, role_id: int, account: ChecksumAddress) -> tuple[bool, int]:
        result = self._call("hasRole(uint64,address)", role_id, account)
        is_member, execution_delay = decode(["bool", "uint32"], result)
        return is_member, execution_delay

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
        events = self._get_grant_role_events()
        role_accounts = []
        for event in events:
            (_role_id,) = decode(["uint64"], event["topics"][1])
            (_account,) = decode(["address"], event["topics"][2])
            if _role_id == role_id:
                is_member, execution_delay = self.has_role(_role_id, _account)
                if is_member:
                    role_account = RoleAccount(
                        account=Web3.to_checksum_address(_account),
                        role_id=_role_id,
                        is_member=is_member,
                        execution_delay=execution_delay,
                    )
                    role_accounts.append(role_account)
        return role_accounts

    def get_all_role_accounts(self) -> list[RoleAccount]:
        events = self._get_grant_role_events()
        role_accounts = []
        for event in events:
            (role_id,) = decode(["uint64"], event["topics"][1])
            (account,) = decode(["address"], event["topics"][2])
            is_member, execution_delay = self.has_role(role_id, account)
            if is_member:
                role_account = RoleAccount(
                    account=Web3.to_checksum_address(account),
                    role_id=role_id,
                    is_member=is_member,
                    execution_delay=execution_delay,
                )
                role_accounts.append(role_account)
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

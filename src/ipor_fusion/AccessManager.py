from dataclasses import dataclass
from typing import List

from eth_abi import encode, decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.types import TxReceipt, LogReceipt

from ipor_fusion.Roles import Roles
from ipor_fusion.TransactionExecutor import TransactionExecutor


@dataclass
class RoleAccount:
    account: ChecksumAddress
    role_id: int
    is_member: bool
    execution_delay: int


class AccessManager:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        access_manager_address: ChecksumAddress,
    ):
        self._transaction_executor = transaction_executor
        self._access_manager_address = access_manager_address

    def address(self) -> ChecksumAddress:
        return self._access_manager_address

    def grant_role(
        self, role_id: int, account: ChecksumAddress, execution_delay
    ) -> TxReceipt:
        selector = function_signature_to_4byte_selector(
            "grantRole(uint64,address,uint32)"
        )
        function = selector + encode(
            ["uint64", "address", "uint32"], [role_id, account, execution_delay]
        )
        return self._transaction_executor.execute(
            self._access_manager_address, function
        )

    def has_role(self, role_id: int, account: ChecksumAddress) -> (bool, int):
        selector = function_signature_to_4byte_selector("hasRole(uint64,address)")
        function = selector + encode(["uint64", "address"], [role_id, account])
        read = self._transaction_executor.read(self._access_manager_address, function)
        is_member, execution_delay = decode(["bool", "uint32"], read)
        return is_member, execution_delay

    def owner(self) -> ChecksumAddress:
        return self.owners()[0]

    def owners(self) -> List[ChecksumAddress]:
        return [
            role_account.account
            for role_account in self.get_accounts_with_role(Roles.OWNER_ROLE)
        ]

    def atomists(self) -> List[ChecksumAddress]:
        return [
            role_account.account
            for role_account in self.get_accounts_with_role(Roles.ATOMIST_ROLE)
        ]

    def get_accounts_with_role(self, role_id: int) -> List[RoleAccount]:
        events = self.get_grant_role_events()
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

    def get_all_role_accounts(self) -> List[RoleAccount]:
        events = self.get_grant_role_events()
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

    def get_grant_role_events(self) -> List[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="RoleGranted(uint64,address,uint32,uint48,bool)")
        ).to_0x_hex()
        logs = self._transaction_executor.get_logs(
            contract_address=self._access_manager_address, topics=[event_signature_hash]
        )
        return logs

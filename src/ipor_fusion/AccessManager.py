from typing import List, Dict

from eth_abi import encode, decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.types import TxReceipt, LogReceipt

from ipor_fusion.Roles import Roles
from ipor_fusion.TransactionExecutor import TransactionExecutor


class AccessManager:

    def __init__(
        self, transaction_executor: TransactionExecutor, access_manager_address: str
    ):
        self._transaction_executor = transaction_executor
        self._access_manager_address = access_manager_address

    def address(self) -> str:
        return self._access_manager_address

    def grant_role(self, role_id: int, account: str, execution_delay) -> TxReceipt:
        selector = function_signature_to_4byte_selector(
            "grantRole(uint64,address,uint32)"
        )
        function = selector + encode(
            ["uint64", "address", "uint32"], [role_id, account, execution_delay]
        )
        return self._transaction_executor.execute(
            self._access_manager_address, function
        )

    def has_role(self, role_id: int, account: str) -> TxReceipt:
        selector = function_signature_to_4byte_selector("hasRole(uint64,address)")
        function = selector + encode(["uint64", "address"], [role_id, account])
        return self._transaction_executor.read(self._access_manager_address, function)

    def owner(self) -> ChecksumAddress:
        return self.owners()[0]

    def owners(self) -> List[ChecksumAddress]:
        return self.get_accounts_with_role(Roles.OWNER_ROLE)

    def get_accounts_with_role(self, role_id: int) -> List[ChecksumAddress]:
        return self.get_accounts_with_roles([role_id]).get(role_id)

    def get_accounts_with_roles(
        self, role_ids: List[int]
    ) -> Dict[int, List[ChecksumAddress]]:
        events = self.get_grant_role_events()
        result = {}
        for role_id in role_ids:
            accounts = []
            for event in events:
                (_role_id,) = decode(["uint64"], event["topics"][1])
                (_account,) = decode(["address"], event["topics"][2])
                if _role_id == role_id and self.has_role(_role_id, _account):
                    accounts.append(Web3.to_checksum_address(_account))
            result.update({role_id: accounts})
        return result

    def get_grant_role_events(self) -> List[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="RoleGranted(uint64,address,uint32,uint48,bool)")
        ).to_0x_hex()
        logs = self._transaction_executor.get_logs(
            contract_address=self._access_manager_address, topics=[event_signature_hash]
        )
        return logs

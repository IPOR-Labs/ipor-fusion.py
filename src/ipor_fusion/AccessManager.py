from typing import List, Union

from eth_abi import encode, decode
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

    def owner(self) -> Union[str, None]:
        events = self.get_grant_role_events()
        sorted_events = sorted(
            events, key=lambda event: event["blockNumber"], reverse=True
        )
        for event in sorted_events:
            (role_id,) = decode(["uint64"], event["topics"][1])
            (account,) = decode(["address"], event["topics"][2])
            if role_id == Roles.OWNER_ROLE:
                return Web3.to_checksum_address(account)
        return None

    def get_grant_role_events(self) -> List[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="RoleGranted(uint64,address,uint32,uint48,bool)")
        ).to_0x_hex()
        logs = self._transaction_executor.get_logs(
            contract_address=self._access_manager_address, topics=[event_signature_hash]
        )
        return logs

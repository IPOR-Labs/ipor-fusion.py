from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3.types import TxReceipt

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

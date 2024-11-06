from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3.types import TxReceipt

from ipor_fusion.TransactionExecutor import TransactionExecutor


class WithdrawManager:

    def __init__(
        self, transaction_executor: TransactionExecutor, withdraw_manager_address: str
    ):
        self._transaction_executor = transaction_executor
        self._withdraw_manager_address = withdraw_manager_address

    def address(self) -> str:
        return self._withdraw_manager_address

    def request(self, to_withdraw: int) -> TxReceipt:
        function = self.__request(to_withdraw)
        return self._transaction_executor.execute(
            self._withdraw_manager_address, function
        )

    def update_withdraw_window(self, window: int):
        selector = function_signature_to_4byte_selector("updateWithdrawWindow(uint256)")
        function = selector + encode(["uint256"], [window])
        return self._transaction_executor.execute(
            self._withdraw_manager_address, function
        )

    @staticmethod
    def __request(to_withdraw: int) -> bytes:
        selector = function_signature_to_4byte_selector("request(uint256)")
        return selector + encode(["uint256"], [to_withdraw])

    def release_funds(self):
        selector = function_signature_to_4byte_selector("releaseFunds()")
        return self._transaction_executor.execute(
            self._withdraw_manager_address, selector
        )

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3.types import TxReceipt

from ipor_fusion.TransactionExecutor import TransactionExecutor


class ERC20:

    def __init__(self, transaction_executor: TransactionExecutor, asset: str):
        self._transaction_executor = transaction_executor
        self._asset = asset

    def transfer(self, to: str, amount: int) -> TxReceipt:
        function = self.__transfer(to, amount)
        return self._transaction_executor.execute(self._asset, function)

    def approve(self, spender: str, value: int) -> TxReceipt:
        function = self.__approve(spender, value)
        return self._transaction_executor.execute(self._asset, function)

    def balance_of(self, holder) -> int:
        return self._transaction_executor.balance_of(holder, self._asset)

    @staticmethod
    def __transfer(to: str, amount: int) -> bytes:
        args = ["address", "uint256"]
        join = ",".join(args)
        function_signature = f"transfer({join})"
        selector = function_signature_to_4byte_selector(function_signature)
        return selector + encode(args, [to, amount])

    @staticmethod
    def __approve(spender: str, value: int) -> bytes:
        args = ["address", "uint256"]
        join = ",".join(args)
        function_signature = f"approve({join})"
        selector = function_signature_to_4byte_selector(function_signature)
        return selector + encode(args, [spender, value])

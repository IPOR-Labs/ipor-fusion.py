from eth_abi import encode, decode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.TransactionExecutor import TransactionExecutor


class ERC20:

    def __init__(self, transaction_executor: TransactionExecutor, asset_address: str):
        self._transaction_executor = transaction_executor
        self._asset_address = asset_address

    def address(self) -> str:
        return self._asset_address

    def transfer(self, to: str, amount: int):
        sig = function_signature_to_4byte_selector("transfer(address,uint256)")
        encoded_args = encode(["address", "uint256"], [to, amount])
        return self._transaction_executor.execute(
            self._asset_address, sig + encoded_args
        )

    def approve(self, spender: str, amount: int):
        sig = function_signature_to_4byte_selector("approve(address,uint256)")
        encoded_args = encode(["address", "uint256"], [spender, amount])
        return self._transaction_executor.execute(
            self._asset_address, sig + encoded_args
        )

    def balance_of(self, account: str) -> int:
        sig = function_signature_to_4byte_selector("balanceOf(address)")
        encoded_args = encode(["address"], [account])
        read = self._transaction_executor.read(self._asset_address, sig + encoded_args)
        (result,) = decode(["uint256"], read)
        return result

    def decimals(self) -> int:
        decimals = function_signature_to_4byte_selector("decimals()")
        read = self._transaction_executor.read(self._asset_address, decimals)
        (result,) = decode(["uint256"], read)
        return result

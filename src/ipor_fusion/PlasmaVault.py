from typing import List

from eth_abi import encode, decode
from eth_utils import function_signature_to_4byte_selector
from web3.types import TxReceipt

from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.fuse.FuseAction import FuseAction


class PlasmaVault:

    def __init__(
        self, transaction_executor: TransactionExecutor, plasma_vault_address: str
    ):
        self._transaction_executor = transaction_executor
        self._plasma_vault = plasma_vault_address

    def execute(self, actions: List[FuseAction]) -> TxReceipt:
        function = self.__execute(actions)
        return self._transaction_executor.execute(self._plasma_vault, function)

    def deposit(self, assets: int, receiver: str) -> TxReceipt:
        function = self.__deposit(assets, receiver)
        return self._transaction_executor.execute(self._plasma_vault, function)

    def mint(self, shares: int, receiver: str) -> TxReceipt:
        sig = function_signature_to_4byte_selector("mint(uint256,address)")
        encoded_args = encode(["uint256", "address"], [shares, receiver])
        return self._transaction_executor.execute(
            self._plasma_vault, sig + encoded_args
        )

    def redeem(self, shares: int, receiver: str, owner: str) -> TxReceipt:
        sig = function_signature_to_4byte_selector("redeem(uint256,address,address)")
        encoded_args = encode(
            ["uint256", "address", "address"], [shares, receiver, owner]
        )
        return self._transaction_executor.execute(
            self._plasma_vault, sig + encoded_args
        )

    def balance_of(self, account: str) -> int:
        sig = function_signature_to_4byte_selector("balanceOf(address)")
        encoded_args = encode(["address"], [account])
        read = self._transaction_executor.read(self._plasma_vault, sig + encoded_args)
        (result,) = decode(["uint256"], read)
        return result

    def max_withdraw(self, account: str) -> int:
        sig = function_signature_to_4byte_selector("maxWithdraw(address)")
        encoded_args = encode(["address"], [account])
        read = self._transaction_executor.read(self._plasma_vault, sig + encoded_args)
        (result,) = decode(["uint256"], read)
        return result

    def total_assets_in_market(self, market: int) -> int:
        sig = function_signature_to_4byte_selector("totalAssetsInMarket(uint256)")
        encoded_args = encode(["uint256"], [market])
        read = self._transaction_executor.read(self._plasma_vault, sig + encoded_args)
        (result,) = decode(["uint256"], read)
        return result

    def decimals(self) -> int:
        sig = function_signature_to_4byte_selector("decimals()")
        read = self._transaction_executor.read(self._plasma_vault, sig)
        (result,) = decode(["uint256"], read)
        return result

    def total_assets(self) -> int:
        sig = function_signature_to_4byte_selector("totalAssets()")
        read = self._transaction_executor.read(self._plasma_vault, sig)
        (result,) = decode(["uint256"], read)
        return result

    def asset(self) -> int:
        sig = function_signature_to_4byte_selector("asset()")
        read = self._transaction_executor.read(self._plasma_vault, sig)
        (result,) = decode(["address"], read)
        return result

    def convert_to_assets(self, amount: int) -> int:
        sig = function_signature_to_4byte_selector("convertToAssets(uint256)")
        encoded_args = encode(["uint256"], [amount])
        read = self._transaction_executor.read(self._plasma_vault, sig + encoded_args)
        (result,) = decode(["uint256"], read)
        return result

    @staticmethod
    def __execute(actions: List[FuseAction]) -> bytes:
        bytes_data = []
        for action in actions:
            bytes_data.append([action.fuse, action.data])
        bytes_ = "(address,bytes)[]"
        encoded_arguments = encode([bytes_], [bytes_data])
        return (
            function_signature_to_4byte_selector("execute((address,bytes)[])")
            + encoded_arguments
        )

    @staticmethod
    def __deposit(assets: int, receiver: str) -> bytes:
        args = ["uint256", "address"]
        join = ",".join(args)
        function_signature = f"deposit({join})"
        selector = function_signature_to_4byte_selector(function_signature)
        return selector + encode(args, [assets, receiver])

    def withdraw(self, assets: int, receiver: str, owner: str) -> TxReceipt:
        sig = function_signature_to_4byte_selector("withdraw(uint256,address,address)")
        encoded_args = encode(
            ["uint256", "address", "address"], [assets, receiver, owner]
        )
        return self._transaction_executor.execute(
            self._plasma_vault, sig + encoded_args
        )

    def transfer(self, to: str, value):
        sig = function_signature_to_4byte_selector("transfer(address,uint256)")
        encoded_args = encode(["address", "uint256"], [to, value])
        return self._transaction_executor.execute(
            self._plasma_vault, sig + encoded_args
        )

    def approve(self, account: str, amount: int):
        sig = function_signature_to_4byte_selector("approve(address,uint256)")
        encoded_args = encode(["address", "uint256"], [account, amount])
        return self._transaction_executor.execute(
            self._plasma_vault, sig + encoded_args
        )

    def transfer_from(self, _from: str, to: str, amount: int):
        sig = function_signature_to_4byte_selector(
            "transferFrom(address,address,uint256)"
        )
        encoded_args = encode(["address", "address", "uint256"], [_from, to, amount])
        return self._transaction_executor.execute(
            self._plasma_vault, sig + encoded_args
        )

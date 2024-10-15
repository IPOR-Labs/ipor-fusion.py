from typing import List

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3.types import TxReceipt

from ipor_fusion.ERC20 import ERC20
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

    def balance_of(self, asset: str) -> int:
        erc20 = ERC20(self._transaction_executor, asset)
        return erc20.balance_of(self._plasma_vault)

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

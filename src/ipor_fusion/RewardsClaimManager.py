from typing import List

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3.types import TxReceipt

from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.fuse.FuseAction import FuseAction


class RewardsClaimManager:

    def __init__(
        self, transaction_executor: TransactionExecutor, rewards_claim_manager: str
    ):
        self._transaction_executor = transaction_executor
        self._rewards_claim_manager = rewards_claim_manager

    def transfer(self, asset: str, to: str, amount: int) -> TxReceipt:
        function = self.__transfer(asset, to, amount)
        return self._transaction_executor.execute(self._rewards_claim_manager, function)

    def balance_of(self, asset: str) -> int:
        return self._transaction_executor.balance_of(self._rewards_claim_manager, asset)

    def claim_rewards(self, claims: List[FuseAction]) -> TxReceipt:
        function = self.__claim_rewards(claims)
        return self._transaction_executor.execute(self._rewards_claim_manager, function)

    @staticmethod
    def __claim_rewards(claims: List[FuseAction]) -> bytes:
        bytes_data = []
        for action in claims:
            bytes_data.append([action.fuse, action.data])
        bytes_ = "(address,bytes)[]"
        encoded_arguments = encode([bytes_], [bytes_data])
        return (
            function_signature_to_4byte_selector("claimRewards((address,bytes)[])")
            + encoded_arguments
        )

    @staticmethod
    def __transfer(asset: str, to: str, amount: int) -> bytes:
        args = ["address", "address", "uint256"]
        join = ",".join(args)
        function_signature = f"transfer({join})"
        selector = function_signature_to_4byte_selector(function_signature)
        return selector + encode(args, [asset, to, amount])

    def update_balance(self):
        function = self.__update_balance()
        return self._transaction_executor.execute(self._rewards_claim_manager, function)

    @staticmethod
    def __update_balance():
        function_signature = "updateBalance()"
        selector = function_signature_to_4byte_selector(function_signature)
        return selector

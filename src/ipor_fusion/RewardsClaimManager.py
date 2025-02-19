from typing import List

from eth_abi import encode, decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.fuse.FuseAction import FuseAction


class RewardsClaimManager:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        rewards_claim_manager_address: ChecksumAddress,
    ):
        self._transaction_executor = transaction_executor
        self._rewards_claim_manager_address = rewards_claim_manager_address

    def address(self) -> ChecksumAddress:
        return self._rewards_claim_manager_address

    def transfer(self, asset: str, to: str, amount: int) -> TxReceipt:
        function = self.__transfer(asset, to, amount)
        return self._transaction_executor.execute(
            self._rewards_claim_manager_address, function
        )

    def balance_of(self) -> int:
        signature = function_signature_to_4byte_selector("balanceOf()")
        read = self._transaction_executor.read(
            self._rewards_claim_manager_address, signature
        )
        (result,) = decode(["uint256"], read)
        return result

    def get_vesting_data(self) -> (int, int, int, int):
        signature = function_signature_to_4byte_selector("getVestingData()")
        read = self._transaction_executor.read(
            self._rewards_claim_manager_address, signature
        )
        (
            (
                vesting_time,
                update_balance_timestamp,
                transferred_tokens,
                last_update_balance,
            ),
        ) = decode(["(uint32,uint32,uint128,uint128)"], read)
        return (
            vesting_time,
            update_balance_timestamp,
            transferred_tokens,
            last_update_balance,
        )

    def get_rewards_fuses(self) -> List[str]:
        signature = function_signature_to_4byte_selector("getRewardsFuses()")
        read = self._transaction_executor.read(
            self._rewards_claim_manager_address, signature
        )
        (result,) = decode(["address[]"], read)
        return [Web3.to_checksum_address(item) for item in list(result)]

    def is_reward_fuse_supported(self, fuse) -> bool:
        signature = function_signature_to_4byte_selector(
            "isRewardFuseSupported(address)"
        )
        function = signature + encode(["address"], [fuse])
        read = self._transaction_executor.read(
            self._rewards_claim_manager_address, function
        )
        (result,) = decode(["bool"], read)
        return result

    def claim_rewards(self, claims: List[FuseAction]) -> TxReceipt:
        function = self.__claim_rewards(claims)
        return self._transaction_executor.execute(
            self._rewards_claim_manager_address, function
        )

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
        return self._transaction_executor.execute(
            self._rewards_claim_manager_address, function
        )

    @staticmethod
    def __update_balance():
        function_signature = "updateBalance()"
        selector = function_signature_to_4byte_selector(function_signature)
        return selector

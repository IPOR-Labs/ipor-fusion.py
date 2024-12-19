from typing import List

from eth_abi import encode, decode
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractPanicError
from web3.types import TxReceipt, LogReceipt
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

    def release_funds(self, timestamp: int = None):
        if timestamp:
            selector = function_signature_to_4byte_selector("releaseFunds(uint256)")
            return self._transaction_executor.execute(
                self._withdraw_manager_address,
                selector + encode(["uint256"], [timestamp]),
            )

        selector = function_signature_to_4byte_selector("releaseFunds()")
        return self._transaction_executor.execute(
            self._withdraw_manager_address, selector
        )

    def get_withdraw_window(self) -> int:
        signature = function_signature_to_4byte_selector("getWithdrawWindow()")
        read = self._transaction_executor.read(
            self._withdraw_manager_address, signature
        )
        (result,) = decode(["uint256"], read)
        return result

    def get_last_release_funds_timestamp(self) -> int:
        signature = function_signature_to_4byte_selector(
            "getLastReleaseFundsTimestamp()"
        )
        read = self._transaction_executor.read(
            self._withdraw_manager_address, signature
        )
        (result,) = decode(["uint256"], read)
        return result

    def request_info(self, account: str) -> int:
        signature = function_signature_to_4byte_selector("requestInfo(address)")
        read = self._transaction_executor.read(
            self._withdraw_manager_address,
            signature + encode(["address"], [account]),
        )
        (
            amount,
            end_withdraw_window_timestamp,
            can_withdraw,
            withdraw_window_in_seconds,
        ) = decode(["uint256", "uint256", "bool", "uint256"], read)
        return (
            amount,
            end_withdraw_window_timestamp,
            can_withdraw,
            withdraw_window_in_seconds,
        )

    def get_pending_requests_info(self) -> (int, int):
        current_timestamp = self._transaction_executor.get_block()["timestamp"]
        events = self.get_withdraw_request_updated_events()

        accounts = []
        for event in events:
            (account, amount, end_withdraw_window) = decode(
                ["address", "uint256", "uint32"], event["data"]
            )
            if (
                end_withdraw_window > current_timestamp
                and amount != 0
                and not account in accounts
            ):
                accounts.append(account)

        requested_amount = 0
        for account in accounts:
            try:
                (
                    amount,
                    end_withdraw_window_timestamp,
                    _,
                    _,
                ) = self.request_info(account)

                if end_withdraw_window_timestamp > current_timestamp:
                    requested_amount += amount
            except ContractPanicError:
                pass

        return requested_amount, current_timestamp - 1

    def get_withdraw_request_updated_events(self) -> List[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="WithdrawRequestUpdated(address,uint256,uint32)")
        ).to_0x_hex()
        logs = self._transaction_executor.get_logs(
            contract_address=self._withdraw_manager_address,
            topics=[event_signature_hash],
        )
        return logs

from typing import List

from eth_abi import encode, decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractPanicError
from web3.types import TxReceipt, LogReceipt, Timestamp
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.types import Shares, Amount, Period


class WithdrawRequestInfo:
    def __init__(
        self,
        shares: Shares,
        end_withdraw_window_timestamp: Timestamp,
        can_withdraw: bool,
        withdraw_window_in_seconds: Period,
    ):
        self.shares = shares
        self.end_withdraw_window_timestamp = end_withdraw_window_timestamp
        self.can_withdraw = can_withdraw
        self.withdraw_window_in_seconds = withdraw_window_in_seconds


class WithdrawManager:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        withdraw_manager_address: ChecksumAddress,
    ):
        self._transaction_executor = transaction_executor
        self._withdraw_manager_address = withdraw_manager_address

    def address(self) -> ChecksumAddress:
        return self._withdraw_manager_address

    def request(self, to_withdraw: Amount) -> TxReceipt:
        selector = function_signature_to_4byte_selector("request(uint256)")
        function = selector + encode(["uint256"], [to_withdraw])
        return self._transaction_executor.execute(
            self._withdraw_manager_address, function
        )

    def request_shares(self, shares: Shares) -> TxReceipt:
        selector = function_signature_to_4byte_selector("requestShares(uint256)")
        function = selector + encode(["uint256"], [shares])
        return self._transaction_executor.execute(
            self._withdraw_manager_address, function
        )

    def update_withdraw_window(self, window: Period):
        selector = function_signature_to_4byte_selector("updateWithdrawWindow(uint256)")
        function = selector + encode(["uint256"], [window])
        return self._transaction_executor.execute(
            self._withdraw_manager_address, function
        )

    def update_plasma_vault_address(self, window: ChecksumAddress):
        selector = function_signature_to_4byte_selector(
            "updatePlasmaVaultAddress(address)"
        )
        function = selector + encode(["address"], [window])
        return self._transaction_executor.execute(
            self._withdraw_manager_address, function
        )

    def release_funds(self, timestamp: Timestamp = None, shares: Shares = None):
        if shares:
            if not timestamp:
                raise ValueError("No timestamp argument in release_founds method call")

            selector = function_signature_to_4byte_selector(
                "releaseFunds(uint256,uint256)"
            )
            return self._transaction_executor.execute(
                self._withdraw_manager_address,
                selector + encode(["uint256", "uint256"], [timestamp, shares]),
            )

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

    def get_withdraw_window(self) -> Period:
        signature = function_signature_to_4byte_selector("getWithdrawWindow()")
        read = self._transaction_executor.read(
            self._withdraw_manager_address, signature
        )
        (result,) = decode(["uint256"], read)
        return result

    def get_last_release_funds_timestamp(self) -> Timestamp:
        signature = function_signature_to_4byte_selector(
            "getLastReleaseFundsTimestamp()"
        )
        read = self._transaction_executor.read(
            self._withdraw_manager_address, signature
        )
        (result,) = decode(["uint256"], read)
        return result

    def get_shares_to_release(self) -> Shares:
        signature = function_signature_to_4byte_selector("getSharesToRelease()")
        read = self._transaction_executor.read(
            self._withdraw_manager_address, signature
        )
        (result,) = decode(["uint256"], read)
        return result

    def get_request_fee(self) -> int:
        signature = function_signature_to_4byte_selector("getRequestFee()")
        read = self._transaction_executor.read(
            self._withdraw_manager_address, signature
        )
        (result,) = decode(["uint256"], read)
        return result

    def request_info(self, account: ChecksumAddress) -> WithdrawRequestInfo:
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
        return WithdrawRequestInfo(
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
                request_info = self.request_info(account)

                if request_info.end_withdraw_window_timestamp > current_timestamp:
                    requested_amount += request_info.shares
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

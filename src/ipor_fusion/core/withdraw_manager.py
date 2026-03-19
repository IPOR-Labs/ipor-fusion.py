from __future__ import annotations

from dataclasses import dataclass

from eth_abi import encode, decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractPanicError
from web3.types import TxReceipt, LogReceipt, Timestamp

from ipor_fusion.core.context import Web3Context
from ipor_fusion.types import Shares, Amount, Period


@dataclass
class WithdrawRequestInfo:
    shares: Shares
    end_withdraw_window_timestamp: Timestamp
    can_withdraw: bool
    withdraw_window_in_seconds: Period


class WithdrawManager:

    def __init__(self, ctx: Web3Context, address: ChecksumAddress):
        self._ctx = ctx
        self._address = Web3.to_checksum_address(address)

    @property
    def address(self) -> ChecksumAddress:
        return self._address

    def request(self, to_withdraw: Amount) -> TxReceipt:
        sig = function_signature_to_4byte_selector("request(uint256)")
        data = sig + encode(["uint256"], [to_withdraw])
        return self._ctx.send(self._address, data)

    def request_shares(self, shares: Shares) -> TxReceipt:
        sig = function_signature_to_4byte_selector("requestShares(uint256)")
        data = sig + encode(["uint256"], [shares])
        return self._ctx.send(self._address, data)

    def update_withdraw_window(self, window: Period) -> TxReceipt:
        sig = function_signature_to_4byte_selector("updateWithdrawWindow(uint256)")
        data = sig + encode(["uint256"], [window])
        return self._ctx.send(self._address, data)

    def update_plasma_vault_address(self, vault: ChecksumAddress) -> TxReceipt:
        sig = function_signature_to_4byte_selector("updatePlasmaVaultAddress(address)")
        data = sig + encode(["address"], [vault])
        return self._ctx.send(self._address, data)

    def release_funds(
        self, timestamp: Timestamp | None = None, shares: Shares | None = None
    ) -> TxReceipt:
        if shares is not None:
            if timestamp is None:
                raise ValueError("timestamp is required when shares is provided")
            sig = function_signature_to_4byte_selector("releaseFunds(uint256,uint256)")
            data = sig + encode(["uint256", "uint256"], [timestamp, shares])
        elif timestamp is not None:
            sig = function_signature_to_4byte_selector("releaseFunds(uint256)")
            data = sig + encode(["uint256"], [timestamp])
        else:
            sig = function_signature_to_4byte_selector("releaseFunds()")
            data = sig

        return self._ctx.send(self._address, data)

    def get_withdraw_window(self) -> Period:
        sig = function_signature_to_4byte_selector("getWithdrawWindow()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["uint256"], result)
        return value

    def get_last_release_funds_timestamp(self) -> Timestamp:
        sig = function_signature_to_4byte_selector("getLastReleaseFundsTimestamp()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["uint256"], result)
        return value

    def get_shares_to_release(self) -> Shares:
        sig = function_signature_to_4byte_selector("getSharesToRelease()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["uint256"], result)
        return value

    def get_request_fee(self) -> int:
        sig = function_signature_to_4byte_selector("getRequestFee()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["uint256"], result)
        return value

    def request_info(self, account: ChecksumAddress) -> WithdrawRequestInfo:
        sig = function_signature_to_4byte_selector("requestInfo(address)")
        result = self._ctx.call(self._address, sig + encode(["address"], [account]))
        (
            amount,
            end_withdraw_window_timestamp,
            can_withdraw,
            withdraw_window_in_seconds,
        ) = decode(["uint256", "uint256", "bool", "uint256"], result)
        return WithdrawRequestInfo(
            shares=amount,
            end_withdraw_window_timestamp=end_withdraw_window_timestamp,
            can_withdraw=can_withdraw,
            withdraw_window_in_seconds=withdraw_window_in_seconds,
        )

    def get_pending_requests_info(self) -> tuple[int, int]:
        current_timestamp = self._ctx.get_block()["timestamp"]
        events = self._get_withdraw_request_updated_events()

        accounts = []
        for event in events:
            (account, amount, end_withdraw_window) = decode(
                ["address", "uint256", "uint32"], event["data"]
            )
            if (
                end_withdraw_window > current_timestamp
                and amount != 0
                and account not in accounts
            ):
                accounts.append(account)

        requested_amount = 0
        for account in accounts:
            try:
                req_info = self.request_info(account)
                if req_info.end_withdraw_window_timestamp > current_timestamp:
                    requested_amount += req_info.shares
            except ContractPanicError:
                pass

        return requested_amount, current_timestamp - 1

    def _get_withdraw_request_updated_events(self) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="WithdrawRequestUpdated(address,uint256,uint32)")
        ).to_0x_hex()
        return list(
            self._ctx.get_logs(
                contract_address=self._address, topics=[event_signature_hash]
            )
        )

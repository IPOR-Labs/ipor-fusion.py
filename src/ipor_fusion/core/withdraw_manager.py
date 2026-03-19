from __future__ import annotations

from dataclasses import dataclass

from eth_abi import decode
from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractPanicError
from web3.types import TxReceipt, LogReceipt, Timestamp

from ipor_fusion.core.contract import ContractWrapper
from ipor_fusion.types import Shares, Amount, Period


@dataclass
class WithdrawRequestInfo:
    """Snapshot of a pending withdrawal request for a single account."""

    shares: Shares
    end_withdraw_window_timestamp: Timestamp
    can_withdraw: bool
    withdraw_window_in_seconds: Period


class WithdrawManager(ContractWrapper):

    def request(self, to_withdraw: Amount) -> TxReceipt:
        return self._send("request(uint256)", to_withdraw)

    def request_shares(self, shares: Shares) -> TxReceipt:
        return self._send("requestShares(uint256)", shares)

    def update_withdraw_window(self, window: Period) -> TxReceipt:
        return self._send("updateWithdrawWindow(uint256)", window)

    def update_plasma_vault_address(self, vault: ChecksumAddress) -> TxReceipt:
        return self._send("updatePlasmaVaultAddress(address)", vault)

    def release_funds(
        self, timestamp: Timestamp | None = None, shares: Shares | None = None
    ) -> TxReceipt:
        if shares is not None:
            if timestamp is None:
                raise ValueError("timestamp is required when shares is provided")
            return self._send("releaseFunds(uint256,uint256)", timestamp, shares)
        if timestamp is not None:
            return self._send("releaseFunds(uint256)", timestamp)
        return self._send("releaseFunds()")

    def get_withdraw_window(self) -> Period:
        (value,) = decode(["uint256"], self._call("getWithdrawWindow()"))
        return value

    def get_last_release_funds_timestamp(self) -> Timestamp:
        (value,) = decode(["uint256"], self._call("getLastReleaseFundsTimestamp()"))
        return value

    def get_shares_to_release(self) -> Shares:
        (value,) = decode(["uint256"], self._call("getSharesToRelease()"))
        return value

    def get_request_fee(self) -> int:
        (value,) = decode(["uint256"], self._call("getRequestFee()"))
        return value

    def request_info(self, account: ChecksumAddress) -> WithdrawRequestInfo:
        result = self._call("requestInfo(address)", account)
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

from __future__ import annotations

import logging
from dataclasses import dataclass

from eth_abi import decode
from eth_typing import BlockNumber, ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractPanicError
from web3.types import TxReceipt, LogReceipt, Timestamp

from ipor_fusion.core.contract import ContractWrapper
from ipor_fusion.types import Shares, Amount, Fee, Period

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class PendingRequestsInfo:
    """Aggregated pending withdrawal requests across all accounts."""

    shares: Shares
    timestamp: Timestamp


@dataclass(slots=True)
class WithdrawRequestInfo:
    """Snapshot of a pending withdrawal request for a single account."""

    shares: Shares
    end_withdraw_window_timestamp: Timestamp
    can_withdraw: bool
    withdraw_window_in_seconds: Period


@dataclass(slots=True)
class AccountRequest:
    """On-chain validated withdrawal request for a single account."""

    account: ChecksumAddress
    shares: Shares
    end_withdraw_window_timestamp: Timestamp
    can_withdraw: bool


class WithdrawManager(ContractWrapper):
    """Handles time-windowed withdrawal requests and fund releases."""

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
        return Period(value)

    def get_last_release_funds_timestamp(self) -> Timestamp:
        (value,) = decode(["uint256"], self._call("getLastReleaseFundsTimestamp()"))
        return value

    def get_shares_to_release(self) -> Shares:
        (value,) = decode(["uint256"], self._call("getSharesToRelease()"))
        return Shares(value)

    def get_request_fee(self) -> Fee:
        (value,) = decode(["uint256"], self._call("getRequestFee()"))
        return Fee(value)

    def get_withdraw_fee(self) -> Fee:
        (value,) = decode(["uint256"], self._call("getWithdrawFee()"))
        return Fee(value)

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

    def get_pending_requests(
        self, from_block: BlockNumber = BlockNumber(0)
    ) -> list[AccountRequest]:
        """Return per-account validated withdrawal requests (active only)."""
        current_timestamp = self._ctx.get_block()["timestamp"]
        events = self._get_withdraw_request_updated_events(from_block=from_block)

        accounts: list[str] = []
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

        results: list[AccountRequest] = []
        for account in accounts:
            try:
                req = self.request_info(Web3.to_checksum_address(account))
                if req.end_withdraw_window_timestamp > current_timestamp:
                    results.append(
                        AccountRequest(
                            account=Web3.to_checksum_address(account),
                            shares=Shares(req.shares),
                            end_withdraw_window_timestamp=req.end_withdraw_window_timestamp,
                            can_withdraw=req.can_withdraw,
                        )
                    )
            except ContractPanicError:
                logger.warning("ContractPanicError for account %s", account)

        return results

    def get_pending_requests_info(
        self, from_block: BlockNumber = BlockNumber(0)
    ) -> PendingRequestsInfo:
        current_timestamp = self._ctx.get_block()["timestamp"]
        requests = self.get_pending_requests(from_block=from_block)
        total = sum(r.shares for r in requests)
        return PendingRequestsInfo(
            shares=Shares(total),
            timestamp=Timestamp(current_timestamp - 1),
        )

    def _get_withdraw_request_updated_events(
        self, from_block: BlockNumber = BlockNumber(0)
    ) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="WithdrawRequestUpdated(address,uint256,uint32)")
        ).to_0x_hex()
        return list(
            self._ctx.get_logs(
                contract_address=self._address,
                topics=[event_signature_hash],
                from_block=from_block,
            )
        )

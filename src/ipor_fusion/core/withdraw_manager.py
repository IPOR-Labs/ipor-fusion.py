from __future__ import annotations

import logging
from dataclasses import dataclass

from eth_abi import decode
from eth_typing import BlockNumber, ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.exceptions import ContractPanicError
from web3.types import LogReceipt, Timestamp

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.types import Amount, Fee, Period, Shares

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


def _withdraw_request_info_decoder(value: tuple) -> WithdrawRequestInfo:
    amount, end_withdraw_window_timestamp, can_withdraw, withdraw_window_in_seconds = (
        value
    )
    return WithdrawRequestInfo(
        shares=amount,
        end_withdraw_window_timestamp=end_withdraw_window_timestamp,
        can_withdraw=can_withdraw,
        withdraw_window_in_seconds=withdraw_window_in_seconds,
    )


class WithdrawManager(ContractWrapper):
    """Handles time-windowed withdrawal requests and fund releases."""

    def request(self, to_withdraw: Amount) -> Call[None]:
        return self._write("request(uint256)", to_withdraw)

    def request_shares(self, shares: Shares) -> Call[None]:
        return self._write("requestShares(uint256)", shares)

    def update_withdraw_window(self, window: Period) -> Call[None]:
        return self._write("updateWithdrawWindow(uint256)", window)

    def update_plasma_vault_address(self, vault: ChecksumAddress) -> Call[None]:
        return self._write("updatePlasmaVaultAddress(address)", vault)

    def release_funds(
        self, timestamp: Timestamp | None = None, shares: Shares | None = None
    ) -> Call[None]:
        if shares is not None:
            if timestamp is None:
                raise ValueError("timestamp is required when shares is provided")
            return self._write("releaseFunds(uint256,uint256)", timestamp, shares)
        if timestamp is not None:
            return self._write("releaseFunds(uint256)", timestamp)
        return self._write("releaseFunds()")

    def get_withdraw_window(self) -> Call[Period]:
        return self._view(
            "getWithdrawWindow()", output_types=["uint256"], decoder=Period
        )

    def get_last_release_funds_timestamp(self) -> Call[Timestamp]:
        return self._view("getLastReleaseFundsTimestamp()", output_types=["uint256"])

    def get_shares_to_release(self) -> Call[Shares]:
        return self._view(
            "getSharesToRelease()", output_types=["uint256"], decoder=Shares
        )

    def get_request_fee(self) -> Call[Fee]:
        return self._view("getRequestFee()", output_types=["uint256"], decoder=Fee)

    def get_withdraw_fee(self) -> Call[Fee]:
        return self._view("getWithdrawFee()", output_types=["uint256"], decoder=Fee)

    def request_info(self, account: ChecksumAddress) -> Call[WithdrawRequestInfo]:
        return self._view(
            "requestInfo(address)",
            account,
            output_types=["uint256", "uint256", "bool", "uint256"],
            decoder=_withdraw_request_info_decoder,
        )

    # ── Compound methods: event aggregation + per-account request_info reads ──

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
                req = self.request_info(Web3.to_checksum_address(account)).call()
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

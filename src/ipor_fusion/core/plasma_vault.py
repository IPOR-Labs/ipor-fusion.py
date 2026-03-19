from __future__ import annotations

from eth_abi import decode
from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.types import TxReceipt, LogReceipt

from ipor_fusion.core.contract import ContractWrapper
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.types import Amount, MarketId, Decimals, Shares


class PlasmaVault(ContractWrapper):
    """ERC-4626 vault that batches and executes FuseAction sequences on-chain."""

    def execute(self, actions: list[FuseAction]) -> TxReceipt:
        data = self._encode_execute(actions)
        return self._ctx.send(self._address, data)

    def deposit(self, assets: Amount, receiver: ChecksumAddress) -> TxReceipt:
        return self._send("deposit(uint256,address)", assets, receiver)

    def mint(self, shares: Shares, receiver: ChecksumAddress) -> TxReceipt:
        return self._send("mint(uint256,address)", shares, receiver)

    def withdraw(
        self, assets: Amount, receiver: ChecksumAddress, owner: ChecksumAddress
    ) -> TxReceipt:
        return self._send("withdraw(uint256,address,address)", assets, receiver, owner)

    def redeem(
        self, shares: Shares, receiver: ChecksumAddress, owner: ChecksumAddress
    ) -> TxReceipt:
        return self._send("redeem(uint256,address,address)", shares, receiver, owner)

    def redeem_from_request(
        self, shares: Shares, receiver: ChecksumAddress, owner: ChecksumAddress
    ) -> TxReceipt:
        return self._send(
            "redeemFromRequest(uint256,address,address)", shares, receiver, owner
        )

    def add_fuses(self, fuses: list[ChecksumAddress]) -> TxReceipt:
        return self._send("addFuses(address[])", fuses)

    def set_total_supply_cap(self, cap: int) -> TxReceipt:
        return self._send("setTotalSupplyCap(uint256)", cap)

    def transfer(self, to: ChecksumAddress, value: Amount) -> TxReceipt:
        return self._send("transfer(address,uint256)", to, value)

    def approve(self, account: ChecksumAddress, amount: Amount) -> TxReceipt:
        return self._send("approve(address,uint256)", account, amount)

    def transfer_from(
        self, _from: ChecksumAddress, to: ChecksumAddress, amount: Amount
    ) -> TxReceipt:
        return self._send("transferFrom(address,address,uint256)", _from, to, amount)

    def balance_of(self, account: ChecksumAddress) -> Amount:
        (value,) = decode(["uint256"], self._call("balanceOf(address)", account))
        return Amount(value)

    def get_total_supply_cap(self) -> Amount:
        (value,) = decode(["uint256"], self._call("getTotalSupplyCap()"))
        return Amount(value)

    def max_withdraw(self, account: ChecksumAddress) -> Amount:
        (value,) = decode(["uint256"], self._call("maxWithdraw(address)", account))
        return Amount(value)

    def convert_to_shares(self, amount: Amount) -> Shares:
        (value,) = decode(["uint256"], self._call("convertToShares(uint256)", amount))
        return Shares(value)

    def convert_to_assets(self, shares: Shares) -> Amount:
        (value,) = decode(["uint256"], self._call("convertToAssets(uint256)", shares))
        return Amount(value)

    def total_assets_in_market(self, market: MarketId) -> Amount:
        (value,) = decode(
            ["uint256"], self._call("totalAssetsInMarket(uint256)", market)
        )
        return Amount(value)

    def decimals(self) -> Decimals:
        (value,) = decode(["uint256"], self._call("decimals()"))
        return Decimals(value)

    def total_assets(self) -> Amount:
        (value,) = decode(["uint256"], self._call("totalAssets()"))
        return Amount(value)

    def underlying_asset_address(self) -> ChecksumAddress:
        (value,) = decode(["address"], self._call("asset()"))
        return Web3.to_checksum_address(value)

    def get_access_manager_address(self) -> ChecksumAddress:
        (value,) = decode(["address"], self._call("getAccessManagerAddress()"))
        return Web3.to_checksum_address(value)

    def get_rewards_claim_manager_address(self) -> ChecksumAddress:
        (value,) = decode(["address"], self._call("getRewardsClaimManagerAddress()"))
        return Web3.to_checksum_address(value)

    def get_price_oracle_middleware_address(self) -> ChecksumAddress:
        (value,) = decode(["address"], self._call("getPriceOracleMiddleware()"))
        return Web3.to_checksum_address(value)

    def get_fuses(self) -> list[ChecksumAddress]:
        (value,) = decode(["address[]"], self._call("getFuses()"))
        return [Web3.to_checksum_address(item) for item in list(value)]

    def get_balance_fuses(self) -> list[tuple[MarketId, ChecksumAddress]]:
        events = self._get_balance_fuse_added_events()
        result = []
        for event in events:
            (market_id, fuse) = decode(["uint256", "address"], event["data"])
            result.append((market_id, Web3.to_checksum_address(fuse)))
        return result

    def withdraw_manager_address(self) -> ChecksumAddress | None:
        events = self._get_withdraw_manager_changed_events()
        sorted_events = sorted(
            events, key=lambda event: event["blockNumber"], reverse=True
        )
        if sorted_events:
            (decoded_address,) = decode(["address"], sorted_events[0]["data"])
            return Web3.to_checksum_address(decoded_address)
        return None

    def get_instant_withdrawal_fuses(self) -> list[ChecksumAddress]:
        (value,) = decode(["address[]"], self._call("getInstantWithdrawalFuses()"))
        return [Web3.to_checksum_address(item) for item in list(value)]

    def get_instant_withdrawal_fuses_params(
        self, fuse: ChecksumAddress, index: int
    ) -> list[bytes]:
        (value,) = decode(
            ["bytes32[]"],
            self._call("getInstantWithdrawalFusesParams(address,uint256)", fuse, index),
        )
        return list(value)

    def get_market_substrates(self, market_id: MarketId) -> list[bytes]:
        (value,) = decode(
            ["bytes32[]"], self._call("getMarketSubstrates(uint256)", market_id)
        )
        return list(value)

    @staticmethod
    def _encode_execute(actions: list[FuseAction]) -> bytes:
        return FuseAction.encode_execute_payload(actions, "execute((address,bytes)[])")

    def _get_withdraw_manager_changed_events(self) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="WithdrawManagerChanged(address)")
        ).to_0x_hex()
        return list(
            self._ctx.get_logs(
                contract_address=self._address, topics=[event_signature_hash]
            )
        )

    def _get_balance_fuse_added_events(self) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="BalanceFuseAdded(uint256,address)")
        ).to_0x_hex()
        return list(
            self._ctx.get_logs(
                contract_address=self._address, topics=[event_signature_hash]
            )
        )

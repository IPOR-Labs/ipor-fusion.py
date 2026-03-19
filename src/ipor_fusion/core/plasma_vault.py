from __future__ import annotations

from eth_abi import encode, decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.types import TxReceipt, LogReceipt

from ipor_fusion.core.context import Web3Context
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.types import Amount, MarketId, Decimals, Shares


class PlasmaVault:

    def __init__(self, ctx: Web3Context, address: ChecksumAddress):
        self._ctx = ctx
        self._address = Web3.to_checksum_address(address)

    @property
    def address(self) -> ChecksumAddress:
        return self._address

    def execute(self, actions: list[FuseAction]) -> TxReceipt:
        data = self._encode_execute(actions)
        return self._ctx.send(self._address, data)

    def deposit(self, assets: Amount, receiver: ChecksumAddress) -> TxReceipt:
        sig = function_signature_to_4byte_selector("deposit(uint256,address)")
        data = sig + encode(["uint256", "address"], [assets, receiver])
        return self._ctx.send(self._address, data)

    def mint(self, shares: Shares, receiver: ChecksumAddress) -> TxReceipt:
        sig = function_signature_to_4byte_selector("mint(uint256,address)")
        data = sig + encode(["uint256", "address"], [shares, receiver])
        return self._ctx.send(self._address, data)

    def withdraw(
        self, assets: Amount, receiver: ChecksumAddress, owner: ChecksumAddress
    ) -> TxReceipt:
        sig = function_signature_to_4byte_selector("withdraw(uint256,address,address)")
        data = sig + encode(
            ["uint256", "address", "address"], [assets, receiver, owner]
        )
        return self._ctx.send(self._address, data)

    def redeem(
        self, shares: Shares, receiver: ChecksumAddress, owner: ChecksumAddress
    ) -> TxReceipt:
        sig = function_signature_to_4byte_selector("redeem(uint256,address,address)")
        data = sig + encode(
            ["uint256", "address", "address"], [shares, receiver, owner]
        )
        return self._ctx.send(self._address, data)

    def redeem_from_request(
        self, shares: Shares, receiver: ChecksumAddress, owner: ChecksumAddress
    ) -> TxReceipt:
        sig = function_signature_to_4byte_selector(
            "redeemFromRequest(uint256,address,address)"
        )
        data = sig + encode(
            ["uint256", "address", "address"], [shares, receiver, owner]
        )
        return self._ctx.send(self._address, data)

    def add_fuses(self, fuses: list[ChecksumAddress]) -> TxReceipt:
        sig = function_signature_to_4byte_selector("addFuses(address[])")
        data = sig + encode(["address[]"], [fuses])
        return self._ctx.send(self._address, data)

    def set_total_supply_cap(self, cap: int) -> TxReceipt:
        sig = function_signature_to_4byte_selector("setTotalSupplyCap(uint256)")
        data = sig + encode(["uint256"], [cap])
        return self._ctx.send(self._address, data)

    def transfer(self, to: ChecksumAddress, value: Amount) -> TxReceipt:
        sig = function_signature_to_4byte_selector("transfer(address,uint256)")
        data = sig + encode(["address", "uint256"], [to, value])
        return self._ctx.send(self._address, data)

    def approve(self, account: ChecksumAddress, amount: Amount) -> TxReceipt:
        sig = function_signature_to_4byte_selector("approve(address,uint256)")
        data = sig + encode(["address", "uint256"], [account, amount])
        return self._ctx.send(self._address, data)

    def transfer_from(
        self, _from: ChecksumAddress, to: ChecksumAddress, amount: Amount
    ) -> TxReceipt:
        sig = function_signature_to_4byte_selector(
            "transferFrom(address,address,uint256)"
        )
        data = sig + encode(["address", "address", "uint256"], [_from, to, amount])
        return self._ctx.send(self._address, data)

    def balance_of(self, account: ChecksumAddress) -> Amount:
        sig = function_signature_to_4byte_selector("balanceOf(address)")
        result = self._ctx.call(self._address, sig + encode(["address"], [account]))
        (value,) = decode(["uint256"], result)
        return value

    def get_total_supply_cap(self) -> Amount:
        sig = function_signature_to_4byte_selector("getTotalSupplyCap()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["uint256"], result)
        return value

    def max_withdraw(self, account: ChecksumAddress) -> Amount:
        sig = function_signature_to_4byte_selector("maxWithdraw(address)")
        result = self._ctx.call(self._address, sig + encode(["address"], [account]))
        (value,) = decode(["uint256"], result)
        return value

    def convert_to_shares(self, amount: Amount) -> Shares:
        sig = function_signature_to_4byte_selector("convertToShares(uint256)")
        result = self._ctx.call(self._address, sig + encode(["uint256"], [amount]))
        (value,) = decode(["uint256"], result)
        return value

    def convert_to_assets(self, shares: Shares) -> Amount:
        sig = function_signature_to_4byte_selector("convertToAssets(uint256)")
        result = self._ctx.call(self._address, sig + encode(["uint256"], [shares]))
        (value,) = decode(["uint256"], result)
        return value

    def total_assets_in_market(self, market: MarketId) -> Amount:
        sig = function_signature_to_4byte_selector("totalAssetsInMarket(uint256)")
        result = self._ctx.call(self._address, sig + encode(["uint256"], [market]))
        (value,) = decode(["uint256"], result)
        return value

    def decimals(self) -> Decimals:
        sig = function_signature_to_4byte_selector("decimals()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["uint256"], result)
        return value

    def total_assets(self) -> Amount:
        sig = function_signature_to_4byte_selector("totalAssets()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["uint256"], result)
        return value

    def underlying_asset_address(self) -> ChecksumAddress:
        sig = function_signature_to_4byte_selector("asset()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["address"], result)
        return Web3.to_checksum_address(value)

    def get_access_manager_address(self) -> ChecksumAddress:
        sig = function_signature_to_4byte_selector("getAccessManagerAddress()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["address"], result)
        return Web3.to_checksum_address(value)

    def get_rewards_claim_manager_address(self) -> ChecksumAddress:
        sig = function_signature_to_4byte_selector("getRewardsClaimManagerAddress()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["address"], result)
        return Web3.to_checksum_address(value)

    def get_price_oracle_middleware_address(self) -> ChecksumAddress:
        sig = function_signature_to_4byte_selector("getPriceOracleMiddleware()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["address"], result)
        return Web3.to_checksum_address(value)

    def get_fuses(self) -> list[ChecksumAddress]:
        sig = function_signature_to_4byte_selector("getFuses()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["address[]"], result)
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
        sig = function_signature_to_4byte_selector("getInstantWithdrawalFuses()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["address[]"], result)
        return [Web3.to_checksum_address(item) for item in list(value)]

    def get_instant_withdrawal_fuses_params(
        self, fuse: ChecksumAddress, index: int
    ) -> list[bytes]:
        sig = function_signature_to_4byte_selector(
            "getInstantWithdrawalFusesParams(address,uint256)"
        )
        result = self._ctx.call(
            self._address, sig + encode(["address", "uint256"], [fuse, index])
        )
        (value,) = decode(["bytes32[]"], result)
        return list(value)

    def get_market_substrates(self, market_id: MarketId) -> list[bytes]:
        sig = function_signature_to_4byte_selector("getMarketSubstrates(uint256)")
        result = self._ctx.call(self._address, sig + encode(["uint256"], [market_id]))
        (value,) = decode(["bytes32[]"], result)
        return list(value)

    def _encode_execute(self, actions: list[FuseAction]) -> bytes:
        bytes_data = [[action.fuse, action.data] for action in actions]
        encoded = encode(["(address,bytes)[]"], [bytes_data])
        return (
            function_signature_to_4byte_selector("execute((address,bytes)[])") + encoded
        )

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

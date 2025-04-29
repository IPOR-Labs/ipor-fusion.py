from typing import List, Optional

from eth_abi import encode, decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.types import TxReceipt, LogReceipt

from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.PriceOracleMiddleware import PriceOracleMiddleware
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.types import Amount, MarketId, Decimals, Shares


# pylint: disable=too-many-public-methods
class PlasmaVault:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        plasma_vault_address: ChecksumAddress,
    ):
        self._transaction_executor = transaction_executor
        self._plasma_vault_address = plasma_vault_address

    def address(self) -> ChecksumAddress:
        return Web3.to_checksum_address(self._plasma_vault_address)

    def execute(self, actions: List[FuseAction]) -> TxReceipt:
        function = self.__execute(actions)
        return self._transaction_executor.execute(self._plasma_vault_address, function)

    def prepare_transaction(self, actions: List[FuseAction]) -> TxReceipt:
        function = self.__execute(actions)
        return self._transaction_executor.prepare_transaction(
            self._plasma_vault_address, function
        )

    def deposit(self, assets: Amount, receiver: ChecksumAddress) -> TxReceipt:
        function = self.__deposit(assets, receiver)
        return self._transaction_executor.execute(self._plasma_vault_address, function)

    def mint(self, shares: Shares, receiver: ChecksumAddress) -> TxReceipt:
        sig = function_signature_to_4byte_selector("mint(uint256,address)")
        encoded_args = encode(["uint256", "address"], [shares, receiver])
        return self._transaction_executor.execute(
            self._plasma_vault_address, sig + encoded_args
        )

    def add_fuses(self, fuses: List[ChecksumAddress]) -> TxReceipt:
        sig = function_signature_to_4byte_selector("addFuses(address[])")
        encoded_args = encode(["address[]"], [fuses])
        return self._transaction_executor.execute(
            self._plasma_vault_address, sig + encoded_args
        )

    def redeem(
        self, shares: Shares, receiver: ChecksumAddress, owner: ChecksumAddress
    ) -> TxReceipt:
        sig = function_signature_to_4byte_selector("redeem(uint256,address,address)")
        encoded_args = encode(
            ["uint256", "address", "address"], [shares, receiver, owner]
        )
        return self._transaction_executor.execute(
            self._plasma_vault_address, sig + encoded_args
        )

    def redeem_from_request(
        self, shares: Shares, receiver: ChecksumAddress, owner: ChecksumAddress
    ) -> TxReceipt:
        sig = function_signature_to_4byte_selector(
            "redeemFromRequest(uint256,address,address)"
        )
        encoded_args = encode(
            ["uint256", "address", "address"], [shares, receiver, owner]
        )
        return self._transaction_executor.execute(
            self._plasma_vault_address, sig + encoded_args
        )

    def balance_of(self, account: ChecksumAddress) -> Amount:
        sig = function_signature_to_4byte_selector("balanceOf(address)")
        encoded_args = encode(["address"], [account])
        read = self._transaction_executor.read(
            self._plasma_vault_address, sig + encoded_args
        )
        (result,) = decode(["uint256"], read)
        return result

    def max_withdraw(self, account: ChecksumAddress) -> Amount:
        sig = function_signature_to_4byte_selector("maxWithdraw(address)")
        encoded_args = encode(["address"], [account])
        read = self._transaction_executor.read(
            self._plasma_vault_address, sig + encoded_args
        )
        (result,) = decode(["uint256"], read)
        return result

    def convert_to_shares(self, amount: Amount) -> Shares:
        sig = function_signature_to_4byte_selector("convertToShares(uint256)")
        encoded_args = encode(["uint256"], [amount])
        read = self._transaction_executor.read(
            self._plasma_vault_address, sig + encoded_args
        )
        (result,) = decode(["uint256"], read)
        return result

    def total_assets_in_market(self, market: MarketId) -> Amount:
        sig = function_signature_to_4byte_selector("totalAssetsInMarket(uint256)")
        encoded_args = encode(["uint256"], [market])
        read = self._transaction_executor.read(
            self._plasma_vault_address, sig + encoded_args
        )
        (result,) = decode(["uint256"], read)
        return result

    def decimals(self) -> Decimals:
        sig = function_signature_to_4byte_selector("decimals()")
        read = self._transaction_executor.read(self._plasma_vault_address, sig)
        (result,) = decode(["uint256"], read)
        return result

    def get_price_oracle_middleware_address(self) -> ChecksumAddress:
        sig = function_signature_to_4byte_selector("getPriceOracleMiddleware()")
        read = self._transaction_executor.read(self._plasma_vault_address, sig)
        (result,) = decode(["address"], read)
        return Web3.to_checksum_address(result)

    def get_price_oracle_middleware(self) -> PriceOracleMiddleware:
        return PriceOracleMiddleware(
            transaction_executor=self._transaction_executor,
            price_oracle_middleware_address=self.get_price_oracle_middleware_address(),
        )

    def total_assets(self) -> Amount:
        sig = function_signature_to_4byte_selector("totalAssets()")
        read = self._transaction_executor.read(self._plasma_vault_address, sig)
        (result,) = decode(["uint256"], read)
        return result

    def underlying_asset_address(self) -> ChecksumAddress:
        sig = function_signature_to_4byte_selector("asset()")
        read = self._transaction_executor.read(self._plasma_vault_address, sig)
        (result,) = decode(["address"], read)
        return Web3.to_checksum_address(result)

    def convert_to_assets(self, amount: Shares) -> Amount:
        sig = function_signature_to_4byte_selector("convertToAssets(uint256)")
        encoded_args = encode(["uint256"], [amount])
        read = self._transaction_executor.read(
            self._plasma_vault_address, sig + encoded_args
        )
        (result,) = decode(["uint256"], read)
        return result

    def get_access_manager_address(self) -> ChecksumAddress:
        sig = function_signature_to_4byte_selector("getAccessManagerAddress()")
        read = self._transaction_executor.read(self._plasma_vault_address, sig)
        (result,) = decode(["address"], read)
        return Web3.to_checksum_address(result)

    def get_rewards_claim_manager_address(self) -> ChecksumAddress:
        sig = function_signature_to_4byte_selector("getRewardsClaimManagerAddress()")
        read = self._transaction_executor.read(self._plasma_vault_address, sig)
        (result,) = decode(["address"], read)
        return Web3.to_checksum_address(result)

    def get_fuses(self) -> List[ChecksumAddress]:
        sig = function_signature_to_4byte_selector("getFuses()")
        read = self._transaction_executor.read(self._plasma_vault_address, sig)
        (result,) = decode(["address[]"], read)
        return [Web3.to_checksum_address(item) for item in list(result)]

    def get_balance_fuses(self) -> List[tuple[MarketId, ChecksumAddress]]:
        events = self.get_balance_fuse_added_events()
        result = []
        for event in events:
            (market_id, fuse) = decode(["uint256", "address"], event["data"])
            result.append((market_id, fuse))
        return result

    def withdraw_manager_address(self) -> Optional[ChecksumAddress]:
        events = self.get_withdraw_manager_changed_events()
        sorted_events = sorted(
            events, key=lambda event: event["blockNumber"], reverse=True
        )
        if sorted_events:
            (decoded_address,) = decode(["address"], sorted_events[0]["data"])
            return Web3.to_checksum_address(decoded_address)
        return None

    def get_instant_withdrawal_fuses(self) -> List[ChecksumAddress]:
        sig = function_signature_to_4byte_selector("getInstantWithdrawalFuses()")
        read = self._transaction_executor.read(self._plasma_vault_address, sig)
        (result,) = decode(["address[]"], read)
        return [Web3.to_checksum_address(item) for item in list(result)]

    def get_instant_withdrawal_fuses_params(
        self, fuse: ChecksumAddress, index: int
    ) -> List[str]:
        sig = function_signature_to_4byte_selector(
            "getInstantWithdrawalFusesParams(address,uint256)"
        )
        encoded_args = encode(["address", "uint256"], [fuse, index])
        read = self._transaction_executor.read(
            self._plasma_vault_address, sig + encoded_args
        )
        (result,) = decode(["bytes32[]"], read)
        return result

    @staticmethod
    def __execute(actions: List[FuseAction]) -> bytes:
        bytes_data = []
        for action in actions:
            bytes_data.append([action.fuse, action.data])
        bytes_ = "(address,bytes)[]"
        encoded_arguments = encode([bytes_], [bytes_data])
        return (
            function_signature_to_4byte_selector("execute((address,bytes)[])")
            + encoded_arguments
        )

    @staticmethod
    def __deposit(assets: Amount, receiver: ChecksumAddress) -> bytes:
        args = ["uint256", "address"]
        join = ",".join(args)
        function_signature = f"deposit({join})"
        selector = function_signature_to_4byte_selector(function_signature)
        return selector + encode(args, [assets, receiver])

    def withdraw(
        self, assets: Amount, receiver: ChecksumAddress, owner: ChecksumAddress
    ) -> TxReceipt:
        sig = function_signature_to_4byte_selector("withdraw(uint256,address,address)")
        encoded_args = encode(
            ["uint256", "address", "address"], [assets, receiver, owner]
        )
        return self._transaction_executor.execute(
            self._plasma_vault_address, sig + encoded_args
        )

    def get_market_substrates(self, market_id: MarketId) -> List[bytes]:
        sig = function_signature_to_4byte_selector("getMarketSubstrates(uint256)")
        encoded_args = encode(["uint256"], [market_id])
        read = self._transaction_executor.read(
            self._plasma_vault_address, sig + encoded_args
        )
        (result,) = decode(["bytes32[]"], read)
        return result

    def transfer(self, to: ChecksumAddress, value: Amount):
        sig = function_signature_to_4byte_selector("transfer(address,uint256)")
        encoded_args = encode(["address", "uint256"], [to, value])
        return self._transaction_executor.execute(
            self._plasma_vault_address, sig + encoded_args
        )

    def approve(self, account: ChecksumAddress, amount: Amount):
        sig = function_signature_to_4byte_selector("approve(address,uint256)")
        encoded_args = encode(["address", "uint256"], [account, amount])
        return self._transaction_executor.execute(
            self._plasma_vault_address, sig + encoded_args
        )

    def transfer_from(
        self, _from: ChecksumAddress, to: ChecksumAddress, amount: Amount
    ):
        sig = function_signature_to_4byte_selector(
            "transferFrom(address,address,uint256)"
        )
        encoded_args = encode(["address", "address", "uint256"], [_from, to, amount])
        return self._transaction_executor.execute(
            self._plasma_vault_address, sig + encoded_args
        )

    def get_withdraw_manager_changed_events(self) -> List[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="WithdrawManagerChanged(address)")
        ).to_0x_hex()
        logs = self._transaction_executor.get_logs(
            contract_address=self._plasma_vault_address, topics=[event_signature_hash]
        )
        return list(logs)

    def get_balance_fuse_added_events(self) -> List[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="BalanceFuseAdded(uint256,address)")
        ).to_0x_hex()
        logs = self._transaction_executor.get_logs(
            contract_address=self._plasma_vault_address, topics=[event_signature_hash]
        )
        return list(logs)

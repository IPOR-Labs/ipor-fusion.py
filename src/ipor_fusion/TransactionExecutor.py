from typing import List

from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.types import TxReceipt, LogReceipt


class TransactionExecutor:
    DEFAULT_TRANSACTION_MAX_PRIORITY_FEE = 2_000_000_000
    GAS_PRICE_MARGIN = 25

    def __init__(self, web3, account, gas_multiplier=1.25):
        self._web3 = web3
        self._account = account
        self._gas_multiplier = gas_multiplier

    def get_web3(self) -> Web3:
        return self._web3

    def get_account_address(self) -> ChecksumAddress:
        return Web3.to_checksum_address(self._account.address)

    def execute(self, contract_address: ChecksumAddress, function: bytes) -> TxReceipt:
        transaction = self.prepare_transaction(contract_address, function)
        signed_tx = self._web3.eth.account.sign_transaction(
            transaction, self._account.key
        )
        tx_hash = self._web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = self._web3.eth.wait_for_transaction_receipt(tx_hash)
        assert receipt["status"] == 1, "Transaction failed"
        return receipt

    def prepare_transaction(self, contract_address, function):
        nonce = self._web3.eth.get_transaction_count(self._account.address)
        gas_price = self._web3.eth.gas_price
        max_fee_per_gas = self.calculate_max_fee_per_gas(gas_price)
        max_priority_fee_per_gas = self.get_max_priority_fee(gas_price)
        data = f"0x{function.hex()}"
        estimated_gas = self.estimate_gas(contract_address, data)
        return {
            "chainId": self._web3.eth.chain_id,
            "gas": estimated_gas,
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": max_priority_fee_per_gas,
            "to": contract_address,
            "from": self._account.address,
            "nonce": nonce,
            "data": data,
        }

    def estimate_gas(self, contract_address, data) -> int:
        return int(
            self._gas_multiplier
            * self._web3.eth.estimate_gas(
                {"to": contract_address, "from": self._account.address, "data": data}
            )
        )

    def read(self, contract, data) -> HexBytes:
        return self._web3.eth.call({"to": contract, "data": data})

    def calculate_max_fee_per_gas(self, gas_price):
        return gas_price + self.percent_of(gas_price, self.GAS_PRICE_MARGIN)

    def get_max_priority_fee(self, gas_price):
        return min(self.DEFAULT_TRANSACTION_MAX_PRIORITY_FEE, gas_price // 10)

    @staticmethod
    def percent_of(value, percentage):
        return value * percentage // 100

    def get_logs(
        self,
        contract_address: ChecksumAddress,
        topics: List[str],
        from_block=0,
        to_block="latest",
    ) -> List[LogReceipt]:
        event_filter = self._web3.eth.filter(
            {
                "fromBlock": from_block,
                "toBlock": to_block,
                "address": contract_address,
                "topics": topics,
            }
        )
        return event_filter.get_all_entries()

    def get_block(self, block="latest"):
        return self._web3.eth.get_block(block)

    def chain_id(self):
        return self._web3.eth.chain_id

    def prank(self, account: str):
        raise NotImplementedError("Use CheatingTransactionExecutor for pranks")

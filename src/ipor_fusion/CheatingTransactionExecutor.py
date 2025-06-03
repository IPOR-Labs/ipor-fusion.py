from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.TransactionExecutor import TransactionExecutor


class CheatingTransactionExecutor(TransactionExecutor):

    def __init__(
        self, web3: Web3, account_address: ChecksumAddress, gas_multiplier=1.25
    ):
        self._account_address = account_address
        super().__init__(web3, account_address, gas_multiplier)

    def prank(self, account: str):
        self._account_address = account

    def execute(self, contract_address: ChecksumAddress, function: bytes) -> TxReceipt:
        nonce = self._web3.eth.get_transaction_count(self._account_address)
        gas_price = self._web3.eth.gas_price
        max_fee_per_gas = self.calculate_max_fee_per_gas(gas_price)
        max_priority_fee_per_gas = self.get_max_priority_fee(gas_price)
        data = f"0x{function.hex()}"
        estimated_gas = int(
            self._gas_multiplier
            * self._web3.eth.estimate_gas(
                {"to": contract_address, "from": self._account_address, "data": data}
            )
        )

        transaction = {
            "chainId": self._web3.eth.chain_id,
            "gas": estimated_gas,
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": max_priority_fee_per_gas,
            "to": contract_address,
            "from": self._account_address,
            "nonce": nonce,
            "data": data,
        }

        tx_hash = self._web3.eth.send_transaction(transaction)
        receipt = self._web3.eth.wait_for_transaction_receipt(tx_hash)

        assert receipt["status"] == 1, "Transaction failed"
        return receipt

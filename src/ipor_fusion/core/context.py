from __future__ import annotations

from eth_account import Account
from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.types import TxReceipt, LogReceipt, BlockIdentifier, FilterParams

from ipor_fusion.errors import TransactionError, _get_revert_reason


class Web3Context:
    """Manages Web3 connection, signing, and transaction dispatch."""

    DEFAULT_TRANSACTION_MAX_PRIORITY_FEE = 2_000_000_000
    GAS_PRICE_MARGIN = 25

    def __init__(
        self,
        web3: Web3,
        chain_id: int,
        signer: ChecksumAddress | None = None,
        private_key: str | None = None,
        gas_multiplier: float = 1.25,
    ):
        self._web3 = web3
        self._chain_id = chain_id
        self._private_key = private_key
        self._gas_multiplier = gas_multiplier
        self._signer: ChecksumAddress | None = None

        if signer:
            self._signer = signer
        elif private_key:
            account = Account.from_key(private_key)
            self._signer = Web3.to_checksum_address(account.address)

    @property
    def web3(self) -> Web3:
        return self._web3

    @property
    def chain_id(self) -> int:
        return self._chain_id

    @property
    def signer(self) -> ChecksumAddress | None:
        return self._signer

    @classmethod
    def from_url(
        cls,
        url: str,
        private_key: str | None = None,
        gas_multiplier: float = 1.25,
    ) -> "Web3Context":
        web3 = Web3(Web3.HTTPProvider(url))
        chain_id = web3.eth.chain_id

        return cls(
            web3=web3,
            chain_id=chain_id,
            private_key=private_key,
            gas_multiplier=gas_multiplier,
        )

    def call(self, to: ChecksumAddress, data: bytes) -> HexBytes:
        return self.web3.eth.call({"to": to, "data": data})

    def send(self, to: ChecksumAddress, data: bytes) -> TxReceipt:
        if not self._private_key or not self._signer:
            raise ValueError("Private key required for sending transactions")

        nonce = self.web3.eth.get_transaction_count(self._signer)
        gas_price = self.web3.eth.gas_price
        max_fee_per_gas = self._calculate_max_fee_per_gas(gas_price)
        max_priority_fee_per_gas = self._get_max_priority_fee(gas_price)

        data_hex = f"0x{data.hex()}"
        estimated_gas = self._estimate_gas(to, data_hex, self._signer)

        transaction = {
            "chainId": self.chain_id,
            "gas": estimated_gas,
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": max_priority_fee_per_gas,
            "to": to,
            "from": self._signer,
            "nonce": nonce,
            "data": data_hex,
        }

        signed_tx = self.web3.eth.account.sign_transaction(
            transaction, self._private_key
        )
        tx_hash = self.web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt["status"] != 1:
            reason = _get_revert_reason(self.web3, tx_hash, receipt)
            raise TransactionError(
                "Transaction failed",
                tx_hash=tx_hash.hex(),
                revert_reason=reason,
            )
        return receipt

    def get_logs(
        self,
        contract_address: ChecksumAddress,
        topics: list[str],
        from_block: BlockIdentifier = 0,
        to_block: BlockIdentifier = "latest",
    ) -> list[LogReceipt]:
        filter_params: FilterParams = {
            "fromBlock": from_block,
            "toBlock": to_block,
            "address": contract_address,
            "topics": topics,  # type: ignore[typeddict-item]
        }
        return self.web3.eth.get_logs(filter_params)

    def get_block(self, block: BlockIdentifier = "latest"):
        return self.web3.eth.get_block(block)

    def _estimate_gas(self, to: ChecksumAddress, data: str, from_address: str) -> int:
        estimated = self.web3.eth.estimate_gas(
            {"to": to, "from": from_address, "data": data}  # type: ignore[typeddict-item]
        )
        return int(self._gas_multiplier * estimated)

    def _calculate_max_fee_per_gas(self, gas_price: int) -> int:
        return gas_price + self._percent_of(gas_price, self.GAS_PRICE_MARGIN)

    def _get_max_priority_fee(self, gas_price: int) -> int:
        return min(self.DEFAULT_TRANSACTION_MAX_PRIORITY_FEE, gas_price // 10)

    @staticmethod
    def _percent_of(value: int, percentage: int) -> int:
        return value * percentage // 100

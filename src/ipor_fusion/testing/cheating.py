from __future__ import annotations

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.core.context import Web3Context
from ipor_fusion.errors import TransactionError, _get_revert_reason


class ForkedWeb3Context(Web3Context):
    def __init__(
        self,
        web3: Web3,
        chain_id: int,
        impersonated_address: ChecksumAddress | None = None,
        gas_multiplier: float = 1.25,
    ):
        super().__init__(
            web3=web3,
            chain_id=chain_id,
            signer=impersonated_address,
            gas_multiplier=gas_multiplier,
        )

    @classmethod
    def from_url(  # type: ignore[override]
        cls,
        url: str,
        private_key: str | None = None,
        gas_multiplier: float = 1.25,
        impersonate: ChecksumAddress | None = None,
    ) -> ForkedWeb3Context:
        web3 = Web3(Web3.HTTPProvider(url))
        chain_id = web3.eth.chain_id
        return cls(
            web3=web3,
            chain_id=chain_id,
            impersonated_address=impersonate,
            gas_multiplier=gas_multiplier,
        )

    def prank(self, address: ChecksumAddress):
        self._signer = Web3.to_checksum_address(address)

    def send(self, to: ChecksumAddress, data: bytes) -> TxReceipt:
        if not self.signer:
            raise ValueError("No impersonated address set. Use prank() first.")

        nonce = self.web3.eth.get_transaction_count(self.signer)
        gas_price = self.web3.eth.gas_price
        max_fee_per_gas = self._calculate_max_fee_per_gas(gas_price)
        max_priority_fee_per_gas = self._get_max_priority_fee(gas_price)

        data_hex = f"0x{data.hex()}"
        estimated_gas = self._estimate_gas(to, data_hex, self.signer)

        transaction = {
            "chainId": self.chain_id,
            "gas": estimated_gas,
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": max_priority_fee_per_gas,
            "to": to,
            "from": self.signer,
            "nonce": nonce,
            "data": data_hex,
        }

        tx_hash = self.web3.eth.send_transaction(transaction)  # type: ignore[arg-type]
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        if receipt["status"] != 1:
            reason = _get_revert_reason(self.web3, tx_hash, receipt)
            raise TransactionError(
                "Transaction failed",
                tx_hash=tx_hash.hex(),
                revert_reason=reason,
            )
        return receipt

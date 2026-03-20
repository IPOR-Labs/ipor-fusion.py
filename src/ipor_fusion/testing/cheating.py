from __future__ import annotations

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.core.context import Web3Context
from ipor_fusion.types import ChainId


class ForkedWeb3Context(Web3Context):
    def __init__(
        self,
        web3: Web3,
        chain_id: ChainId,
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
        chain_id = ChainId(web3.eth.chain_id)
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
        transaction = self._build_transaction(to, data)
        tx_hash = self.web3.eth.send_transaction(transaction)  # type: ignore[arg-type]
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        return self._handle_receipt(tx_hash, receipt)

from __future__ import annotations

from eth_account import Account
from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.types import BlockIdentifier, FilterParams, LogReceipt, TxReceipt

from ipor_fusion.errors import TransactionError, get_revert_reason
from ipor_fusion.types import ChainId


class Web3Context:
    """Manages Web3 connection, signing, and transaction dispatch."""

    DEFAULT_TRANSACTION_MAX_PRIORITY_FEE = 2_000_000_000
    GAS_PRICE_MARGIN = 25
    # maxFeePerGas ceiling as a multiple of the current base fee. EIP-1559
    # charges min(maxFeePerGas, baseFee + priority), so a high ceiling is free
    # insurance against the base fee rising between this read and the broadcast
    # (Arbitrum's base fee moves every block); too tight and the node rejects
    # the tx with "max fee per gas less than block base fee".
    BASE_FEE_HEADROOM_MULTIPLIER = 2
    # web3's own HTTP default, made explicit so callers can tighten it: against
    # a degraded RPC every request otherwise blocks for the full 30s, and a
    # multi-call read (vault fetch, health check) fans that out into minutes of
    # wall clock. Long-running services should pass something tighter.
    DEFAULT_RPC_TIMEOUT_S = 30.0

    def __init__(
        self,
        web3: Web3,
        chain_id: ChainId,
        signer: ChecksumAddress | None = None,
        private_key: str | None = None,
        gas_multiplier: float = 1.25,
    ):
        self._web3 = web3
        self._chain_id = chain_id
        self._private_key = private_key
        self._gas_multiplier = gas_multiplier
        self._default_block: BlockIdentifier = "latest"
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
    def chain_id(self) -> ChainId:
        return self._chain_id

    @property
    def default_block(self) -> BlockIdentifier:
        return self._default_block

    @default_block.setter
    def default_block(self, value: BlockIdentifier) -> None:
        self._default_block = value

    @property
    def signer(self) -> ChecksumAddress | None:
        return self._signer

    @classmethod
    def from_url(
        cls,
        url: str,
        private_key: str | None = None,
        gas_multiplier: float = 1.25,
        request_timeout_s: float = DEFAULT_RPC_TIMEOUT_S,
    ) -> Web3Context:
        web3 = Web3(
            Web3.HTTPProvider(url, request_kwargs={"timeout": request_timeout_s})
        )
        chain_id = ChainId(web3.eth.chain_id)

        return cls(
            web3=web3,
            chain_id=chain_id,
            private_key=private_key,
            gas_multiplier=gas_multiplier,
        )

    def call(
        self,
        to: ChecksumAddress,
        data: bytes,
        block: BlockIdentifier | None = None,
    ) -> HexBytes:
        return self.web3.eth.call(
            {"to": to, "data": data}, block_identifier=self._resolve_block(block)
        )

    def _resolve_block(self, block: BlockIdentifier | None) -> BlockIdentifier:
        return block if block is not None else self._default_block

    def build_transaction(self, to: ChecksumAddress, data: bytes) -> dict:
        """Build the transaction dict for ``data`` against ``to`` without signing
        or broadcasting — the same dict `send` would submit, ready to hand to an
        external signer or to inspect for a gas/cost preview (`gas` and
        `maxFeePerGas` are both present).

        Needs a signer *address* (for the nonce read and gas estimation) but no
        private key; raises ``ValueError`` if no signer is configured. Because
        gas estimation executes the call, a would-revert transaction surfaces
        as the underlying revert rather than returning a dict. To observe
        *state changes* instead of cost, use `VaultSimulator`
        (`core/simulation.py`, `eth_simulateV1`) — it answers "what happens",
        this answers "what it costs / here's the transaction to sign" on any RPC.
        """
        signer = self._require_signer("build a transaction")
        nonce = self.web3.eth.get_transaction_count(signer)
        gas_price = self.web3.eth.gas_price
        max_priority_fee_per_gas = self._get_max_priority_fee(gas_price)
        base_fee = self.get_block().get("baseFeePerGas")
        max_fee_per_gas = self._calculate_max_fee_per_gas(
            gas_price, base_fee, max_priority_fee_per_gas
        )
        data_hex = self._data_hex(data)
        estimated_gas = self._estimate_gas(to, data_hex, signer)
        return {
            "chainId": self.chain_id,
            "gas": estimated_gas,
            "maxFeePerGas": max_fee_per_gas,
            "maxPriorityFeePerGas": max_priority_fee_per_gas,
            "to": to,
            "from": signer,
            "nonce": nonce,
            "data": data_hex,
        }

    def _handle_receipt(self, tx_hash, receipt) -> TxReceipt:
        if receipt["status"] != 1:
            reason = get_revert_reason(self.web3, tx_hash, receipt)
            raise TransactionError(
                "Transaction failed",
                tx_hash=tx_hash.hex(),
                revert_reason=reason,
            )
        return receipt

    def send(self, to: ChecksumAddress, data: bytes) -> TxReceipt:
        if not self._private_key or not self._signer:
            raise ValueError("Private key required for sending transactions")
        transaction = self.build_transaction(to, data)
        signed_tx = self.web3.eth.account.sign_transaction(
            transaction, self._private_key
        )
        tx_hash = self.web3.eth.send_raw_transaction(signed_tx.raw_transaction)
        receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        return self._handle_receipt(tx_hash, receipt)

    def estimate_gas(self, to: ChecksumAddress, data: bytes) -> int:
        """Estimate gas for ``data`` against ``to`` (gas-multiplier applied, as
        in `send`) without signing or broadcasting.

        Like `build_transaction`, needs a signer address but no private key, and
        raises ``ValueError`` if no signer is configured. A would-revert
        transaction raises the underlying revert rather than returning a number
        — the cheapest dry-run there is.
        """
        signer = self._require_signer("estimate gas")
        return self._estimate_gas(to, self._data_hex(data), signer)

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

    def get_storage_at(
        self,
        address: ChecksumAddress,
        slot: int,
        block: BlockIdentifier | None = None,
    ) -> HexBytes:
        return self.web3.eth.get_storage_at(
            address, slot, block_identifier=self._resolve_block(block)
        )

    def _estimate_gas(self, to: ChecksumAddress, data: str, from_address: str) -> int:
        estimated = self.web3.eth.estimate_gas(
            {"to": to, "from": from_address, "data": data}  # type: ignore[typeddict-item]
        )
        # State can shift between estimate and execution, so pad the limit; it is
        # only a ceiling, and unused gas is not charged.
        return int(self._gas_multiplier * estimated)

    def _calculate_max_fee_per_gas(
        self, gas_price: int, base_fee: int | None, max_priority_fee_per_gas: int
    ) -> int:
        # Prefer a base-fee-relative ceiling on EIP-1559 chains; fall back to a
        # gas-price margin where the block carries no base fee.
        if base_fee is None:
            return gas_price + self._percent_of(gas_price, self.GAS_PRICE_MARGIN)
        return base_fee * self.BASE_FEE_HEADROOM_MULTIPLIER + max_priority_fee_per_gas

    def _get_max_priority_fee(self, gas_price: int) -> int:
        return min(self.DEFAULT_TRANSACTION_MAX_PRIORITY_FEE, gas_price // 10)

    def _require_signer(self, action: str) -> ChecksumAddress:
        if self.signer is None:
            raise ValueError(f"Signer required to {action}")
        return self.signer

    @staticmethod
    def _data_hex(data: bytes) -> str:
        return f"0x{data.hex()}"

    @staticmethod
    def _percent_of(value: int, percentage: int) -> int:
        return value * percentage // 100

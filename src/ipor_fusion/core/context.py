from __future__ import annotations

import copy
import logging
import threading
import time

from eth_account import Account
from eth_typing import ChecksumAddress, HexStr
from eth_utils import encode_hex
from hexbytes import HexBytes
from requests import ConnectionError as RequestsConnectionError
from requests import ConnectTimeout, HTTPError, Timeout
from urllib3.exceptions import MaxRetryError, NewConnectionError
from web3 import Web3
from web3.exceptions import ContractLogicError, TimeExhausted, Web3RPCError
from web3.middleware import ExtraDataToPOAMiddleware, Web3Middleware
from web3.providers.rpc.utils import (
    REQUEST_RETRY_ALLOWLIST,
    ExceptionRetryConfiguration,
    check_if_retry_on_failure,
)
from web3.types import (
    BlockIdentifier,
    LogReceipt,
    MakeBatchRequestFn,
    MakeRequestFn,
    RPCEndpoint,
    RPCResponse,
    TxParams,
    TxReceipt,
    Wei,
)

from ipor_fusion.cache import ContractCache, default_contract_cache
from ipor_fusion.chains import GET_LOGS_RANGE_HINTS
from ipor_fusion.core.logs import (
    DEFAULT_LOG_SCAN_MAX_REQUESTS,
    DEFAULT_LOG_SCAN_TIMEOUT_S,
    LogSource,
    block_number,
    get_logs_adaptive,
)
from ipor_fusion.errors import (
    LogSourceUnavailable,
    TransactionError,
    TransactionOutcomeUnknown,
    TransactionTimeout,
    get_revert_reason,
)
from ipor_fusion.types import ChainId, TxHash, parse_tx_hash

# web3 retries every allowlisted method on any HTTP error, a 400 included.
#
# eth_getLogs: a too-wide query answered with HTTP 400/413 or a query timeout
# would be sent five times before the adaptive scan could shrink it.
#
# eth_sendRawTransaction: a post whose answer timed out may already be in the
# mempool. Its repeat fails with "already known" or "nonce too low", which
# would make send raise for a transaction that lands.
_NOT_RETRIED = frozenset({"eth_getLogs", "eth_sendRawTransaction"})
_RETRY_POLICY = ExceptionRetryConfiguration(
    errors=(RequestsConnectionError, HTTPError, Timeout),
    method_allowlist=[m for m in REQUEST_RETRY_ALLOWLIST if m not in _NOT_RETRIED],
)

_SEND_CONNECT_ATTEMPTS = 3
_SEND_CONNECT_BACKOFF_S = 0.25
_OUTCOME_UNKNOWN = "Transaction outcome unknown; look up tx_hash before sending again"


def _provider_retries_post(provider: object) -> bool:
    """Whether the provider repeats ``eth_sendRawTransaction`` itself.

    web3 then raises the error of its last attempt only, which says nothing
    about the attempts before it.
    """
    config = getattr(provider, "exception_retry_configuration", None)
    return isinstance(config, ExceptionRetryConfiguration) and (
        check_if_retry_on_failure(
            RPCEndpoint("eth_sendRawTransaction"), config.method_allowlist
        )
    )


def _no_connection_made(exc: Exception) -> bool:
    """Whether a failed request provably sent nothing: no connection was made.

    requests raises ``ConnectTimeout`` for a connect timeout, but a
    ``ConnectionError`` around urllib3's ``NewConnectionError`` for a refused
    connection or a failed DNS lookup. A reset may come after the node read
    the body, so it does not count.
    """
    if isinstance(exc, ConnectTimeout):
        return True
    if not isinstance(exc, RequestsConnectionError) or not exc.args:
        return False
    cause = exc.args[0]
    return isinstance(cause, MaxRetryError) and isinstance(
        cause.reason, NewConnectionError
    )


_logger = logging.getLogger(__name__)

# Some revm/reth-based nodes report a revert without return data as
# {"code": -32603, "message": "Failed to call: InvalidTransaction(Revert(
# RevertError { output: None }))"}; web3 recognizes a revert only by code 3 or
# "execution reverted" in the message, so it raises a generic Web3RPCError and
# getter probes that expect ContractLogicError fail.
_UNRECOGNIZED_REVERT_MARKER = "InvalidTransaction(Revert(RevertError"


def _is_unrecognized_revert(exc: Web3RPCError) -> bool:
    error = (exc.rpc_response or {}).get("error")
    message = error.get("message") if isinstance(error, dict) else None
    return isinstance(message, str) and _UNRECOGNIZED_REVERT_MARKER in message


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
    # RPC requests in flight at once through a `from_url` context. The vault
    # reads fan out on nested thread pools; this bounds what reaches the
    # provider, whatever the nesting, so a rate-limited endpoint sees a steady
    # stream instead of bursts.
    DEFAULT_MAX_CONCURRENT_REQUESTS = 8

    def __init__(
        self,
        web3: Web3,
        chain_id: ChainId,
        *,
        signer: ChecksumAddress | None = None,
        private_key: str | None = None,
        gas_multiplier: float = 1.25,
        log_scan_timeout_s: float = DEFAULT_LOG_SCAN_TIMEOUT_S,
        log_scan_max_requests: int | None = DEFAULT_LOG_SCAN_MAX_REQUESTS,
        log_source: LogSource | None = None,
        cache: ContractCache | None = None,
    ):
        # EIP-3668: web3 would otherwise follow a contract's OffchainLookup
        # revert from inside eth.call, sending an HTTP request to a URL chosen
        # by whoever deployed the contract. Nothing in the SDK reads through it.
        web3.provider.global_ccip_read_enabled = False
        self._web3 = web3
        self._chain_id = chain_id
        # Bounds of every `get_logs` scan: wall clock, and eth_getLogs requests
        # (None: unbounded); see `core.logs.get_logs_adaptive`.
        self.log_scan_timeout_s = log_scan_timeout_s
        self.log_scan_max_requests = log_scan_max_requests
        # Asked first by `get_logs`; None scans the RPC only.
        self.log_source = log_source
        # None: the process-wide file cache (`cache.default_contract_cache`).
        self._cache = cache
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
    def cache(self) -> ContractCache:
        """Where the SDK keeps immutable contract facts read through this
        context (token symbols and decimals, contract names, deployment
        blocks): the ``cache=`` passed in, else the file cache under
        ``$XDG_CACHE_HOME/ipor-fusion/``. Pass `NullCache()` to keep nothing."""
        return self._cache if self._cache is not None else default_contract_cache()

    @property
    def default_block(self) -> BlockIdentifier:
        """The block reads default to: ``"latest"`` unless this context came
        from `at_block`. Read-only; pin a copy with `at_block` instead."""
        return self._default_block

    def at_block(self, block: BlockIdentifier) -> Web3Context:
        """A copy of this context whose reads default to ``block``: same
        connection, signer, log source, cache and scan bounds. Pinning a copy leaves
        this context (and anything sharing it across threads) untouched."""
        pinned = copy.copy(self)
        pinned._default_block = block
        return pinned

    @property
    def signer(self) -> ChecksumAddress | None:
        return self._signer

    @classmethod
    def from_url(
        cls,
        url: str,
        *,
        private_key: str | None = None,
        gas_multiplier: float = 1.25,
        request_timeout_s: float = DEFAULT_RPC_TIMEOUT_S,
        log_scan_timeout_s: float = DEFAULT_LOG_SCAN_TIMEOUT_S,
        log_scan_max_requests: int | None = DEFAULT_LOG_SCAN_MAX_REQUESTS,
        log_source: LogSource | None = None,
        cache: ContractCache | None = None,
        max_concurrent_requests: int
        | InFlightLimit
        | None = DEFAULT_MAX_CONCURRENT_REQUESTS,
    ) -> Web3Context:
        """A context on an HTTP provider at ``url``. ``max_concurrent_requests``
        bounds the RPC requests in flight: an ``int`` across every thread using
        this context, an `InFlightLimit` across every context and `Web3` it is
        installed on (share one per provider in a service that builds a context
        per request); ``None`` leaves them unbounded."""
        web3 = Web3(
            Web3.HTTPProvider(
                url,
                request_kwargs={"timeout": request_timeout_s},
                exception_retry_configuration=_RETRY_POLICY,
            )
        )
        # web3's validation middleware fetches eth_chainId (uncached) on the
        # request AND the response of every eth_call/eth_estimateGas, tripling
        # round trips, only to check a tx "chainId" field. Reads never carry
        # one, and `build_transaction` sets it from the chain id read below.
        web3.middleware_onion.remove("validation")
        # Some chains put more than 32 bytes in a block's extraData (Avalanche:
        # 66 bytes in ~1 block of 140), which web3's block formatter rejects
        # with a ValueError. The middleware moves it to proofOfAuthorityData
        # and leaves every other block untouched.
        web3.middleware_onion.inject(ExtraDataToPOAMiddleware, layer=0)
        if isinstance(max_concurrent_requests, int):
            max_concurrent_requests = InFlightLimit(max_concurrent_requests)
        if max_concurrent_requests is not None:
            # Injected last, so innermost (see `InFlightLimit.install`).
            max_concurrent_requests.install(web3)
        chain_id = ChainId(web3.eth.chain_id)

        return cls(
            web3=web3,
            chain_id=chain_id,
            private_key=private_key,
            gas_multiplier=gas_multiplier,
            log_scan_timeout_s=log_scan_timeout_s,
            log_scan_max_requests=log_scan_max_requests,
            log_source=log_source,
            cache=cache,
        )

    def call(
        self,
        to: ChecksumAddress,
        data: bytes,
        block: BlockIdentifier | None = None,
    ) -> HexBytes:
        try:
            return self.web3.eth.call(
                {"to": to, "data": data}, block_identifier=self._resolve_block(block)
            )
        except Web3RPCError as exc:
            if _is_unrecognized_revert(exc):
                raise ContractLogicError("execution reverted", data=None) from exc
            raise

    def _resolve_block(self, block: BlockIdentifier | None) -> BlockIdentifier:
        return block if block is not None else self._default_block

    def build_transaction(
        self,
        to: ChecksumAddress,
        data: bytes,
        *,
        block: BlockIdentifier = "latest",
    ) -> TxParams:
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

        ``block`` pins the nonce, base fee and gas estimate to one block, so a
        preview is reproducible and matches a simulation pinned to the same
        parent. It is ``"latest"`` by default, not `default_block`: a dict
        built for signing must reflect the chain head. The ``eth_gasPrice``
        read that caps the priority fee has no block parameter and stays
        current.
        """
        signer = self._require_signer("build a transaction")
        nonce = self.web3.eth.get_transaction_count(signer, block_identifier=block)
        gas_price = self.web3.eth.gas_price
        max_priority_fee_per_gas = self._get_max_priority_fee(gas_price)
        base_fee = self.get_block(block).get("baseFeePerGas")
        max_fee_per_gas = self._calculate_max_fee_per_gas(
            gas_price, base_fee, max_priority_fee_per_gas
        )
        data_hex = encode_hex(data)
        estimated_gas = self._estimate_gas(to, data_hex, signer, block)
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

    def _handle_receipt(self, tx_hash: TxHash, receipt: TxReceipt) -> TxReceipt:
        if receipt["status"] != 1:
            raise TransactionError(
                "Transaction failed",
                tx_hash=tx_hash,
                revert_reason=get_revert_reason(self.web3, tx_hash, receipt),
            )
        return receipt

    def send(self, to: ChecksumAddress, data: bytes) -> TxReceipt:
        """Sign the `build_transaction` dict, post it, and wait for the
        receipt; a reverted transaction raises `TransactionError`.

        A node that rejects the transaction raises web3's ``Web3RPCError``. A
        receipt wait that times out raises `TransactionTimeout`, and any other
        failure of the post or the receipt wait `TransactionOutcomeUnknown`:
        the transaction may still land, so look up their ``tx_hash`` or the
        signer's nonce before sending again.

        On a provider that posts once, as a `from_url` context's does, a post
        that never reached a node (a connect timeout, a refused connection) is
        repeated a few times, then its transport error is raised. A provider
        that repeats the post itself, as web3's default ``HTTPProvider`` does,
        reports only its last attempt's error, so every failed post there is
        `TransactionOutcomeUnknown`.
        """
        if not self._private_key or not self._signer:
            raise ValueError("Private key required for sending transactions")
        transaction = self.build_transaction(to, data)
        signed_tx = self.web3.eth.account.sign_transaction(
            transaction, self._private_key
        )
        tx_hash = parse_tx_hash(signed_tx.hash)
        self._post_raw_transaction(signed_tx.raw_transaction, tx_hash)
        try:
            receipt = self.web3.eth.wait_for_transaction_receipt(tx_hash)
        except TimeExhausted as exc:
            raise TransactionTimeout(_OUTCOME_UNKNOWN, tx_hash) from exc
        except Exception as exc:
            raise TransactionOutcomeUnknown(_OUTCOME_UNKNOWN, tx_hash) from exc
        return self._handle_receipt(tx_hash, receipt)

    def _post_raw_transaction(self, raw: bytes, tx_hash: TxHash) -> None:
        posts_once = not _provider_retries_post(self.web3.provider)
        for attempt in range(_SEND_CONNECT_ATTEMPTS):
            try:
                self.web3.eth.send_raw_transaction(raw)
                return
            except Web3RPCError:
                raise
            except Exception as exc:
                if not (posts_once and _no_connection_made(exc)):
                    raise TransactionOutcomeUnknown(_OUTCOME_UNKNOWN, tx_hash) from exc
                if attempt == _SEND_CONNECT_ATTEMPTS - 1:
                    raise
                time.sleep(_SEND_CONNECT_BACKOFF_S * 2**attempt)

    def estimate_gas(
        self,
        to: ChecksumAddress,
        data: bytes,
        *,
        block: BlockIdentifier = "latest",
    ) -> int:
        """Estimate gas for ``data`` against ``to`` (gas-multiplier applied, as
        in `send`) without signing or broadcasting.

        Like `build_transaction`, needs a signer address but no private key, and
        raises ``ValueError`` if no signer is configured. A would-revert
        transaction raises the underlying revert rather than returning a number
        — the cheapest dry-run there is. ``block`` pins the estimate as in
        `build_transaction`.
        """
        signer = self._require_signer("estimate gas")
        return self._estimate_gas(to, encode_hex(data), signer, block)

    def get_logs(
        self,
        contract_address: ChecksumAddress,
        # Per position: one topic, or a list of alternatives (OR).
        topics: list[str | list[str]],
        from_block: BlockIdentifier | None = None,
        to_block: BlockIdentifier | None = None,
    ) -> list[LogReceipt]:
        """Every matching log in the range, paged within the provider's
        `eth_getLogs` caps (`core.logs.get_logs_adaptive`); raises
        `LogScanError` rather than return a partial result. ``from_block=None``
        scans from the contract's creation; ``to_block=None`` ends at the
        context's default block, so a pinned context never sees later logs.

        With a `log_source` set, a filter on the event signature alone is
        asked there first; when it raises `LogSourceUnavailable` the RPC
        scan runs as without one."""
        end = block_number(self.web3, self._resolve_block(to_block))
        if self.log_source is not None and len(topics) == 1:
            topic0 = [topics[0]] if isinstance(topics[0], str) else list(topics[0])
            start = 0 if from_block is None else block_number(self.web3, from_block)
            try:
                return self.log_source.get_logs(
                    self.chain_id, contract_address, topic0, start, end
                )
            except LogSourceUnavailable as exc:
                _logger.warning(
                    "log source unavailable for %s on chain %s, scanning the RPC: %s",
                    contract_address,
                    self.chain_id,
                    exc,
                )
        return get_logs_adaptive(
            self.web3,
            contract_address,
            topics,
            from_block,
            end,
            chain_id=self.chain_id,
            chunk_hint=GET_LOGS_RANGE_HINTS.get(self.chain_id),
            timeout_s=self.log_scan_timeout_s,
            max_requests=self.log_scan_max_requests,
        )

    def get_block(self, block: BlockIdentifier | None = None):
        """The block at ``block``, the context's default block when ``None``."""
        return self.web3.eth.get_block(self._resolve_block(block))

    def get_storage_at(
        self,
        address: ChecksumAddress,
        slot: int,
        block: BlockIdentifier | None = None,
    ) -> HexBytes:
        return self.web3.eth.get_storage_at(
            address, slot, block_identifier=self._resolve_block(block)
        )

    def _estimate_gas(
        self,
        to: ChecksumAddress,
        data: HexStr,
        from_address: ChecksumAddress,
        block: BlockIdentifier = "latest",
    ) -> int:
        estimated = self.web3.eth.estimate_gas(
            {"to": to, "from": from_address, "data": data},
            block_identifier=block,
        )
        # State can shift between estimate and execution, so pad the limit; it is
        # only a ceiling, and unused gas is not charged.
        return int(self._gas_multiplier * estimated)

    def _calculate_max_fee_per_gas(
        self, gas_price: int, base_fee: int | None, max_priority_fee_per_gas: int
    ) -> Wei:
        # Prefer a base-fee-relative ceiling on EIP-1559 chains; fall back to a
        # gas-price margin where the block carries no base fee.
        if base_fee is None:
            return Wei(gas_price + self._percent_of(gas_price, self.GAS_PRICE_MARGIN))
        return Wei(
            base_fee * self.BASE_FEE_HEADROOM_MULTIPLIER + max_priority_fee_per_gas
        )

    def _get_max_priority_fee(self, gas_price: int) -> Wei:
        return Wei(min(self.DEFAULT_TRANSACTION_MAX_PRIORITY_FEE, gas_price // 10))

    def _require_signer(self, action: str) -> ChecksumAddress:
        if self.signer is None:
            raise ValueError(f"Signer required to {action}")
        return self.signer

    @staticmethod
    def _percent_of(value: int, percentage: int) -> int:
        return value * percentage // 100


class InFlightLimit:
    """A bound on the RPC requests in flight, shareable across `Web3` objects.

    Every `Web3` it is installed on (``install``, or
    ``Web3Context.from_url(max_concurrent_requests=limit)``) takes a slot from
    the same semaphore for each provider call, so several contexts on one
    provider, or a service that builds a context per request, stay within
    ``limit`` in total. A JSON-RPC batch is one HTTP request and takes one slot
    whatever its item count; a failed request releases its slot.
    """

    def __init__(self, limit: int):
        if limit < 1:
            raise ValueError(f"an in-flight limit must be at least 1, got {limit}")
        self._limit = limit
        self._middleware = _slot_middleware(threading.BoundedSemaphore(limit))

    @property
    def limit(self) -> int:
        return self._limit

    @property
    def middleware(self) -> type[Web3Middleware]:
        """The middleware class to add to a `Web3` by hand; every instance
        web3 builds from it shares this limit's slots."""
        return self._middleware

    def install(self, web3: Web3) -> None:
        """Add the limit to ``web3`` as its innermost middleware
        (``in_flight_limit``). A slot then covers only the provider call, so a
        middleware that issues its own request (the gas price strategy reads a
        block) never waits on the slot it already holds."""
        web3.middleware_onion.inject(self._middleware, name="in_flight_limit", layer=0)


def _slot_middleware(slots: threading.BoundedSemaphore) -> type[Web3Middleware]:
    class InFlightLimitMiddleware(Web3Middleware):
        def wrap_make_request(self, make_request: MakeRequestFn) -> MakeRequestFn:
            def limited(method: RPCEndpoint, params: object) -> RPCResponse:
                with slots:
                    return make_request(method, params)

            return limited

        def wrap_make_batch_request(
            self, make_batch_request: MakeBatchRequestFn
        ) -> MakeBatchRequestFn:
            def limited(
                requests_info: list[tuple[RPCEndpoint, object]],
            ) -> list[RPCResponse] | RPCResponse:
                with slots:
                    return make_batch_request(requests_info)

            return limited

    return InFlightLimitMiddleware

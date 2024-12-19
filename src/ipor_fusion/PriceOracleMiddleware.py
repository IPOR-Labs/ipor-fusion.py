from eth_abi import encode, decode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.TransactionExecutor import TransactionExecutor


class PriceOracleMiddleware:

    def __init__(
        self,
        transaction_executor: TransactionExecutor,
        price_oracle_middleware_address: str,
    ):
        self._transaction_executor = transaction_executor
        self._price_oracle_middleware_address = price_oracle_middleware_address

    def address(self) -> str:
        return self._price_oracle_middleware_address

    def get_source_of_asset_price(self, asset: str) -> str:
        signature = function_signature_to_4byte_selector(
            "getSourceOfAssetPrice(address)"
        )
        read = self._transaction_executor.read(
            self._price_oracle_middleware_address,
            signature + encode(["address"], [asset]),
        )
        (source_of_asset_price,) = decode(["address"], read)
        return source_of_asset_price

    def CHAINLINK_FEED_REGISTRY(self) -> str:
        signature = function_signature_to_4byte_selector("CHAINLINK_FEED_REGISTRY()")
        read = self._transaction_executor.read(
            self._price_oracle_middleware_address, signature
        )
        (chainlink_feed_registry,) = decode(["address"], read)
        return chainlink_feed_registry

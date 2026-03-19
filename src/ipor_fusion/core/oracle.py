from dataclasses import dataclass
from eth_abi import encode, decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.types import LogReceipt

from ipor_fusion.core.context import Web3Context
from ipor_fusion.types import Price


@dataclass
class AssetPriceSource:
    asset: ChecksumAddress
    source: ChecksumAddress


class PriceOracleMiddleware:

    def __init__(self, ctx: Web3Context, address: ChecksumAddress):
        self._ctx = ctx
        self._address = Web3.to_checksum_address(address)

    @property
    def address(self) -> ChecksumAddress:
        return self._address

    def get_source_of_asset_price(self, asset: ChecksumAddress) -> ChecksumAddress:
        sig = function_signature_to_4byte_selector("getSourceOfAssetPrice(address)")
        result = self._ctx.call(self._address, sig + encode(["address"], [asset]))
        (value,) = decode(["address"], result)
        return Web3.to_checksum_address(value)

    def chainlink_feed_registry(self) -> ChecksumAddress:
        sig = function_signature_to_4byte_selector("CHAINLINK_FEED_REGISTRY()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["address"], result)
        return Web3.to_checksum_address(value)

    def get_assets_price_sources(self) -> list[AssetPriceSource]:
        events = self._get_asset_price_source_updated_events()
        sources = []
        for event in events:
            (asset, source) = decode(["address", "address"], event["data"])
            sources.append(
                AssetPriceSource(
                    asset=Web3.to_checksum_address(asset),
                    source=Web3.to_checksum_address(source),
                )
            )
        return sources

    def get_asset_price(self, asset_address: ChecksumAddress) -> Price:
        sig = function_signature_to_4byte_selector("getAssetPrice(address)")
        result = self._ctx.call(
            self._address, sig + encode(["address"], [asset_address])
        )
        (amount, decimals) = decode(["uint256", "uint256"], result)
        return Price(asset=asset_address, amount=amount, decimals=decimals)

    def _get_asset_price_source_updated_events(self) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="AssetPriceSourceUpdated(address,address)")
        ).to_0x_hex()
        return list(
            self._ctx.get_logs(
                contract_address=self._address, topics=[event_signature_hash]
            )
        )

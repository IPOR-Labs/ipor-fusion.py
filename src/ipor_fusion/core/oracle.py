from dataclasses import dataclass
from eth_abi import decode
from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.types import LogReceipt

from ipor_fusion.core.contract import ContractWrapper
from ipor_fusion.types import Price


@dataclass(slots=True)
class AssetPriceSource:
    asset: ChecksumAddress
    source: ChecksumAddress


class PriceOracleMiddleware(ContractWrapper):
    """Middleware for querying on-chain asset prices."""

    def get_source_of_asset_price(self, asset: ChecksumAddress) -> ChecksumAddress:
        (value,) = decode(
            ["address"], self._call("getSourceOfAssetPrice(address)", asset)
        )
        return Web3.to_checksum_address(value)

    def chainlink_feed_registry(self) -> ChecksumAddress:
        (value,) = decode(["address"], self._call("CHAINLINK_FEED_REGISTRY()"))
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
        result = self._call("getAssetPrice(address)", asset_address)
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

from dataclasses import dataclass
from functools import partial

from eth_abi import decode
from eth_typing import ChecksumAddress
from hexbytes import HexBytes
from web3 import Web3
from web3.types import LogReceipt

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.types import Price


@dataclass(slots=True)
class AssetPriceSource:
    asset: ChecksumAddress
    source: ChecksumAddress


def _price_decoder(asset: ChecksumAddress, value: tuple) -> Price:
    amount, decimals = value
    return Price(asset=asset, amount=amount, decimals=decimals)


class PriceOracleMiddleware(ContractWrapper):
    """Middleware for querying on-chain asset prices."""

    def get_source_of_asset_price(
        self, asset: ChecksumAddress
    ) -> Call[ChecksumAddress]:
        return self._view(
            "getSourceOfAssetPrice(address)",
            asset,
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def chainlink_feed_registry(self) -> Call[ChecksumAddress]:
        return self._view(
            "CHAINLINK_FEED_REGISTRY()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def get_asset_price(self, asset_address: ChecksumAddress) -> Call[Price]:
        return self._view(
            "getAssetPrice(address)",
            asset_address,
            output_types=["uint256", "uint256"],
            decoder=partial(_price_decoder, asset_address),
        )

    # ── Compound method: event replay ──────────────────────────────────────

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

    def _get_asset_price_source_updated_events(self) -> list[LogReceipt]:
        event_signature_hash = HexBytes(
            Web3.keccak(text="AssetPriceSourceUpdated(address,address)")
        ).to_0x_hex()
        return list(
            self._ctx.get_logs(
                contract_address=self._address, topics=[event_signature_hash]
            )
        )


class PriceOracleMiddlewareManager(ContractWrapper):
    """Per-vault price-source overrides (`PriceOracleMiddlewareManager`).

    A zero source for an asset is not a missing price: it means "no override",
    so the read falls through to the global `PriceOracleMiddleware` this
    manager points at. Deliberately a sibling of `PriceOracleMiddleware` rather
    than a subclass — the two contracts share read signatures but emit
    different events (`AssetPriceSourceAdded`/`Removed` here), and the manager
    has no feed registry. Enumerate its overrides with `get_configured_assets`
    plus `get_source_of_asset_price`.
    """

    def set_assets_price_sources(
        self, assets: list[ChecksumAddress], sources: list[ChecksumAddress]
    ) -> Call[None]:
        """PRICE_ORACLE_MIDDLEWARE_MANAGER-only: point each asset at its price
        source. `sources[i]` overrides `assets[i]`; the contract rejects empty
        and length-mismatched arrays."""
        return self._write(
            "setAssetsPriceSources(address[],address[])", list(assets), list(sources)
        )

    def remove_assets_price_sources(self, assets: list[ChecksumAddress]) -> Call[None]:
        """PRICE_ORACLE_MIDDLEWARE_MANAGER-only inverse of
        `set_assets_price_sources`: drops the override, so the assets fall back
        to the global middleware."""
        return self._write("removeAssetsPriceSources(address[])", list(assets))

    def get_source_of_asset_price(
        self, asset: ChecksumAddress
    ) -> Call[ChecksumAddress]:
        """The override source, or the zero address when there is none."""
        return self._view(
            "getSourceOfAssetPrice(address)",
            asset,
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def get_asset_price(self, asset_address: ChecksumAddress) -> Call[Price]:
        """Effective price, normalized to WAD regardless of what the feed reports."""
        return self._view(
            "getAssetPrice(address)",
            asset_address,
            output_types=["uint256", "uint256"],
            decoder=partial(_price_decoder, asset_address),
        )

    def get_configured_assets(self) -> Call[list[ChecksumAddress]]:
        """Assets carrying an override on this manager."""
        return self._view(
            "getConfiguredAssets()",
            output_types=["address[]"],
            decoder=lambda lst: [Web3.to_checksum_address(a) for a in lst],
        )

    def get_price_oracle_middleware(self) -> Call[ChecksumAddress]:
        """The global middleware that zero-source assets delegate to."""
        return self._view(
            "getPriceOracleMiddleware()",
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

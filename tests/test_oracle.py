"""Unit tests for PriceOracleMiddleware — mock _call() and _ctx, verify decoding."""

from unittest.mock import MagicMock

from eth_abi import encode
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion.core.oracle import AssetPriceSource, PriceOracleMiddleware
from ipor_fusion.types import Price

CONTRACT_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
ASSET_ADDR = Web3.to_checksum_address("0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa")
SOURCE_ADDR = Web3.to_checksum_address("0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB")
FEED_REGISTRY = Web3.to_checksum_address("0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC")
ASSET_2 = Web3.to_checksum_address("0xdDdDddDdDdddDDddDDddDDDDdDdDDdDDdDDDDDDd")
SOURCE_2 = Web3.to_checksum_address("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE")


def _make_oracle():
    ctx = MagicMock()
    oracle = PriceOracleMiddleware(ctx, CONTRACT_ADDR)
    return oracle, ctx


class TestGetSourceOfAssetPrice:
    def test_returns_checksum_address(self):
        oracle, ctx = _make_oracle()
        ctx.call.return_value = encode(["address"], [SOURCE_ADDR])

        result = oracle.get_source_of_asset_price(ASSET_ADDR)

        assert result == SOURCE_ADDR
        ctx.call.assert_called_once()


class TestChainlinkFeedRegistry:
    def test_returns_checksum_address(self):
        oracle, ctx = _make_oracle()
        ctx.call.return_value = encode(["address"], [FEED_REGISTRY])

        result = oracle.chainlink_feed_registry()

        assert result == FEED_REGISTRY
        ctx.call.assert_called_once()


class TestGetAssetsPriceSources:
    def test_parses_single_event(self):
        oracle, ctx = _make_oracle()
        event_data = encode(["address", "address"], [ASSET_ADDR, SOURCE_ADDR])
        ctx.get_logs.return_value = [{"data": event_data}]

        result = oracle.get_assets_price_sources()

        assert len(result) == 1
        assert isinstance(result[0], AssetPriceSource)
        assert result[0].asset == ASSET_ADDR
        assert result[0].source == SOURCE_ADDR

    def test_parses_multiple_events(self):
        oracle, ctx = _make_oracle()
        event1 = encode(["address", "address"], [ASSET_ADDR, SOURCE_ADDR])
        event2 = encode(["address", "address"], [ASSET_2, SOURCE_2])
        ctx.get_logs.return_value = [{"data": event1}, {"data": event2}]

        result = oracle.get_assets_price_sources()

        assert len(result) == 2
        assert result[0].asset == ASSET_ADDR
        assert result[0].source == SOURCE_ADDR
        assert result[1].asset == ASSET_2
        assert result[1].source == SOURCE_2

    def test_returns_empty_list_when_no_events(self):
        oracle, ctx = _make_oracle()
        ctx.get_logs.return_value = []

        result = oracle.get_assets_price_sources()

        assert not result


class TestGetAssetPrice:
    def test_returns_price_dataclass(self):
        oracle, ctx = _make_oracle()
        ctx.call.return_value = encode(["uint256", "uint256"], [1_500_000_000, 8])

        result = oracle.get_asset_price(ASSET_ADDR)

        assert isinstance(result, Price)
        assert result.asset == ASSET_ADDR
        assert result.amount == 1_500_000_000
        assert result.decimals == 8


class TestGetAssetPriceSourceUpdatedEvents:
    def test_uses_correct_event_signature_hash(self):
        oracle, ctx = _make_oracle()
        ctx.get_logs.return_value = []

        oracle.get_assets_price_sources()

        expected_hash = HexBytes(
            Web3.keccak(text="AssetPriceSourceUpdated(address,address)")
        ).to_0x_hex()
        ctx.get_logs.assert_called_once_with(
            contract_address=CONTRACT_ADDR, topics=[expected_hash]
        )


class TestAssetPriceSourceDataclass:
    def test_fields(self):
        source = AssetPriceSource(asset=ASSET_ADDR, source=SOURCE_ADDR)
        assert source.asset == ASSET_ADDR
        assert source.source == SOURCE_ADDR

    def test_slots(self):
        source = AssetPriceSource(asset=ASSET_ADDR, source=SOURCE_ADDR)
        assert hasattr(source, "__slots__") or not hasattr(source, "__dict__")

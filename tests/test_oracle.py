"""Unit tests for the oracle wrappers — mock _call() and _ctx, verify encoding
and decoding for both PriceOracleMiddleware and PriceOracleMiddlewareManager."""

from unittest.mock import MagicMock

from eth_abi import encode
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion.core.oracle import (
    AssetPriceSource,
    PriceOracleMiddleware,
    PriceOracleMiddlewareManager,
)
from ipor_fusion.types import Price

CONTRACT_ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")
MANAGER_ADDR = Web3.to_checksum_address("0x2222222222222222222222222222222222222222")
ASSET_ADDR = Web3.to_checksum_address("0xaAaAaAaaAaAaAaaAaAAAAAAAAaaaAaAaAaaAaaAa")
SOURCE_ADDR = Web3.to_checksum_address("0xbBbBBBBbbBBBbbbBbbBbbbbBBbBbbbbBbBbbBBbB")
FEED_REGISTRY = Web3.to_checksum_address("0xCcCCccccCCCCcCCCCCCcCcCccCcCCCcCcccccccC")
ASSET_2 = Web3.to_checksum_address("0xdDdDddDdDdddDDddDDddDDDDdDdDDdDDdDDDDDDd")
SOURCE_2 = Web3.to_checksum_address("0xEeeeeEeeeEeEeeEeEeEeeEEEeeeeEeeeeeeeEEeE")
MIDDLEWARE_ADDR = Web3.to_checksum_address("0xfFFfFffFffFFfFFffFFFffFfFfFFFFffFFFFFfFf")


def _make_oracle():
    ctx = MagicMock()
    oracle = PriceOracleMiddleware(ctx, CONTRACT_ADDR)
    return oracle, ctx


def _make_manager():
    ctx = MagicMock()
    manager = PriceOracleMiddlewareManager(ctx, MANAGER_ADDR)
    return manager, ctx


class TestGetSourceOfAssetPrice:
    def test_returns_checksum_address(self):
        oracle, ctx = _make_oracle()
        ctx.call.return_value = encode(["address"], [SOURCE_ADDR])

        result = oracle.get_source_of_asset_price(ASSET_ADDR).call()

        assert result == SOURCE_ADDR
        ctx.call.assert_called_once()


class TestChainlinkFeedRegistry:
    def test_returns_checksum_address(self):
        oracle, ctx = _make_oracle()
        ctx.call.return_value = encode(["address"], [FEED_REGISTRY])

        result = oracle.chainlink_feed_registry().call()

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

        result = oracle.get_asset_price(ASSET_ADDR).call()

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


class TestManagerSetAssetsPriceSources:
    def test_sends_transaction(self):
        manager, ctx = _make_manager()
        ctx.send.return_value = {"status": 1}

        result = manager.set_assets_price_sources([ASSET_ADDR], [SOURCE_ADDR]).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_encodes_singular_price_signature(self):
        # Pins the singular "Price" spelling: the global middleware's setter is
        # setAssetsPricesSources, one letter apart and a different contract.
        manager, ctx = _make_manager()

        manager.set_assets_price_sources([ASSET_ADDR], [SOURCE_ADDR]).send()

        sent_to, sent_data = ctx.send.call_args[0]
        assert sent_to == MANAGER_ADDR
        selector = Web3.keccak(text="setAssetsPriceSources(address[],address[])")[:4]
        assert sent_data == selector + encode(
            ["address[]", "address[]"], [[ASSET_ADDR], [SOURCE_ADDR]]
        )

    def test_encodes_two_assets_in_one_call(self):
        manager, ctx = _make_manager()

        manager.set_assets_price_sources(
            [ASSET_ADDR, ASSET_2], [SOURCE_ADDR, SOURCE_2]
        ).send()

        _, sent_data = ctx.send.call_args[0]
        selector = Web3.keccak(text="setAssetsPriceSources(address[],address[])")[:4]
        assert sent_data == selector + encode(
            ["address[]", "address[]"],
            [[ASSET_ADDR, ASSET_2], [SOURCE_ADDR, SOURCE_2]],
        )


class TestManagerRemoveAssetsPriceSources:
    def test_sends_transaction(self):
        manager, ctx = _make_manager()
        ctx.send.return_value = {"status": 1}

        result = manager.remove_assets_price_sources([ASSET_ADDR]).send()

        assert result == {"status": 1}
        ctx.send.assert_called_once()

    def test_encodes_calldata(self):
        manager, ctx = _make_manager()

        manager.remove_assets_price_sources([ASSET_ADDR, ASSET_2]).send()

        sent_to, sent_data = ctx.send.call_args[0]
        assert sent_to == MANAGER_ADDR
        selector = Web3.keccak(text="removeAssetsPriceSources(address[])")[:4]
        assert sent_data == selector + encode(["address[]"], [[ASSET_ADDR, ASSET_2]])


class TestManagerReads:
    def test_get_source_of_asset_price(self):
        manager, ctx = _make_manager()
        ctx.call.return_value = encode(["address"], [SOURCE_ADDR])

        result = manager.get_source_of_asset_price(ASSET_ADDR).call()

        assert result == SOURCE_ADDR
        ctx.call.assert_called_once()

    def test_get_asset_price(self):
        # The manager always normalizes to WAD, so 18 decimals is the only
        # value this contract can return.
        manager, ctx = _make_manager()
        ctx.call.return_value = encode(["uint256", "uint256"], [2 * 10**18, 18])

        result = manager.get_asset_price(ASSET_ADDR).call()

        assert isinstance(result, Price)
        assert result.asset == ASSET_ADDR
        assert result.amount == 2 * 10**18
        assert result.decimals == 18
        ctx.call.assert_called_once()

    def test_get_configured_assets(self):
        manager, ctx = _make_manager()
        ctx.call.return_value = encode(["address[]"], [[ASSET_ADDR, ASSET_2]])

        result = manager.get_configured_assets().call()

        assert result == [ASSET_ADDR, ASSET_2]
        ctx.call.assert_called_once()

    def test_get_configured_assets_empty(self):
        manager, ctx = _make_manager()
        ctx.call.return_value = encode(["address[]"], [[]])

        result = manager.get_configured_assets().call()

        assert result == []
        ctx.call.assert_called_once()

    def test_get_price_oracle_middleware(self):
        manager, ctx = _make_manager()
        ctx.call.return_value = encode(["address"], [MIDDLEWARE_ADDR])

        result = manager.get_price_oracle_middleware().call()

        assert result == MIDDLEWARE_ADDR
        ctx.call.assert_called_once()

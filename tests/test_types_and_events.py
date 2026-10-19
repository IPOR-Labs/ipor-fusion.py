from dataclasses import dataclass

import pytest
from eth_abi import encode
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion.fuses.events import extract_events
from ipor_fusion.types import Amount, Decimals, Price

ADDR = Web3.to_checksum_address("0x1111111111111111111111111111111111111111")


class TestPrice:
    def test_readable(self):
        price = Price(asset=ADDR, amount=Amount(1_500_000), decimals=Decimals(6))
        assert price.readable() == 1.5

    def test_negative_decimals_raises(self):
        with pytest.raises(ValueError, match="non-negative"):
            Price(asset=ADDR, amount=Amount(100), decimals=Decimals(-1))

    def test_repr(self):
        price = Price(asset=ADDR, amount=Amount(2_000_000), decimals=Decimals(6))
        r = repr(price)
        assert "Price(" in r
        assert "readable=2.0 USD" in r

    def test_str(self):
        price = Price(asset=ADDR, amount=Amount(1_000_000), decimals=Decimals(6))
        assert str(price) == repr(price)


class TestExtractEvents:
    def test_extracts_matching_event(self):
        @dataclass
        class TransferEvent:
            value: int

        sig = "Transfer(uint256)"
        sig_hash = Web3.keccak(text=sig)

        receipt = {
            "logs": [
                {
                    "topics": [HexBytes(sig_hash)],
                    "data": HexBytes(encode(["uint256"], [42])),
                },
                {
                    "topics": [HexBytes(b"\x00" * 32)],
                    "data": HexBytes(b""),
                },
            ]
        }

        events = extract_events(receipt, sig, ["uint256"], TransferEvent)
        assert len(events) == 1
        assert events[0].value == 42

    def test_returns_empty_for_no_match(self):
        receipt = {"logs": []}
        events = extract_events(receipt, "Foo()", [], lambda: None)
        assert not events


class TestParsers:
    def test_hex_address_is_lowercase_0x_from_any_spelling(self):
        from ipor_fusion import parse_hex_address

        checksum = "0xAbCdEf0123456789aBcDeF0123456789AbCdEf01"
        lower = checksum.lower()
        assert parse_hex_address(checksum) == lower
        assert parse_hex_address(lower[2:]) == lower
        assert parse_hex_address(bytes.fromhex(lower[2:])) == lower

    def test_tx_hash_is_lowercase_0x_from_any_spelling(self):
        from ipor_fusion import parse_tx_hash

        digest = "AB" * 32
        assert parse_tx_hash(digest) == "0x" + digest.lower()
        assert parse_tx_hash(bytes.fromhex(digest)) == "0x" + digest.lower()

    @pytest.mark.parametrize("value", ["0x1234", "0x" + "zz" * 20, b"\x00" * 19])
    def test_hex_address_rejects_anything_else(self, value):
        from ipor_fusion import parse_hex_address

        with pytest.raises(ValueError):
            parse_hex_address(value)


class TestPositionEventAddresses:
    def test_decoded_addresses_are_checksummed(self):
        from eth_abi import encode
        from web3 import Web3

        from ipor_fusion.fuses.uniswap_v3 import UniswapV3Events

        lower = "0x" + "ab" * 20
        signature = (
            "UniswapV3NewPositionFuseEnter(address,uint256,uint128,uint256,uint256,"
            "address,address,uint24,int24,int24)"
        )
        data = encode(
            [
                "address",
                "uint256",
                "uint128",
                "uint256",
                "uint256",
                "address",
                "address",
                "uint24",
                "int24",
                "int24",
            ],
            [lower, 1, 2, 3, 4, lower, lower, 500, -10, 10],
        )
        receipt = {"logs": [{"topics": [Web3.keccak(text=signature)], "data": data}]}
        (event,) = UniswapV3Events.extract_new_position_events(receipt)  # type: ignore[arg-type]
        checksum = Web3.to_checksum_address(lower)
        assert (event.version, event.sender, event.recipient) == (checksum,) * 3
        assert event.token_id == 1

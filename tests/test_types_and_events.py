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

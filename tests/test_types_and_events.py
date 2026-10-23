from dataclasses import dataclass

import pytest
from eth_abi import encode
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion.fuses.events import extract_events
from ipor_fusion.types import MAX_UINT256, Amount, Decimals, Price, Shares

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
    def test_hex_address_is_lowercase_0x_from_either_prefix_case(self):
        from ipor_fusion import parse_hex_address

        checksum = "0xAbCdEf0123456789aBcDeF0123456789AbCdEf01"
        lower = checksum.lower()
        assert parse_hex_address(checksum) == lower
        assert parse_hex_address("0X" + lower[2:]) == lower
        assert parse_hex_address(bytes.fromhex(lower[2:])) == lower

    def test_tx_hash_is_lowercase_0x_from_either_prefix_case(self):
        from ipor_fusion import parse_tx_hash

        digest = "AB" * 32
        assert parse_tx_hash("0x" + digest) == "0x" + digest.lower()
        assert parse_tx_hash(bytes.fromhex(digest)) == "0x" + digest.lower()

    @pytest.mark.parametrize(
        "value",
        [
            "0x1234",
            "0x" + "zz" * 20,
            b"\x00" * 19,
            "ab" * 20,
            " 0x" + "ab" * 20,
            "0x" + "ab" * 20 + "\n",
            "0x0X" + "ab" * 19,
        ],
    )
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


class TestRoleParsers:
    LOWER = "0x43ee0243ea8cf02f7087d8b16c8d2007cc9c7ca2"

    @pytest.mark.parametrize(
        "parse",
        [
            "parse_plasma_vault_address",
            "parse_fuse_address",
            "parse_access_manager_address",
        ],
    )
    def test_role_parsers_checksum_and_validate(self, parse):
        from web3 import Web3

        import ipor_fusion

        parser = getattr(ipor_fusion, parse)
        checksum = Web3.to_checksum_address(self.LOWER)
        assert parser(self.LOWER) == checksum
        assert parser(bytes.fromhex(self.LOWER[2:])) == checksum
        with pytest.raises(ValueError):
            parser("0x1234")


class TestAmountText:
    @pytest.mark.parametrize(
        ("text", "decimals", "raw"),
        [
            ("1.5", 6, 1_500_000),
            ("0.000001", 6, 1),
            ("1", 18, 10**18),
            ("1.", 6, 1_000_000),
            (".5", 6, 500_000),
            (" 42 ", 0, 42),
            ("0", 6, 0),
            ("1.000000", 6, 1_000_000),
            (str(MAX_UINT256), 0, MAX_UINT256),
            (
                "115792089237316195423570985008687907853269984665640564039457.584007913129639935",
                18,
                MAX_UINT256,
            ),
        ],
    )
    def test_parse_amount_is_exact(self, text, decimals, raw):
        from ipor_fusion import parse_amount

        assert parse_amount(text, Decimals(decimals)) == raw

    @pytest.mark.parametrize(
        "text",
        [
            "",
            ".",
            "-1",
            "+1",
            "1e6",
            "1,000",
            "1_000",
            "1.5.0",
            "0x10",
            "1 0",
            "nan",
            "\u0663",
            "\uff11.5",
        ],
    )
    def test_parse_amount_rejects_anything_but_plain_decimal_text(self, text):
        from ipor_fusion import parse_amount

        with pytest.raises(ValueError, match="not a decimal amount"):
            parse_amount(text, Decimals(6))

    def test_parse_amount_never_rounds(self):
        from ipor_fusion import parse_amount

        with pytest.raises(ValueError, match="7 fractional digits"):
            parse_amount("1.0000001", Decimals(6))

    def test_parse_amount_rejects_uint256_overflow_and_floats(self):
        from ipor_fusion import parse_amount

        with pytest.raises(ValueError, match="exceeds uint256"):
            parse_amount(str(MAX_UINT256 + 1), Decimals(0))
        with pytest.raises(TypeError, match="floats lose precision"):
            parse_amount(1.5, Decimals(6))  # type: ignore[arg-type]
        with pytest.raises(ValueError, match="decimals"):
            parse_amount("1", Decimals(-1))
        with pytest.raises(ValueError, match="0..255"):
            parse_amount("1", Decimals(256))

    def test_parse_amount_bounds_digits_before_converting(self):
        from ipor_fusion import parse_amount

        # Leading zeros are not significant, past Python's int() digit limit too.
        assert parse_amount("0" * 5000 + "1", Decimals(0)) == 1
        with pytest.raises(ValueError, match="exceeds uint256"):
            parse_amount("1" * 5000, Decimals(0))
        assert parse_amount("0." + "0" * 254 + "1", Decimals(255)) == 1

    @pytest.mark.parametrize(
        ("raw", "decimals", "text"),
        [
            (1_500_000, 6, "1.5"),
            (1, 6, "0.000001"),
            (10**18, 18, "1"),
            (0, 6, "0"),
            (42, 0, "42"),
            (123_456_789, 4, "12345.6789"),
            (
                MAX_UINT256,
                18,
                "115792089237316195423570985008687907853269984665640564039457.584007913129639935",
            ),
        ],
    )
    def test_format_amount_round_trips(self, raw, decimals, text):
        from ipor_fusion import format_amount, parse_amount

        assert format_amount(Amount(raw), Decimals(decimals)) == text
        assert parse_amount(text, Decimals(decimals)) == raw

    @pytest.mark.parametrize("raw", [-1, MAX_UINT256 + 1])
    def test_format_amount_rejects_values_outside_uint256(self, raw):
        from ipor_fusion import format_amount, format_shares

        with pytest.raises(ValueError, match="within uint256"):
            format_amount(Amount(raw), Decimals(6))
        with pytest.raises(ValueError, match="within uint256"):
            format_shares(Shares(raw), Decimals(6))
        with pytest.raises(ValueError, match="0..255"):
            format_amount(Amount(1), Decimals(256))

    def test_shares_pair_mirrors_amounts(self):
        from ipor_fusion import format_shares, parse_shares

        assert parse_shares("2.5", Decimals(8)) == Shares(250_000_000)
        assert format_shares(Shares(250_000_000), Decimals(8)) == "2.5"

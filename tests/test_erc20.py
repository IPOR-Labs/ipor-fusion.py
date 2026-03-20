from unittest.mock import MagicMock

from eth_abi import encode
from web3 import Web3

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.types import Amount

USDC_ADDRESS = Web3.to_checksum_address("0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48")
ALICE = Web3.to_checksum_address("0x70997970C51812dc3A010C7d01b50e0d17dc79C8")
BOB = Web3.to_checksum_address("0x3C44CdDdB6a900fa2b585dd299e03d12FA4293BC")


def _make_erc20() -> tuple[ERC20, MagicMock]:
    ctx = MagicMock(spec=Web3Context)
    erc20 = ERC20(ctx, USDC_ADDRESS)
    return erc20, ctx


def test_address_returns_checksum_address():
    erc20, _ = _make_erc20()
    assert erc20.address == USDC_ADDRESS


def test_decimals():
    erc20, ctx = _make_erc20()
    ctx.call.return_value = encode(["uint256"], [6])

    result = erc20.decimals()

    assert result == 6
    ctx.call.assert_called_once()


def test_symbol():
    erc20, ctx = _make_erc20()
    ctx.call.return_value = encode(["string"], ["USDC"])

    result = erc20.symbol()

    assert result == "USDC"
    ctx.call.assert_called_once()


def test_name():
    erc20, ctx = _make_erc20()
    ctx.call.return_value = encode(["string"], ["USD Coin"])

    result = erc20.name()

    assert result == "USD Coin"
    ctx.call.assert_called_once()


def test_total_supply():
    erc20, ctx = _make_erc20()
    expected = 10_000_000_000
    ctx.call.return_value = encode(["uint256"], [expected])

    result = erc20.total_supply()

    assert result == Amount(expected)
    ctx.call.assert_called_once()


def test_balance_of():
    erc20, ctx = _make_erc20()
    expected = 1_000_000
    ctx.call.return_value = encode(["uint256"], [expected])

    result = erc20.balance_of(ALICE)

    assert result == Amount(expected)
    ctx.call.assert_called_once()


def test_allowance():
    erc20, ctx = _make_erc20()
    expected = 500_000
    ctx.call.return_value = encode(["uint256"], [expected])

    result = erc20.allowance(ALICE, BOB)

    assert result == Amount(expected)
    ctx.call.assert_called_once()


def test_transfer():
    erc20, ctx = _make_erc20()
    ctx.send.return_value = {"status": 1}

    erc20.transfer(ALICE, Amount(500_000))

    ctx.send.assert_called_once()


def test_approve():
    erc20, ctx = _make_erc20()
    ctx.send.return_value = {"status": 1}

    erc20.approve(ALICE, Amount(1_000_000))

    ctx.send.assert_called_once()

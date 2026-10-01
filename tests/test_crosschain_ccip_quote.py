"""``quote_ccip_native_fee``: the Router's quote read off the payer's
``CcipInsufficientNativeFeeBalance`` revert in a zero-balance simulation."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from _simulate import simulate_response
from eth_abi import encode
from web3 import Web3

from ipor_fusion import quote_ccip_native_fee
from ipor_fusion.core.contract import Call
from ipor_fusion.crosschain.ccip.quote import INSUFFICIENT_NATIVE_FEE_SELECTOR

FACTORY = Web3.to_checksum_address("0x" + "11" * 20)
ALPHA = Web3.to_checksum_address("0x" + "22" * 20)
REQUEST = Call(to=FACTORY, data=b"\x12\x34\x56\x78")


def _web3(call_result: dict) -> MagicMock:
    web3 = MagicMock()
    web3.eth.get_block.return_value = {"timestamp": 1_700_000_000}
    web3.provider.make_request.return_value = {
        "result": simulate_response(**call_result)
    }
    return web3


def _reverted(data: bytes) -> dict:
    return {
        "status": "0x0",
        "returnData": "0x" + data.hex(),
        "gasUsed": "0x5208",
        "logs": [],
        "error": {"message": "execution reverted"},
    }


def test_quote_is_the_required_fee_and_the_payer_is_emptied():
    required = 8_528_218_631_341_098
    data = INSUFFICIENT_NATIVE_FEE_SELECTOR + encode(
        ["uint256", "uint256"], [0, required]
    )
    web3 = _web3(_reverted(data))

    fee = quote_ccip_native_fee(
        web3, REQUEST, payer=FACTORY, from_=ALPHA, block=100, gas=1_000_000
    )

    assert fee == required
    (block,) = web3.provider.make_request.call_args.args[1][0]["blockStateCalls"]
    assert block["stateOverrides"][FACTORY]["balance"] == "0x0"
    (call,) = block["calls"]
    assert call["from"] == ALPHA
    assert call["gas"] == hex(1_000_000)
    assert call["to"] == FACTORY


def test_quote_rejects_a_call_that_sends_no_message():
    web3 = _web3({"status": "0x1", "returnData": "0x", "gasUsed": "0x0", "logs": []})

    with pytest.raises(ValueError, match="sends no CCIP message"):
        quote_ccip_native_fee(web3, REQUEST, payer=FACTORY, from_=ALPHA)


def test_quote_names_an_earlier_revert():
    data = Web3.keccak(text="CreatorNotAllowed(address)")[:4] + encode(
        ["address"], [ALPHA]
    )
    web3 = _web3(_reverted(data))

    with pytest.raises(
        ValueError, match="before the CCIP fee check: CreatorNotAllowed"
    ):
        quote_ccip_native_fee(web3, REQUEST, payer=FACTORY, from_=ALPHA)

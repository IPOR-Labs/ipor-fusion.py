"""RPC-gated proof for the Compound V3 Institutional USDC example."""

from __future__ import annotations

from eth_abi import decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3


def test_compound_v3_institutional_usdc_example_simulation(web3_eth, load_example):
    mod = load_example("advanced_compound_v3_institutional_usdc_ethereum.py")

    for fuse in (mod.SUPPLY_FUSE, mod.BORROW_FUSE, mod.BALANCE_FUSE):
        (comet,) = decode(
            ["address"],
            web3_eth.eth.call(
                {"to": fuse, "data": function_signature_to_4byte_selector("COMET()")},
                block_identifier=mod.PINNED_BLOCK,
            ),
        )
        assert Web3.to_checksum_address(comet) == mod.COMET

    result = mod.run_simulation(web3_eth)
    assert result.gas_used > 0

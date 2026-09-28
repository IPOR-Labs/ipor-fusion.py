"""RPC-gated proof for the Arbitrum Euler V2 looping example."""

from __future__ import annotations

from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3


def _view_address(web3, contract: str, signature: str, block: int) -> str:
    raw = web3.eth.call(
        {"to": contract, "data": function_signature_to_4byte_selector(signature)},
        block_identifier=block,
    )
    (address,) = decode(["address"], raw)
    return Web3.to_checksum_address(address)


def test_advanced_euler_v2_looping_arbitrum_simulation(web3_arb, load_example):
    mod = load_example("advanced_euler_v2_looping_arbitrum.py")

    assert (
        _view_address(web3_arb, mod.EVAULT_WBTC, "asset()", mod.PINNED_BLOCK)
        == mod.ARBITRUM_WBTC
    )
    assert (
        _view_address(web3_arb, mod.EVAULT_USDC, "asset()", mod.PINNED_BLOCK)
        == mod.ARBITRUM_USDC
    )

    uniswap_factory = Web3.to_checksum_address(
        "0x1F98431c8aD98523631AE4a59f267346ea31F984"
    )
    pool_data = function_signature_to_4byte_selector(
        "getPool(address,address,uint24)"
    ) + encode(
        ["address", "address", "uint24"],
        [mod.ARBITRUM_USDC, mod.ARBITRUM_WBTC, mod.UNISWAP_POOL_FEE],
    )
    (pool,) = decode(
        ["address"],
        web3_arb.eth.call(
            {"to": uniswap_factory, "data": pool_data},
            block_identifier=mod.PINNED_BLOCK,
        ),
    )
    assert Web3.to_checksum_address(pool) == mod.UNISWAP_USDC_WBTC_POOL

    deployed_selectors = {
        mod.EULER_SUPPLY_FUSE: "enter((address,uint256,bytes1))",
        mod.EULER_COLLATERAL_FUSE: "enter((address,bytes1))",
        mod.EULER_CONTROLLER_FUSE: "enter((address,bytes1))",
        mod.EULER_BORROW_FUSE: "enter((address,uint256,bytes1))",
        mod.MORPHO_FLASH_LOAN_FUSE: "enter((address,uint256,bytes))",
        mod.UNIVERSAL_SWAPPER_FUSE_V2: (
            "enter((address,address,uint256,uint256,(address[],bytes[])))"
        ),
        mod.MORPHO_CALLBACK_HANDLER: "onMorphoFlashLoan(uint256,bytes)",
    }
    for address, signature in deployed_selectors.items():
        code = bytes(web3_arb.eth.get_code(address, mod.PINNED_BLOCK))
        assert function_signature_to_4byte_selector(signature) in code

    result = mod.run_simulation(web3_arb)
    assert result.gas_used > 0

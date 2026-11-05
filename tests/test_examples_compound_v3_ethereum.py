"""Offline checks for the Compound V3 Institutional USDC example."""

from __future__ import annotations

from eth_abi import decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import IporFusionMarkets
from ipor_fusion.types import MAX_UINT256

EXAMPLE = "advanced_compound_v3_institutional_usdc_ethereum.py"


def _selector(signature: str) -> bytes:
    return function_signature_to_4byte_selector(signature)


def test_example_targets_market_57(load_example):
    mod = load_example(EXAMPLE)

    assert mod.COMPOUND_MARKET == IporFusionMarkets.COMPOUND_V3_INSTITUTIONAL_USDC
    assert mod.ERC20_MARKET == IporFusionMarkets.ERC20_VAULT_BALANCE


def test_substrates_are_narrow(load_example):
    mod = load_example(EXAMPLE)

    def addresses(substrates: list[bytes]) -> list[str]:
        return [Web3.to_checksum_address(s[12:]) for s in substrates]

    assert addresses(mod.compound_market_substrates()) == [
        mod.ETHEREUM_WETH,
        mod.ETHEREUM_USDC,
    ]
    assert addresses(mod.erc20_market_substrates()) == [mod.ETHEREUM_USDC]


def test_collateral_actions_name_weth(load_example):
    mod = load_example(EXAMPLE)
    cases = [
        (mod.build_supply_collateral_action, "enter((address,uint256))"),
        (mod.build_withdraw_collateral_action, "exit((address,uint256))"),
    ]

    for build, signature in cases:
        action = build(mod.WETH_COLLATERAL)
        assert action.fuse == mod.SUPPLY_FUSE
        assert action.data[:4] == _selector(signature)
        (asset, amount) = decode(["address", "uint256"], action.data[4:])
        assert Web3.to_checksum_address(asset) == mod.ETHEREUM_WETH
        assert amount == mod.WETH_COLLATERAL


def test_borrow_and_repay_actions_carry_only_the_amount(load_example):
    mod = load_example(EXAMPLE)

    borrow = mod.build_borrow_action(mod.USDC_BORROW)
    assert borrow.fuse == mod.BORROW_FUSE
    assert borrow.data[:4] == _selector("enter((uint256))")
    assert decode(["uint256"], borrow.data[4:]) == (mod.USDC_BORROW,)

    repay = mod.build_repay_action(MAX_UINT256)
    assert repay.fuse == mod.BORROW_FUSE
    assert repay.data[:4] == _selector("exit((uint256))")
    assert decode(["uint256"], repay.data[4:]) == (MAX_UINT256,)


def test_clone_uses_weth_as_the_underlying(load_example):
    mod = load_example(EXAMPLE)

    assert mod.clone_args()["underlying_token"] == mod.ETHEREUM_WETH
    assert mod.clone_args()["owner"] == mod.OWNER

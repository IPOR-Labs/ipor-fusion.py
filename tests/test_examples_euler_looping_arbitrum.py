"""Offline checks for the Arbitrum Euler V2 looping example."""

from __future__ import annotations

from eth_abi import decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.fuses import UniversalTokenSwapperSubstrates


def _decode_euler_substrate(substrate: bytes) -> tuple[str, int, int, int]:
    value = int.from_bytes(substrate, "big")
    vault = Web3.to_checksum_address(f"0x{(value >> 96) & ((1 << 160) - 1):040x}")
    return vault, (value >> 88) & 0xFF, (value >> 80) & 0xFF, (value >> 72) & 0xFF


def test_euler_looping_substrates_are_narrow(load_example):
    mod = load_example("advanced_euler_v2_looping_arbitrum.py")

    assert [
        _decode_euler_substrate(value) for value in mod.euler_market_substrates()
    ] == [
        (mod.EVAULT_WBTC, 1, 0, mod.SUB_ACCOUNT),
        (mod.EVAULT_USDC, 0, 1, mod.SUB_ACCOUNT),
    ]
    assert set(mod.swapper_market_substrates()) == {
        UniversalTokenSwapperSubstrates.token(mod.ARBITRUM_USDC),
        UniversalTokenSwapperSubstrates.token(mod.ARBITRUM_WBTC),
        UniversalTokenSwapperSubstrates.target(mod.ARBITRUM_USDC),
        UniversalTokenSwapperSubstrates.target(mod.UNISWAP_UNIVERSAL_ROUTER),
        UniversalTokenSwapperSubstrates.slippage(mod.MAX_SWAP_SLIPPAGE_WAD),
    }


def test_euler_looping_enable_actions_encode_collateral_then_controller(load_example):
    mod = load_example("advanced_euler_v2_looping_arbitrum.py")
    actions = mod.enable_euler_actions()

    assert [action.fuse for action in actions] == [
        mod.EULER_COLLATERAL_FUSE,
        mod.EULER_CONTROLLER_FUSE,
    ]
    expected = [mod.EVAULT_WBTC, mod.EVAULT_USDC]
    selector = function_signature_to_4byte_selector("enter((address,bytes1))")
    for action, vault in zip(actions, expected, strict=True):
        assert action.data[:4] == selector
        ((decoded_vault, sub_account),) = decode(["(address,bytes1)"], action.data[4:])
        assert Web3.to_checksum_address(decoded_vault) == vault
        assert sub_account == bytes([mod.SUB_ACCOUNT])


def test_euler_looping_flash_action_contains_supply_borrow_swap(load_example):
    mod = load_example("advanced_euler_v2_looping_arbitrum.py")
    action = mod.build_loop_action()

    assert action.fuse == mod.MORPHO_FLASH_LOAN_FUSE
    assert action.data[:4] == function_signature_to_4byte_selector(
        "enter((address,uint256,bytes))"
    )
    ((asset, amount, encoded_actions),) = decode(
        ["(address,uint256,bytes)"], action.data[4:]
    )
    assert Web3.to_checksum_address(asset) == mod.ARBITRUM_WBTC
    assert amount == mod.FLASH_WBTC

    (inner_actions,) = decode(["(address,bytes)[]"], encoded_actions)
    assert [Web3.to_checksum_address(fuse) for fuse, _ in inner_actions] == [
        mod.EULER_SUPPLY_FUSE,
        mod.EULER_BORROW_FUSE,
        mod.UNIVERSAL_SWAPPER_FUSE_V2,
    ]

    supply = inner_actions[0][1]
    ((supply_vault, supply_amount, supply_sub_account),) = decode(
        ["(address,uint256,bytes1)"], supply[4:]
    )
    assert Web3.to_checksum_address(supply_vault) == mod.EVAULT_WBTC
    assert supply_amount == mod.SUPPLY_WBTC
    assert supply_sub_account == bytes([mod.SUB_ACCOUNT])

    borrow = inner_actions[1][1]
    ((borrow_vault, borrow_amount, borrow_sub_account),) = decode(
        ["(address,uint256,bytes1)"], borrow[4:]
    )
    assert Web3.to_checksum_address(borrow_vault) == mod.EVAULT_USDC
    assert borrow_amount == mod.BORROW_USDC
    assert borrow_sub_account == bytes([mod.SUB_ACCOUNT])


def test_euler_looping_swap_uses_deployed_min_amount_out_abi(load_example):
    mod = load_example("advanced_euler_v2_looping_arbitrum.py")
    action = mod.build_swap_action()
    signature = "enter((address,address,uint256,uint256,(address[],bytes[])))"

    assert action.data[:4] == function_signature_to_4byte_selector(signature)
    ((token_in, token_out, amount_in, min_out, calls),) = decode(
        ["(address,address,uint256,uint256,(address[],bytes[]))"], action.data[4:]
    )
    assert Web3.to_checksum_address(token_in) == mod.ARBITRUM_USDC
    assert Web3.to_checksum_address(token_out) == mod.ARBITRUM_WBTC
    assert amount_in == mod.BORROW_USDC
    assert min_out == mod.FLASH_WBTC
    assert [Web3.to_checksum_address(target) for target in calls[0]] == [
        mod.ARBITRUM_USDC,
        mod.UNISWAP_UNIVERSAL_ROUTER,
    ]
    assert calls[1][1][:4] == function_signature_to_4byte_selector(
        "execute(bytes,bytes[])"
    )


def test_euler_looping_callback_and_clone_are_import_safe(load_example):
    mod = load_example("advanced_euler_v2_looping_arbitrum.py")

    assert mod.morpho_callback_selector() == function_signature_to_4byte_selector(
        "onMorphoFlashLoan(uint256,bytes)"
    )
    assert mod.clone_args()["underlying_token"] == mod.ARBITRUM_WBTC
    assert mod.unsigned_clone_calldata()[:4] == function_signature_to_4byte_selector(
        "clone(string,string,address,uint256,address,uint256)"
    )

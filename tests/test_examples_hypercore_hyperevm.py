"""Offline checks for the HyperCore example on the official HyperEVM deployment."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from eth_abi import decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import decode_substrate
from ipor_fusion.hypercore import (
    SPOT_DEX,
    USDC_SYSTEM_ADDRESS,
    HyperCorePerpAssetInfo,
    HyperCoreTokenInfo,
    TimeInForce,
)
from ipor_fusion.types import PlasmaVaultAddress

EXAMPLE = "hypercore_vault_official_deployment_hyperevm.py"
OFFICIAL = json.loads(
    (Path(__file__).parent / "fixtures" / "hypercore_official.json").read_text()
)
VAULT = PlasmaVaultAddress(
    Web3.to_checksum_address("0x532960128293Ed8d724938119E9Cda95732af3a0")
)
SEND_ASSET = "enterSendAsset((address,uint32,uint32,uint64,uint64))"
ORDER = "enter((uint32,bool,uint64,uint64,bool,uint8,uint128))"


def _args(data: bytes, signature: str) -> tuple[object, ...]:
    assert data[:4] == function_signature_to_4byte_selector(signature)
    types = signature[signature.index("(") + 1 : -1]
    (values,) = decode([types], data[4:])
    return values


def test_example_addresses_are_the_published_deployment(load_example):
    mod = load_example(EXAMPLE)
    contracts = OFFICIAL["contracts"]
    for name, address in (
        ("HyperCoreDepositFuse", mod.DEPOSIT_FUSE),
        ("HyperCoreSendFuse", mod.SEND_FUSE),
        ("HyperCoreOrderFuse", mod.ORDER_FUSE),
        ("HyperCoreBalanceFuse", mod.BALANCE_FUSE),
        ("HyperCoreCapitalFlowPreHook", mod.CAPITAL_FLOW_PRE_HOOK),
        ("HyperCorePendingActionPreHook", mod.PENDING_ACTION_PRE_HOOK),
        ("HyperCorePendingReader", mod.PENDING_READER),
    ):
        assert contracts[name] == address, name


def test_clone_calldata_names_the_simulation_owner(load_example):
    mod = load_example(EXAMPLE)
    data = mod.unsigned_clone_calldata()
    assert data[:4] == function_signature_to_4byte_selector(
        "clone(string,string,address,uint256,address,uint256)"
    )
    name, symbol, asset, delay, owner, package = decode(
        ["string", "string", "address", "uint256", "address", "uint256"], data[4:]
    )
    assert (asset, delay, package) == (mod.HYPEREVM_USDC.lower(), 0, 0)
    assert Web3.to_checksum_address(owner) == mod.OWNER
    assert symbol == "ipUSDChc" and "HyperCore" in name


def test_market_substrates_decode_to_the_intended_grants(load_example):
    mod = load_example(EXAMPLE)
    decoded = [
        decode_substrate(raw, mod.HYPERCORE_MARKET)
        for raw in mod.market_substrates(VAULT)
    ]
    assert [(i.type_label, i.address, i.extra) for i in decoded] == [
        ("SPOT_TOKEN", mod.HYPEREVM_USDC.lower(), {"token_index": "0"}),
        (
            "PERP_MARKET",
            None,
            {
                "asset": "110002",
                "max_notional_usd6": "15000000",
                "reduce_only_required": "false",
                "dex": "1",
                "read_index": "10002",
            },
        ),
        ("DESTINATION", VAULT.lower(), {}),
        ("DESTINATION", USDC_SYSTEM_ADDRESS.lower(), {}),
        ("SEND_CAP", None, {"token_index": "0", "max_wei": "10000000000"}),
        ("CONFIG", None, {"key": "PerpDexIds", "value": "2"}),
        ("CONFIG", None, {"key": "NavJumpFloorUsd6", "value": "1000000"}),
    ]


def test_pre_hook_wiring_covers_the_eight_selectors_once(load_example):
    mod = load_example(EXAMPLE)
    selectors, implementations, substrates = mod.pre_hook_wiring()
    assert [s.hex() for s in selectors] == [
        "6e553f65",
        "50921b23",
        "94bf804d",
        "b460af94",
        "ba087652",
        "a64099d2",
        "baae8abf",
        "e9a2e778",
    ]
    assert (
        implementations
        == [mod.CAPITAL_FLOW_PRE_HOOK] * 6 + [mod.PENDING_ACTION_PRE_HOOK] * 2
    )
    assert substrates == [[]] * 8


def test_core_steps_encode_the_granted_route(load_example):
    mod = load_example(EXAMPLE)
    steps = mod.core_steps(VAULT)
    assert [step.action.fuse for step in steps] == [
        mod.DEPOSIT_FUSE,
        mod.SEND_FUSE,
        mod.ORDER_FUSE,
        mod.ORDER_FUSE,
        mod.SEND_FUSE,
        mod.SEND_FUSE,
    ]
    bridge, to_dex, buy, close, from_dex, to_evm = (step.action.data for step in steps)
    assert _args(bridge, "enter((uint64,uint256))") == (0, 15_000_000)
    vault = VAULT.lower()
    assert _args(to_dex, SEND_ASSET) == (vault, SPOT_DEX, 1, 0, 1_000_000_000)
    assert _args(buy, ORDER) == (
        110_002,
        True,
        230 * 10**8,
        5_000_000,
        False,
        TimeInForce.IOC,
        1,
    )
    assert _args(close, ORDER) == (
        110_002,
        False,
        220 * 10**8,
        5_000_000,
        True,
        TimeInForce.IOC,
        2,
    )
    assert _args(from_dex, SEND_ASSET) == (vault, 1, SPOT_DEX, 0, 1_000_000_000)
    assert _args(to_evm, SEND_ASSET) == (
        USDC_SYSTEM_ADDRESS.lower(),
        SPOT_DEX,
        SPOT_DEX,
        0,
        1_395_000_000,
    )


def test_order_notional_stays_under_the_granted_cap(load_example):
    mod = load_example(EXAMPLE)
    for price in (mod.LIMIT_BUY_PX, 225 * 10**8):
        assert mod.ORDER_SIZE * price // 10**8 // 100 <= mod.MAX_ORDER_NOTIONAL_USD6


def test_core_ledger_is_the_route_the_steps_assume(load_example):
    mod = load_example(EXAMPLE)
    ledger = mod.core_ledger()
    assert len(ledger) == 7
    assert not ledger[0].account_exists
    assert ledger[1].spot_usdc_wei == 15 * 10**8 - mod.ACTIVATION_FEE_WEI
    assert ledger[2].spot_usdc_wei + ledger[2].xyz_account_value_usd6 * 100 == (
        ledger[1].spot_usdc_wei
    )
    assert ledger[3].position_szi == 50 and ledger[4].position_szi == 0
    assert ledger[5] == ledger[6] and ledger[5].spot_usdc_wei == ledger[1].spot_usdc_wei


@pytest.mark.parametrize(
    ("spot", "expected"),
    [(1_400_000_000, 1_395_000_000), (5_000_150, 100), (4_000_000, 0)],
)
def test_core_to_evm_amount_keeps_the_reserve_and_evm_precision(
    load_example, spot, expected
):
    mod = load_example(EXAMPLE)
    assert mod.core_to_evm_amount(spot) == expected


def _usdc_info(extra_decimals: int) -> HyperCoreTokenInfo:
    zero = Web3.to_checksum_address("0x" + "00" * 20)
    return HyperCoreTokenInfo("USDC", (), 0, zero, zero, 8, 8, extra_decimals)


def test_core_metadata_check_accepts_core_and_rejects_drift(load_example):
    mod = load_example(EXAMPLE)
    nvda = HyperCorePerpAssetInfo("xyz:NVDA", 20, 3, 20, False)
    mod.check_core_metadata(_usdc_info(-2), mod.HYPEREVM_USDC, nvda)

    with pytest.raises(AssertionError, match="evmExtraWeiDecimals"):
        mod.check_core_metadata(_usdc_info(0), mod.HYPEREVM_USDC, nvda)
    with pytest.raises(AssertionError, match="token"):
        mod.check_core_metadata(_usdc_info(-2), mod.OWNER, nvda)
    with pytest.raises(AssertionError, match="szDecimals"):
        mod.check_core_metadata(
            _usdc_info(-2),
            mod.HYPEREVM_USDC,
            HyperCorePerpAssetInfo("xyz:NVDA", 20, 2, 20, False),
        )
    with pytest.raises(AssertionError, match="read index"):
        mod.check_core_metadata(
            _usdc_info(-2),
            mod.HYPEREVM_USDC,
            HyperCorePerpAssetInfo("xyz:AAPL", 20, 3, 20, False),
        )

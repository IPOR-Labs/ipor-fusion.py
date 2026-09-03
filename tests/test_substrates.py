"""Tests for the public substrate decode API (ipor_fusion.substrates).

The ASYNC_ACTION fixtures are the live substrate set granted on Ethereum
mainnet by tx 0x9321732ec456e44a46d6f10ba9eee956333c13f5a45fec3bd242218ce8c3ce93
(TESS USDe sUSDe Loop Vault, market 40, 2026-08-19).
"""

from __future__ import annotations

import pytest

from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.substrates import SubstrateInfo, decode_substrate, market_name

SUSDE = "0x9d39a5de30e57443bff2a8307a4256c8797a3497"
USDE = "0x4c9edd5852cd905f086c759e8383e09bff1e68b3"

ASYNC_AMOUNT_TO_OUTSIDE = (
    "0x009d39a5de30e57443bff2a8307a4256c8797a349701a784379d99db42000000"
)
ASYNC_TARGET_COOLDOWN_SHARES = (
    "0x01000000000000009d39a5de30e57443bff2a8307a4256c8797a34979343d9e1"
)
ASYNC_TARGET_UNSTAKE_USDE = (
    "0x01000000000000004c9edd5852cd905f086c759e8383e09bff1e68b3f2888dbb"
)
ASYNC_EXIT_SLIPPAGE = (
    "0x02000000000000000000000000000000000000000000000000038d7ea4c68000"
)

# Live Aave V4 substrate granted on Ethereum mainnet (ETH-weETH Liquidity
# Optimizer 0x7fd6b3b8…, market 49, 2026-09-01): Reserve on spoke
# 0x94e7a5dc… (reserve 0 = WETH), supply-only.
AAVE_V4_SPOKE = "0x94e7a5dcbe816e498b89ab752661904e2f56c485"
AAVE_V4_WETH_SUPPLY_ONLY = (
    "0x0194e7a5dcbe816e498b89ab752661904e2f56c4850000000000000000000000"
)


def test_async_action_allowed_amount_to_outside():
    info = decode_substrate(ASYNC_AMOUNT_TO_OUTSIDE, market_id=40)
    assert info.type_label == "ALLOWED_AMOUNT_TO_OUTSIDE"
    assert info.address == SUSDE
    assert info.extra == {"amount": str(2_000_000 * 10**18)}
    assert not info.is_error


def test_async_action_allowed_targets():
    info = decode_substrate(ASYNC_TARGET_COOLDOWN_SHARES, market_id=40)
    assert info.type_label == "ALLOWED_TARGETS"
    assert info.address == SUSDE
    assert info.extra == {"selector": "0x9343d9e1"}  # cooldownShares(uint256)

    info = decode_substrate(ASYNC_TARGET_UNSTAKE_USDE, market_id=40)
    assert info.address == USDE
    assert info.extra == {"selector": "0xf2888dbb"}  # unstake(address)


def test_async_action_allowed_exit_slippage():
    info = decode_substrate(ASYNC_EXIT_SLIPPAGE, market_id=40)
    assert info.type_label == "ALLOWED_EXIT_SLIPPAGE"
    assert info.address == ""
    assert info.extra == {"slippage": str(10**15)}  # 0.1% WAD


def test_async_action_unknown_type_byte_falls_back_to_raw():
    raw = "0x07" + "00" * 31
    info = decode_substrate(raw, market_id=40)
    assert info.type_label == "type=7"
    assert info.raw_hex == raw
    assert info.address == ""


def test_async_action_not_decoded_as_plain_address():
    """Regression: market 40 used to run through the plain-address decoder,
    rendering the low 20 bytes (amount tail included) as a bogus address."""
    info = decode_substrate(ASYNC_AMOUNT_TO_OUTSIDE, market_id=40)
    assert info.address != "0x" + ASYNC_AMOUNT_TO_OUTSIDE[-40:]


def test_aave_v4_reserve():
    info = decode_substrate(AAVE_V4_WETH_SUPPLY_ONLY, market_id=49)
    assert info.type_label == "AAVE_V4_RESERVE"
    assert info.address == AAVE_V4_SPOKE
    assert info.extra == {
        "reserve_id": "0",
        "is_collateral": "False",
        "can_borrow": "False",
    }
    assert not info.is_error


def test_aave_v4_reserve_id_and_flags():
    # spoke | reserveId=7 | flags=0x03 (isCollateral + canBorrow)
    raw = "0x01" + SUSDE[2:] + "00000007" + "03" + "00" * 6
    info = decode_substrate(raw, market_id=49)
    assert info.address == SUSDE
    assert info.extra == {
        "reserve_id": "7",
        "is_collateral": "True",
        "can_borrow": "True",
    }


def test_aave_v4_non_reserve_types_stay_raw():
    undefined = "0x" + "00" * 32
    info = decode_substrate(undefined, market_id=49)
    assert info.type_label == "Undefined"
    assert info.address == ""

    unknown = "0x05" + "00" * 31
    info = decode_substrate(unknown, market_id=49)
    assert info.type_label == "type=5"
    assert info.address == ""


def test_aave_v4_not_decoded_from_the_low_bytes():
    """Regression: market 49 used to run through the generic type<<248 helper
    (address in the low 20 bytes), rendering a left-aligned Reserve word as a
    garbage address built from the reserveId/flags/padding tail."""
    info = decode_substrate(AAVE_V4_WETH_SUPPLY_ONLY, market_id=49)
    assert info.address != "0x" + AAVE_V4_WETH_SUPPLY_ONLY[-40:]


def test_bytes_and_hex_str_inputs_are_equivalent():
    raw_hex = ASYNC_AMOUNT_TO_OUTSIDE
    from_bytes = decode_substrate(bytes.fromhex(raw_hex[2:]), market_id=40)
    from_prefixed = decode_substrate(raw_hex, market_id=40)
    from_bare = decode_substrate(raw_hex[2:], market_id=40)
    assert from_bytes == from_prefixed == from_bare


def test_plain_address_market():
    raw = "0x" + "00" * 12 + SUSDE[2:]
    info = decode_substrate(raw, market_id=1)
    assert info == SubstrateInfo(address=SUSDE)


def test_morpho_market_is_raw():
    raw = "0x" + "ab" * 32
    info = decode_substrate(raw, market_id=14)
    assert info.type_label == "morpho_market_id"
    assert info.raw_hex == raw


def test_morpho_flash_loan_substrate_is_a_token_address():
    # MorphoFlashLoanFuse gates the loan token via isSubstrateAsAssetGranted;
    # the substrate is the plain address form, never a Morpho market id
    raw = "0x" + "00" * 12 + USDE[2:]
    assert decode_substrate(raw, market_id=19) == SubstrateInfo(address=USDE)


@pytest.mark.parametrize(
    ("raw", "label"),
    [
        ("0x01" + "00" * 11 + SUSDE[2:], "Token"),
        ("0x02" + "00" * 11 + SUSDE[2:], "Target"),
        ("0x03" + "00" * 24 + "2386f26fc10000", "Slippage"),
    ],
)
def test_universal_token_swapper_v2_shares_the_v1_layout(raw: str, label: str):
    v2 = decode_substrate(raw, market_id=IporFusionMarkets.UNIVERSAL_TOKEN_SWAPPER_V2)
    assert v2 == decode_substrate(raw, market_id=12)
    assert v2.type_label == label
    assert not v2.is_error


# Live Uniswap V4 PoolId granted on Ethereum mainnet (rETH Liquity LP Carry
# 0xb9e806e8…, market 53): the BOLD/USDC 0.05% pool, no hook.
UNISWAP_V4_BOLD_USDC = (
    "0x5d0ed52610c76d7bf729130ce7ddc0488b2f4bd0a0db1f12adbe6a32deaff893"
)
BOLD = "0x6440f144b7e50d6a8439336510312d2f54beb01d"


def test_uniswap_v4_pool_id_is_labelled_raw():
    info = decode_substrate(UNISWAP_V4_BOLD_USDC, market_id=53)
    assert info.type_label == "uniswap_v4_pool_id"
    assert info.raw_hex == UNISWAP_V4_BOLD_USDC
    assert info.address == ""


def test_uniswap_v4_pool_currency_is_a_plain_address():
    raw = "0x" + "00" * 12 + BOLD[2:]
    info = decode_substrate(raw, market_id=53)
    assert info == SubstrateInfo(address=BOLD, type_label="uniswap_v4_token")


def test_market_ids_52_and_53_follow_ipor_fusion_markets_sol():
    assert IporFusionMarkets.TERM_FINANCE == 52
    assert IporFusionMarkets.UNISWAP_V4 == 53
    assert market_name(52) == "TERM_FINANCE"
    assert market_name(53) == "UNISWAP_V4"
    # no substrate library mirrored for Term Finance yet: loud, never guessed
    info = decode_substrate("0x" + "11" * 32, market_id=52)
    assert info.address == ""
    assert info.type_label == "no_decoder(TERM_FINANCE)"


def test_market_without_decoder_is_labelled_not_guessed():
    info = decode_substrate("0x" + "11" * 32, market_id=31)
    assert info.address == ""
    assert info.type_label == "no_decoder(VELODROME_SUPERCHAIN)"


def test_external_state_is_canonical_name_and_rwa_is_alias():
    # Market 50 is EXTERNAL_STATE; RWA remains a backward-compatible alias of
    # the same value, but the canonical name is what id->name lookups display.
    assert IporFusionMarkets.EXTERNAL_STATE == 50
    assert IporFusionMarkets.RWA == IporFusionMarkets.EXTERNAL_STATE
    info = decode_substrate("0x" + "11" * 32, market_id=50)
    assert info.type_label == "no_decoder(EXTERNAL_STATE)"


def test_no_market_context_returns_raw():
    info = decode_substrate("0x" + "22" * 32)
    assert info == SubstrateInfo(raw_hex="0x" + "22" * 32)


@pytest.mark.parametrize(
    "bad",
    ["0x1234", "0x" + "00" * 33, "not-hex", b"\x00" * 31],
)
def test_wrong_length_or_malformed_input_is_error(bad: str | bytes):
    assert decode_substrate(bad, market_id=1).is_error


def test_market_id_registrations_follow_ipor_fusion_markets_sol():
    """Regression for stale registrations: DOLOMITE=47 (not 46), NAPIER=46
    (plain assets), SPARK_LEND=44 (reuses AaveV3SupplyFuse, plain assets)."""
    dolomite = "0x" + SUSDE[2:] + "0201" + "00" * 10
    info = decode_substrate(dolomite, market_id=47)
    assert info.address == SUSDE
    assert info.extra == {"sub_account_id": "2", "can_borrow": "True"}

    napier = "0x" + "00" * 12 + SUSDE[2:]
    assert decode_substrate(napier, market_id=46) == SubstrateInfo(address=SUSDE)

    spark = "0x" + "00" * 12 + SUSDE[2:]
    assert decode_substrate(spark, market_id=44) == SubstrateInfo(address=SUSDE)

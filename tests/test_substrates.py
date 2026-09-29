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


# The EXTERNAL_STATE substrates below are built from the layout documented in
# ExternalStateSubstrateLib.sol; the live vaults granting them sit on HyperEVM,
# which the SDK does not reach, so there is no on-chain fixture to cite.
def test_external_state_address_types():
    for type_byte, label in ((1, "ASSET"), (3, "CUSTODIAN"), (4, "BALANCE_ACCOUNT")):
        raw = f"0x0{type_byte}" + "00" * 11 + SUSDE[2:]
        info = decode_substrate(raw, market_id=50)
        assert info.type_label == label
        assert info.address == SUSDE
        assert not info.is_error


def test_external_state_target():
    # transfer(address,uint256) allowed on USDe: selector above the address.
    raw = "0x02" + "00" * 7 + "a9059cbb" + USDE[2:]
    info = decode_substrate(raw, market_id=50)
    assert info.type_label == "TARGET"
    assert info.address == USDE
    assert info.extra == {"selector": "0xa9059cbb"}


def test_external_state_scalar_guards():
    # The extra key names the unit: the four guards share a payload shape but
    # measure seconds, basis points and percent of one token respectively.
    for type_byte, label, unit in (
        (5, "STALENESS_MAX", "seconds"),
        (6, "BIG_CHANGE_BPS", "bps"),
        (7, "DUST_THRESHOLD", "percent"),
        (8, "MIN_UPDATE_INTERVAL", "seconds"),
    ):
        raw = f"0x0{type_byte}" + f"{3600:062x}"
        info = decode_substrate(raw, market_id=50)
        assert info.type_label == label
        assert info.address == ""
        assert info.extra == {unit: "3600"}


def test_external_state_unknown_type_byte_falls_back_to_raw():
    raw = "0x09" + "00" * 31
    info = decode_substrate(raw, market_id=50)
    assert info.type_label == "type=9"
    assert info.raw_hex == raw
    assert info.address == ""


def test_external_state_undefined_type_is_named():
    # Type 0 is the enum's invalid member; it must not render an address.
    raw = "0x00" + "00" * 11 + SUSDE[2:]
    info = decode_substrate(raw, market_id=50)
    assert info.type_label == "UNDEFINED"
    assert info.raw_hex == raw
    assert info.address == ""


def test_external_state_and_async_action_target_layouts_are_mirrored():
    """Market 40 packs TARGET as target<<32 | selector; market 50 inverts it
    (selector<<160 | target). The same (target, selector) pair is therefore a
    different bytes32 per market, and reading one at the other's offsets
    recovers a garbage address."""
    # The market-40 side is the live fixture; only its market-50 counterpart
    # is built here, from the same (target, selector) pair.
    selector = "f2888dbb"  # unstake(address)
    async_action = ASYNC_TARGET_UNSTAKE_USDE
    external_state = "0x02" + "00" * 7 + selector + USDE[2:]
    assert external_state != async_action

    es = decode_substrate(external_state, market_id=50)
    aa = decode_substrate(async_action, market_id=40)
    assert es.address == aa.address == USDE
    assert es.extra["selector"] == aa.extra["selector"] == f"0x{selector}"

    # A market-40 TARGET read at market-50 offsets yields the target's tail
    # concatenated with the selector -- a well-formed, wrong address.
    crossed = decode_substrate(async_action, market_id=50)
    assert crossed.type_label == "ASSET"
    assert crossed.address == f"0x{USDE[10:]}{selector}"


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
    assert market_name(50) == "EXTERNAL_STATE"


def test_spol_unstake_follows_ipor_fusion_markets_sol():
    assert IporFusionMarkets.SPOL_UNSTAKE == 424_243
    assert market_name(424_243) == "SPOL_UNSTAKE"
    # SPOLUnstakeFuse is not in the public contracts repo, so its substrate
    # grant check cannot be verified: loud, never guessed
    info = decode_substrate("0x" + "11" * 32, market_id=424_243)
    assert info.address == ""
    assert info.type_label == "no_decoder(SPOL_UNSTAKE)"


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


# The 12 words granted on the first HyperEVM HyperCore test vault
# (0x41c46c32…, run of 2026-09-28; see tests/fixtures/hypercore_hip3_flow.json).
HYPERCORE_USDC = "0xb88339cb7199b77e23db6e890353e22632ba630f"
HYPERCORE_WORDS = {
    "spot_token": "0x010000000000000000000000b88339cb7199b77e23db6e890353e22632ba630f",
    "perp_market": "0x020001adb20000000000000000e4e1c000000000000000000000000000000000",
    "destination": "0x03000000000000000000000041c46c328036fa7865d5cb15d531d7c5120f05c8",
    "send_cap": "0x06000000000000000000000000000000000000000000000000000002540be400",
    "config_dex_ids": "0x0504000000000000000000000000000000000000000000000000000000000002",
    "config_mode": "0x0505000000000000000000000000000000000000000000000000000000000001",
    "builder": "0x040000000000000000000001cee5c4272e246a424aede992c987966736e0f63b",
}


def test_hypercore_typed_substrates():
    market = IporFusionMarkets.HYPERCORE
    assert market_name(market) == "HYPERCORE"

    spot = decode_substrate(HYPERCORE_WORDS["spot_token"], market_id=market)
    assert spot == SubstrateInfo(
        address=HYPERCORE_USDC, type_label="SPOT_TOKEN", extra={"token_index": "0"}
    )

    perp = decode_substrate(HYPERCORE_WORDS["perp_market"], market_id=market)
    assert perp.type_label == "PERP_MARKET"
    assert perp.extra == {
        "asset": "110002",
        "max_notional_usd6": "15000000",
        "reduce_only_required": "false",
    }

    dest = decode_substrate(HYPERCORE_WORDS["destination"], market_id=market)
    assert dest == SubstrateInfo(
        address="0x41c46c328036fa7865d5cb15d531d7c5120f05c8", type_label="DESTINATION"
    )

    cap = decode_substrate(HYPERCORE_WORDS["send_cap"], market_id=market)
    assert cap.type_label == "SEND_CAP"
    assert cap.extra == {"token_index": "0", "max_wei": str(100 * 10**8)}

    dex_ids = decode_substrate(HYPERCORE_WORDS["config_dex_ids"], market_id=market)
    assert dex_ids.type_label == "CONFIG"
    assert dex_ids.extra == {"key": "PerpDexIds", "value": "2"}

    mode = decode_substrate(HYPERCORE_WORDS["config_mode"], market_id=market)
    assert mode.extra == {"key": "SettlementMode", "value": "1", "mode": "TIMING"}

    builder = decode_substrate(HYPERCORE_WORDS["builder"], market_id=market)
    assert builder.address == "0xcee5c4272e246a424aede992c987966736e0f63b"
    assert builder.extra == {"max_fee_rate_decibps": "1"}


@pytest.mark.parametrize(
    ("word", "label"),
    [
        # SpotToken with bits 247..224 set
        (
            "0x01"
            + "ff0000"
            + "0000000000000000"
            + "b88339cb7199b77e23db6e890353e22632ba630f",
            "SPOT_TOKEN",
        ),
        # PerpMarket with the low 120 bits set
        ("0x020001adb20000000000000000e4e1c000" + "01" + "00" * 14, "PERP_MARKET"),
        # PerpMarket with a reduce-only flag of 2
        ("0x020001adb20000000000000000e4e1c002" + "00" * 15, "PERP_MARKET"),
        # Destination with bits above the address set
        (
            "0x03" + "01" + "00" * 10 + "41c46c328036fa7865d5cb15d531d7c5120f05c8",
            "DESTINATION",
        ),
        # Builder with bits 247..224 set
        (
            "0x04"
            + "000001"
            + "0000000000000001"
            + "cee5c4272e246a424aede992c987966736e0f63b",
            "BUILDER",
        ),
        # SendCap with bits 247..192 set
        (
            "0x06"
            + "00000000000001"
            + "0000000000000000"
            + hex(100 * 10**8)[2:].rjust(32, "0"),
            "SEND_CAP",
        ),
        # tags the library has no member for
        ("0x00" + "00" * 31, "type=0"),
        ("0x07" + "00" * 31, "type=7"),
        # a config key outside 1..6
        ("0x0509" + "00" * 30, "CONFIG key=9"),
    ],
)
def test_hypercore_words_the_library_rejects_are_errors(word, label):
    assert len(word) == 66, word
    info = decode_substrate(word, market_id=IporFusionMarkets.HYPERCORE)
    assert info.is_error
    assert info.type_label == label
    assert info.address == ""
    assert info.raw_hex == word
    assert info.extra == {"error": "invalid HyperCore substrate"}

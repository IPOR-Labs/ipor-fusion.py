"""The `hypercore` block of `vault info`: the CLI producer's dict validates
against the MCP model, field by field."""

from __future__ import annotations

from unittest.mock import MagicMock

from web3 import Web3

from ipor_fusion.cli.vault_cmd import _build_hypercore_json
from ipor_fusion.mcp.models import HyperCoreSection, VaultInfoResponse
from ipor_fusion.readers.hypercore import (
    HyperCoreActionClass,
    HyperCoreNav,
    HyperCorePendingState,
    HyperCorePerpLeg,
    HyperCorePerpMarket,
    HyperCoreReportedResult,
    HyperCoreSpotLeg,
    HyperCoreVaultState,
)
from ipor_fusion.types import Amount, Decimals, MarketId, Price

USDC = Web3.to_checksum_address("0xb88339cb7199b77e23db6e890353e22632ba630f")
FUSE = Web3.to_checksum_address("0x66f605b579dbe23c552edcf480de71198f3c4cd1")


def _state(*, pending: HyperCorePendingState | None) -> HyperCoreVaultState:
    nav = HyperCoreNav(
        core_user_exists=True,
        perp_dex_bitmap=2,
        spot=(
            HyperCoreSpotLeg(
                0,
                USDC,
                824_500,
                0,
                8,
                Price(USDC, Amount(10**8), Decimals(8)),
                8_245_000_000_000_000,
            ),
        ),
        native_perp=HyperCorePerpLeg(0, 0, 0),
        hip3=(HyperCorePerpLeg(1, -5, -5 * 10**12),),
        value_wad=8_245_000_000_000_000 - 5 * 10**12,
    )
    return HyperCoreVaultState(
        market_id=MarketId(55),
        balance_fuse=FUSE,
        nav=nav,
        balance_fuse_value_wad=nav.value_wad,
        pending=pending,
        perp_markets=(
            HyperCorePerpMarket(110_002, 1, 10_002, "xyz:NVDA", 15_000_000, False),
        ),
    )


PENDING = HyperCorePendingState(
    pending_until=1_790_609_642,
    enqueued_l1_block=1_164_341_828,
    enqueued_evm_block=47_125_670,
    action_class=HyperCoreActionClass.TRANSFER,
    settlement_mode=None,
    reported_result=HyperCoreReportedResult.NONE,
    result_l1_block=0,
    action_id=13,
    payload_hash=b"\x30" * 32,
    refreshing=False,
    cached_value_wad=10**16,
    action_nonce=32,
    pending=False,
    settled=True,
    current_l1_block=1_168_823_034,
    current_timestamp=1_790_931_001,
    l1_block_available=True,
)


def test_producer_dict_validates_against_the_model():
    payload = _build_hypercore_json(MagicMock(hypercore=_state(pending=PENDING)))
    assert payload is not None
    section = HyperCoreSection.model_validate(payload)
    assert section.market == "HYPERCORE" and section.market_id == 55
    assert section.nav_wad == 8_245_000_000_000_000 - 5 * 10**12
    assert section.nav_matches_balance_fuse is True
    assert section.spot[0].price_usd == 1.0 and section.spot[0].value_usd == 0.008245
    assert section.hip3[0].account_value_usd6 == -5
    assert section.perp_markets[0].coin == "xyz:NVDA"
    assert section.pending is not None
    assert section.pending.action_class == "TRANSFER"
    assert section.pending.settlement_mode is None
    assert section.pending.pending_until_utc == "2026-09-28T15:34:02Z"


def test_unknown_pending_reader_and_absent_market_are_null():
    payload = _build_hypercore_json(MagicMock(hypercore=_state(pending=None)))
    assert payload is not None and payload["pending"] is None
    HyperCoreSection.model_validate(payload)
    assert _build_hypercore_json(MagicMock(hypercore=None)) is None
    assert "hypercore" in VaultInfoResponse.model_fields

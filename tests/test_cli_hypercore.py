"""`vault info` on a HyperCore vault: the text block and the fetch gating."""

from __future__ import annotations

from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from test_mcp_hypercore import FUSE, PENDING, USDC, _state
from web3 import Web3

from ipor_fusion.cli.vault_info_view import (
    _print_hypercore,
)
from ipor_fusion.core.plasma_vault import BalanceFuse
from ipor_fusion.readers.hypercore import HyperCoreActionClass, HyperCoreEffectKind
from ipor_fusion.types import MarketId
from ipor_fusion.vault_data import _fetch_hypercore_state

VAULT = Web3.to_checksum_address("0x41c46c328036fa7865d5cb15d531d7c5120f05c8")


def test_text_block_lists_nav_legs_markets_and_pending(capsys):
    _print_hypercore(MagicMock(hypercore=_state(pending=PENDING)))
    out = capsys.readouterr().out
    assert (
        out.startswith("HyperCore (HYPERCORE (55)):")
        or "HYPERCORE" in out.splitlines()[0]
    )
    assert "NAV: 0.008240 USD | balance fuse: 0.008240 USD (match)" in out
    assert "Spot token 0" in out and "824500 wei (8 dec)" in out
    assert "HIP-3 dex 1: -0.000005 USD" in out
    assert (
        "Perp market xyz:NVDA (asset 110002, dex 1, read index 10002): cap 15.00 USD"
        in out
    )
    assert (
        "Pending action: none (last TRANSFER #32 settled); L1 block 1168823034" in out
    )


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        (
            {"action_pending": True, "action_settled": False},
            "Pending action: TRANSFER #32 (id 13) until 2026-09-28T15:34:02Z;"
            " L1 block 1168823034",
        ),
        (
            {
                "action_pending": True,
                "action_settled": False,
                "effect_kind": HyperCoreEffectKind.CORE_SPOT,
                "expected_balance": 5_000_000_000,
                "timeout_at": 1_790_609_822,
            },
            "until 2026-09-28T15:34:02Z, awaiting Core spot token 0 >= 5000000000 wei"
            " or timeout 2026-09-28T15:37:02Z; L1 block 1168823034",
        ),
        (
            {
                "action_pending": True,
                "action_settled": False,
                "effect_kind": HyperCoreEffectKind.EVM_BALANCE,
                "effect_evm_asset": USDC,
                "expected_balance": 7,
                "timeout_at": 1_790_609_822,
            },
            f"awaiting EVM {USDC} >= 7 units or timeout 2026-09-28T15:37:02Z;",
        ),
        (
            {"action_class": HyperCoreActionClass.NONE},
            "Pending action: none (last action #32 cleared); L1 block 1168823034",
        ),
        (
            {"action_class": HyperCoreActionClass.NONE, "action_nonce": 0},
            "Pending action: none (no action yet); L1 block 1168823034",
        ),
    ],
)
def test_pending_line_follows_the_record(capsys, changes, expected):
    _print_hypercore(MagicMock(hypercore=_state(pending=replace(PENDING, **changes))))
    out = capsys.readouterr().out
    assert expected in out
    assert "Last order" not in out


@pytest.mark.parametrize(
    ("changes", "expected"),
    [
        ({"last_order_l1_block": 1_164_341_700}, "L1 block 1164341700\n"),
        ({"last_order_l1_block": 1_168_823_034}, "L1 block 1168823034 (in flight)\n"),
        (
            {"last_order_l1_block": 1_168_823_034, "l1_block_available": False},
            "L1 block 1168823034\n",
        ),
    ],
)
def test_last_order_line_flags_an_order_in_flight(capsys, changes, expected):
    _print_hypercore(MagicMock(hypercore=_state(pending=replace(PENDING, **changes))))
    assert f"  Last order: {expected}" in capsys.readouterr().out


def test_text_block_without_a_reader_says_so(capsys):
    _print_hypercore(MagicMock(hypercore=_state(pending=None)))
    assert "Pending action: unknown" in capsys.readouterr().out
    _print_hypercore(MagicMock(hypercore=None))
    assert capsys.readouterr().out == ""


def _markets(*fuses: BalanceFuse) -> MagicMock:
    return MagicMock(balance_fuses=list(fuses))


@patch("ipor_fusion.vault_data.read_hypercore_vault_state")
def test_fetch_only_on_hyperevm_with_a_hypercore_balance_fuse(read_state):
    ctx = MagicMock(chain_id=999)
    hypercore = BalanceFuse(MarketId(55), FUSE)
    assert (
        _fetch_hypercore_state(MagicMock(chain_id=1), VAULT, _markets(hypercore))
        is None
    )
    assert (
        _fetch_hypercore_state(ctx, VAULT, _markets(BalanceFuse(MarketId(14), FUSE)))
        is None
    )
    read_state.assert_not_called()

    read_state.return_value = _state(pending=None)
    assert (
        _fetch_hypercore_state(ctx, VAULT, _markets(hypercore))
        is read_state.return_value
    )
    read_state.assert_called_once_with(ctx, VAULT, FUSE)

    read_state.side_effect = ValueError("unsupported HIP-3 perp dex bitmap 0x4")
    assert _fetch_hypercore_state(ctx, VAULT, _markets(hypercore)) is None

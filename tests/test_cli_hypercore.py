"""`vault info` on a HyperCore vault: the text block and the fetch gating."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from test_mcp_hypercore import FUSE, PENDING, _state
from web3 import Web3

from ipor_fusion.cli.vault_cmd import _print_hypercore
from ipor_fusion.cli.vault_fetcher import _fetch_hypercore_state
from ipor_fusion.core.plasma_vault import BalanceFuse
from ipor_fusion.types import MarketId

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


def test_text_block_without_a_reader_says_so(capsys):
    _print_hypercore(MagicMock(hypercore=_state(pending=None)))
    assert "Pending action: unknown" in capsys.readouterr().out
    _print_hypercore(MagicMock(hypercore=None))
    assert capsys.readouterr().out == ""


def _markets(*fuses: BalanceFuse) -> MagicMock:
    return MagicMock(balance_fuses=list(fuses))


@patch("ipor_fusion.cli.vault_fetcher.read_hypercore_vault_state")
def test_fetch_only_on_hyperevm_with_a_hypercore_balance_fuse(read_state):
    ctx = MagicMock()
    hypercore = BalanceFuse(MarketId(55), FUSE)
    assert _fetch_hypercore_state(ctx, VAULT, 1, _markets(hypercore)) is None
    assert (
        _fetch_hypercore_state(
            ctx, VAULT, 999, _markets(BalanceFuse(MarketId(14), FUSE))
        )
        is None
    )
    read_state.assert_not_called()

    read_state.return_value = _state(pending=None)
    assert (
        _fetch_hypercore_state(ctx, VAULT, 999, _markets(hypercore))
        is read_state.return_value
    )
    read_state.assert_called_once_with(ctx, VAULT, FUSE)

    read_state.side_effect = ValueError("unsupported HIP-3 perp dex bitmap 0x4")
    assert _fetch_hypercore_state(ctx, VAULT, 999, _markets(hypercore)) is None

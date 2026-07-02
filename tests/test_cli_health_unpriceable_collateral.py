"""Unit tests for the unpriceable-Morpho-collateral health critical.

Exercises the pure helper ``_compute_unpriceable_collateral_criticals`` with
duck-typed fixtures — it only reads ``data.morpho_positions`` (a dict of
market_id -> breakdowns with a ``collateral_token``) and
``data.asset_price_sources`` (lowercase token address -> feed source), so no
chain access or full ``_VaultData`` construction is needed.
"""

from types import SimpleNamespace

from ipor_fusion.cli.vault_health import _compute_unpriceable_collateral_criticals

_FEED = "0x1111111111111111111111111111111111111111"
_ZERO = "0x0000000000000000000000000000000000000000"
_CB_BTC = "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"
_WETH = "0x4200000000000000000000000000000000000006"


def _breakdown(collateral_token: str) -> SimpleNamespace:
    return SimpleNamespace(collateral_token=collateral_token)


def _data(morpho_positions, asset_price_sources) -> SimpleNamespace:
    return SimpleNamespace(
        morpho_positions=morpho_positions,
        asset_price_sources=asset_price_sources,
    )


def test_flags_collateral_without_feed():
    data = _data({14: [_breakdown(_CB_BTC)]}, {_CB_BTC.lower(): _ZERO})
    criticals = _compute_unpriceable_collateral_criticals(data)
    assert len(criticals) == 1
    assert _CB_BTC in criticals[0]
    assert "supplyCollateral" in criticals[0]


def test_no_flag_when_feed_present():
    data = _data({14: [_breakdown(_WETH)]}, {_WETH.lower(): _FEED})
    assert _compute_unpriceable_collateral_criticals(data) == []


def test_skips_when_source_unknown():
    # Transient read failure -> token omitted from sources -> not a false alarm.
    data = _data({14: [_breakdown(_CB_BTC)]}, {})
    assert _compute_unpriceable_collateral_criticals(data) == []


def test_dedupes_same_collateral_across_markets():
    data = _data(
        {14: [_breakdown(_CB_BTC)], 15: [_breakdown(_CB_BTC)]},
        {_CB_BTC.lower(): _ZERO},
    )
    assert len(_compute_unpriceable_collateral_criticals(data)) == 1


def test_mixed_feeds_flag_only_missing():
    data = _data(
        {14: [_breakdown(_WETH), _breakdown(_CB_BTC)]},
        {_WETH.lower(): _FEED, _CB_BTC.lower(): _ZERO},
    )
    criticals = _compute_unpriceable_collateral_criticals(data)
    assert len(criticals) == 1
    assert _CB_BTC in criticals[0]
    assert _WETH not in criticals[0]


def test_empty_when_no_morpho_positions_or_sources():
    assert _compute_unpriceable_collateral_criticals(_data(None, {_ZERO: _ZERO})) == []
    assert (
        _compute_unpriceable_collateral_criticals(
            _data({14: [_breakdown(_CB_BTC)]}, None)
        )
        == []
    )

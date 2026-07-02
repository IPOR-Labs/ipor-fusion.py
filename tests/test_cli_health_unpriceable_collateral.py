"""Unit tests for the unpriceable-Morpho-collateral health critical.

Exercises the pure helper ``_compute_unpriceable_collateral_criticals`` with
duck-typed fixtures — it only reads ``data.morpho_positions`` (a dict of
market_id -> breakdowns with a ``collateral_token``) and
``data.unpriceable_tokens`` (lowercase addresses confirmed unpriceable by
``_fetch_unpriceable_tokens``), so no chain access or full ``_VaultData``
construction is needed.
"""

from types import SimpleNamespace

from ipor_fusion.cli.vault_health import _compute_unpriceable_collateral_criticals

_CB_BTC = "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf"
_WETH = "0x4200000000000000000000000000000000000006"


def _breakdown(collateral_token: str) -> SimpleNamespace:
    return SimpleNamespace(collateral_token=collateral_token)


def _data(morpho_positions, unpriceable_tokens) -> SimpleNamespace:
    return SimpleNamespace(
        morpho_positions=morpho_positions,
        unpriceable_tokens=unpriceable_tokens,
    )


def test_flags_unpriceable_collateral():
    data = _data({14: [_breakdown(_CB_BTC)]}, frozenset({_CB_BTC.lower()}))
    criticals = _compute_unpriceable_collateral_criticals(data)
    assert len(criticals) == 1
    assert _CB_BTC in criticals[0]
    assert "supplyCollateral" in criticals[0]


def test_no_flag_when_priceable():
    # Priceable via explicit feed or the mainnet Chainlink registry fallback —
    # either way the token is absent from unpriceable_tokens.
    data = _data({14: [_breakdown(_WETH)]}, frozenset({_CB_BTC.lower()}))
    assert _compute_unpriceable_collateral_criticals(data) == []


def test_dedupes_same_collateral_across_markets():
    data = _data(
        {14: [_breakdown(_CB_BTC)], 15: [_breakdown(_CB_BTC)]},
        frozenset({_CB_BTC.lower()}),
    )
    assert len(_compute_unpriceable_collateral_criticals(data)) == 1


def test_mixed_flags_only_unpriceable():
    data = _data(
        {14: [_breakdown(_WETH), _breakdown(_CB_BTC)]},
        frozenset({_CB_BTC.lower()}),
    )
    criticals = _compute_unpriceable_collateral_criticals(data)
    assert len(criticals) == 1
    assert _CB_BTC in criticals[0]
    assert _WETH not in criticals[0]


def test_empty_when_no_morpho_positions_or_unpriceable_set():
    assert (
        _compute_unpriceable_collateral_criticals(
            _data(None, frozenset({_CB_BTC.lower()}))
        )
        == []
    )
    assert (
        _compute_unpriceable_collateral_criticals(
            _data({14: [_breakdown(_CB_BTC)]}, None)
        )
        == []
    )
    assert (
        _compute_unpriceable_collateral_criticals(
            _data({14: [_breakdown(_CB_BTC)]}, frozenset())
        )
        == []
    )

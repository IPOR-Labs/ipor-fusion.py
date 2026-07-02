"""Unit tests for the unpriceable middleware-priced-substrate health critical.

Two layers are covered without chain access:

- ``_compute_unpriceable_substrate_criticals`` (vault_health) with duck-typed
  fixtures — it only reads ``data.middleware_priced_substrates`` (market_id ->
  ``MiddlewarePricedToken``-shaped entries) and ``data.unpriceable_tokens``
  (lowercase addresses confirmed unpriceable by ``_fetch_unpriceable_tokens``).
- The pure (no-RPC) branches of ``_fetch_middleware_priced_substrates``
  (vault_fetcher): Dolomite substrate decoding and the Aave V4 Asset/Spoke
  gating. Euler/Silo/Aave-V3 branches need RPC and are not exercised here.
"""

from types import SimpleNamespace

from ipor_fusion.cli.vault_fetcher import (
    MiddlewarePricedToken,
    _fetch_middleware_priced_substrates,
)
from ipor_fusion.cli.vault_health import _compute_unpriceable_substrate_criticals
from ipor_fusion.market_ids import IporFusionMarkets

_ASSET_A = "0xcbb7c0000ab88b473b1f5afd9ef808440eed33bf"
_ASSET_B = "0x4200000000000000000000000000000000000006"


def _mpt(token: str, zero_balance_reverts: bool = True) -> MiddlewarePricedToken:
    return MiddlewarePricedToken(
        token=token,
        via="granted test substrate",
        zero_balance_reverts=zero_balance_reverts,
    )


def _data(middleware_priced_substrates, unpriceable_tokens) -> SimpleNamespace:
    return SimpleNamespace(
        middleware_priced_substrates=middleware_priced_substrates,
        unpriceable_tokens=unpriceable_tokens,
    )


class TestComputeUnpriceableSubstrateCriticals:
    def test_flags_unpriceable_token(self):
        data = _data({35: [_mpt(_ASSET_A)]}, frozenset({_ASSET_A}))
        criticals = _compute_unpriceable_substrate_criticals(data)
        assert len(criticals) == 1
        assert _ASSET_A in criticals[0]
        assert "even at zero balance" in criticals[0]

    def test_conditional_fuse_gets_deposit_on_behalf_wording(self):
        data = _data(
            {47: [_mpt(_ASSET_A, zero_balance_reverts=False)]},
            frozenset({_ASSET_A}),
        )
        criticals = _compute_unpriceable_substrate_criticals(data)
        assert len(criticals) == 1
        assert "deposit on the vault's behalf" in criticals[0]

    def test_no_flag_when_priceable(self):
        # Priceable via explicit feed or the mainnet Chainlink registry
        # fallback — either way absent from unpriceable_tokens.
        data = _data({35: [_mpt(_ASSET_B)]}, frozenset({_ASSET_A}))
        assert _compute_unpriceable_substrate_criticals(data) == []

    def test_dedupes_same_token_within_market(self):
        data = _data({11: [_mpt(_ASSET_A), _mpt(_ASSET_A)]}, frozenset({_ASSET_A}))
        assert len(_compute_unpriceable_substrate_criticals(data)) == 1

    def test_same_token_in_two_markets_flagged_per_market(self):
        data = _data(
            {11: [_mpt(_ASSET_A)], 35: [_mpt(_ASSET_A)]}, frozenset({_ASSET_A})
        )
        assert len(_compute_unpriceable_substrate_criticals(data)) == 2

    def test_empty_when_no_data(self):
        assert (
            _compute_unpriceable_substrate_criticals(_data(None, frozenset({_ASSET_A})))
            == []
        )
        assert (
            _compute_unpriceable_substrate_criticals(
                _data({35: [_mpt(_ASSET_A)]}, None)
            )
            == []
        )
        assert (
            _compute_unpriceable_substrate_criticals(
                _data({35: [_mpt(_ASSET_A)]}, frozenset())
            )
            == []
        )


def _dolomite_substrate(asset_hex40: str) -> bytes:
    # asset<<96 | subAccountId<<88 | canBorrow<<80
    return bytes.fromhex(asset_hex40 + "00" + "01" + "00" * 10)


def _aave_v4_substrate(flag: int, addr_hex40: str) -> bytes:
    # type flag in the MSB byte, address in the low 20 bytes
    return bytes.fromhex(f"{flag:02x}" + "00" * 11 + addr_hex40)


class TestFetchMiddlewarePricedSubstrates:
    def test_dolomite_substrate_asset_collected(self):
        subs = {IporFusionMarkets.DOLOMITE: [_dolomite_substrate("ab" * 20)]}
        result = _fetch_middleware_priced_substrates(None, None, subs, [])
        assert result is not None
        tokens = result[IporFusionMarkets.DOLOMITE]
        assert tokens == [
            MiddlewarePricedToken(
                token="0x" + "ab" * 20,
                via="granted Dolomite asset substrate",
                zero_balance_reverts=False,
            )
        ]

    def test_aave_v4_assets_collected_only_with_spoke(self):
        asset = _aave_v4_substrate(1, "ab" * 20)
        spoke = _aave_v4_substrate(2, "cd" * 20)
        with_spoke = _fetch_middleware_priced_substrates(
            None, None, {IporFusionMarkets.AAVE_V4: [asset, spoke]}, []
        )
        assert with_spoke is not None
        assert [t.token for t in with_spoke[IporFusionMarkets.AAVE_V4]] == [
            "0x" + "ab" * 20
        ]
        # Without a granted Spoke the fuse never iterates reserves -> nothing
        # is ever priced -> no exposure to report.
        without_spoke = _fetch_middleware_priced_substrates(
            None, None, {IporFusionMarkets.AAVE_V4: [asset]}, []
        )
        assert without_spoke is None

    def test_zero_address_substrates_skipped(self):
        subs = {IporFusionMarkets.DOLOMITE: [_dolomite_substrate("00" * 20)]}
        assert _fetch_middleware_priced_substrates(None, None, subs, []) is None

    def test_unrelated_markets_ignored(self):
        subs = {IporFusionMarkets.MORPHO: [bytes(32)]}
        assert _fetch_middleware_priced_substrates(None, None, subs, []) is None

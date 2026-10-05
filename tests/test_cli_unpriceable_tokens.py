from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.cli import vault_unpriceable
from ipor_fusion.cli.vault_health import _compute_unpriceable_token_criticals
from ipor_fusion.cli.vault_unpriceable import (
    MiddlewarePricedToken,
    fetch_unpriceable_priced_tokens,
)
from ipor_fusion.core.plasma_vault import BalanceFuse
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import MarketId


def _addr(byte: int) -> str:
    return Web3.to_checksum_address("0x" + f"{byte:02x}" * 20)


ORACLE = _addr(0x0A)
UNDERLYING = _addr(0x01)
PRICED = _addr(0x02)
UNPRICED = _addr(0x03)
BROKEN_FEED = _addr(0x04)
FEED = _addr(0x05)
VAULT = _addr(0x10)
SILO_CONFIG = _addr(0x11)
SILO_0 = _addr(0x12)
SILO_1 = _addr(0x13)
AAVE_FUSE = _addr(0x14)
ZERO = _addr(0x00)


def _sel(signature: str) -> bytes:
    return function_signature_to_4byte_selector(signature)


def _plain(address: str) -> bytes:
    return bytes(12) + bytes.fromhex(address[2:])


def _high(address: str, flags: bytes = b"\x01\x00") -> bytes:
    return bytes.fromhex(address[2:]) + flags + bytes(10)


class _FakeMulticall:
    """Answers each Call by (to, calldata); an unknown call reverts."""

    answers: dict[tuple[str, bytes], object] = {}

    def __init__(self, ctx):
        pass

    def try_aggregate(self, calls):
        return [self.answers.get((call.to, bytes(call.data))) for call in calls]


def _oracle_answers(sources: dict[str, str], priced: set[str]):
    answers: dict[tuple[str, bytes], object] = {}
    for token, source in sources.items():
        arg = encode(["address"], [token])
        answers[(ORACLE, _sel("getSourceOfAssetPrice(address)") + arg)] = source
        if token in priced:
            answers[(ORACLE, _sel("getAssetPrice(address)") + arg)] = object()
    return answers


@pytest.fixture
def chain(monkeypatch):
    _FakeMulticall.answers = {}
    monkeypatch.setattr(vault_unpriceable, "Multicall3", _FakeMulticall)
    ctx = MagicMock()
    ctx.web3.eth.get_code.return_value = b"\x60\x80"
    return SimpleNamespace(ctx=ctx, answers=_FakeMulticall.answers)


def _fetch(chain, substrates, balance_fuses=(), morpho_positions=None):
    return fetch_unpriceable_priced_tokens(
        chain.ctx,
        ORACLE,
        UNDERLYING,
        list(balance_fuses),
        substrates,
        morpho_positions,
    )


class TestFetch:
    def test_erc20_flags_only_reverting_non_underlying(self, chain):
        chain.answers.update(
            _oracle_answers(
                {PRICED: ZERO, UNPRICED: ZERO, UNDERLYING: ZERO}, priced={PRICED}
            )
        )
        flagged = _fetch(
            chain,
            {
                IporFusionMarkets.ERC20_VAULT_BALANCE: [
                    _plain(UNDERLYING),
                    _plain(PRICED),
                    _plain(UNPRICED),
                    bytes(32),
                ]
            },
        )
        assert flagged == [
            MiddlewarePricedToken(
                IporFusionMarkets.ERC20_VAULT_BALANCE,
                UNPRICED,
                "granted ERC20 substrate",
                zero_balance_reverts=True,
            )
        ]

    def test_registry_priced_token_with_zero_source_is_not_flagged(self, chain):
        chain.answers.update(_oracle_answers({PRICED: ZERO}, priced={PRICED}))
        substrates = {IporFusionMarkets.DOLOMITE: [_high(PRICED)]}
        assert _fetch(chain, substrates) == []

    def test_configured_source_that_reverts_is_flagged_with_it(self, chain):
        chain.answers.update(_oracle_answers({UNPRICED: BROKEN_FEED}, priced=set()))
        (flagged,) = _fetch(chain, {IporFusionMarkets.DOLOMITE: [_high(UNPRICED)]})
        assert flagged.price_source == BROKEN_FEED
        assert not flagged.zero_balance_reverts

    def test_unreadable_source_reads_as_none(self, chain):
        (flagged,) = _fetch(chain, {IporFusionMarkets.DOLOMITE: [_high(UNPRICED)]})
        assert flagged.price_source is None

    def test_vault_and_silo_assets(self, chain):
        chain.answers[(VAULT, _sel("asset()"))] = UNPRICED
        chain.answers[(SILO_CONFIG, _sel("getSilos()"))] = [SILO_0, ZERO]
        chain.answers[(SILO_0, _sel("asset()"))] = UNPRICED
        chain.answers.update(_oracle_answers({UNPRICED: FEED}, priced=set()))
        flagged = _fetch(
            chain,
            {
                IporFusionMarkets.EULER_V2: [_high(VAULT)],
                IporFusionMarkets.ERC4626_0001: [_plain(VAULT)],
                IporFusionMarkets.SILO_V2: [_plain(SILO_CONFIG)],
            },
        )
        assert [(f.market_id, f.via) for f in flagged] == [
            (IporFusionMarkets.EULER_V2, f"asset() of granted Euler vault {VAULT}"),
            (
                IporFusionMarkets.SILO_V2,
                f"asset() of silo {SILO_0} of granted Silo Config {SILO_CONFIG}",
            ),
            (
                IporFusionMarkets.ERC4626_0001,
                f"asset() of granted ERC4626 vault {VAULT}",
            ),
        ]

    def test_unreadable_vault_asset_and_silos_are_skipped(self, chain):
        substrates = {
            IporFusionMarkets.EULER_V2: [_high(VAULT)],
            IporFusionMarkets.SILO_V2: [_plain(SILO_CONFIG)],
        }
        assert _fetch(chain, substrates) == []

    def test_morpho_loan_and_collateral(self, chain):
        chain.answers.update(
            _oracle_answers({PRICED: ZERO, UNPRICED: ZERO}, priced={PRICED})
        )
        position = SimpleNamespace(
            market_id="ab" * 32, loan_token=PRICED, collateral_token=UNPRICED
        )
        idle = SimpleNamespace(
            market_id="cd" * 32, loan_token=PRICED, collateral_token=ZERO
        )
        (flagged,) = _fetch(
            chain, {}, morpho_positions={IporFusionMarkets.MORPHO: [position, idle]}
        )
        assert flagged.token == UNPRICED
        assert flagged.via == f"collateral token of Morpho market {'ab' * 32}"
        assert not flagged.zero_balance_reverts

    @pytest.mark.parametrize(
        ("code", "flagged"),
        [
            (b"\x60\x80", True),
            (b"\x60\x80\x63" + _sel("getPriceOracle()"), False),
            (b"", False),
        ],
    )
    def test_aave_v3_only_the_middleware_variant(self, chain, code, flagged):
        chain.ctx.web3.eth.get_code.return_value = code
        chain.answers.update(_oracle_answers({UNPRICED: ZERO}, priced=set()))
        market = MarketId(IporFusionMarkets.AAVE_V3)
        result = _fetch(
            chain,
            {market: [_plain(UNPRICED)]},
            balance_fuses=[
                BalanceFuse(market_id=market, fuse=AAVE_FUSE),
                BalanceFuse(
                    market_id=MarketId(IporFusionMarkets.MORPHO), fuse=AAVE_FUSE
                ),
            ],
        )
        assert bool(result) is flagged

    def test_nothing_priced_reads_nothing(self, chain):
        assert _fetch(chain, {IporFusionMarkets.MORPHO: [bytes(32)]}) == []

    def test_same_token_flagged_once_per_market(self, chain):
        chain.answers.update(_oracle_answers({UNPRICED: ZERO}, priced=set()))
        flagged = _fetch(
            chain,
            {
                IporFusionMarkets.ERC20_VAULT_BALANCE: [_plain(UNPRICED)] * 2,
                IporFusionMarkets.DOLOMITE: [_high(UNPRICED)],
            },
        )
        assert [f.market_id for f in flagged] == [
            IporFusionMarkets.ERC20_VAULT_BALANCE,
            IporFusionMarkets.DOLOMITE,
        ]


class TestCriticals:
    def test_messages(self):
        data = SimpleNamespace(
            unpriceable_priced_tokens=[
                MiddlewarePricedToken(
                    IporFusionMarkets.ERC20_VAULT_BALANCE,
                    UNPRICED,
                    "granted ERC20 substrate",
                    zero_balance_reverts=True,
                ),
                MiddlewarePricedToken(
                    IporFusionMarkets.DOLOMITE,
                    UNPRICED,
                    "granted Dolomite asset substrate",
                    zero_balance_reverts=False,
                    price_source=BROKEN_FEED,
                ),
            ]
        )
        no_position, on_behalf = _compute_unpriceable_token_criticals(data)  # type: ignore[arg-type]
        assert no_position.startswith("CRITICAL — market ERC20_VAULT_BALANCE (7)")
        assert "it has no price source" in no_position
        assert "even with no position" in no_position
        assert f"its price source {BROKEN_FEED} reverts" in on_behalf
        assert "on the vault's behalf" in on_behalf

    def test_none_when_not_run(self):
        data = SimpleNamespace(unpriceable_priced_tokens=None)
        assert _compute_unpriceable_token_criticals(data) == []  # type: ignore[arg-type]

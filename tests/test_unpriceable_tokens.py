from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.core.multicall import Multicall3Batch
from ipor_fusion.core.plasma_vault import BalanceFuse
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.readers import unpriceable
from ipor_fusion.readers.unpriceable import (
    _DERIVE_WITHDRAWAL_OUTBOX,
    MiddlewarePricedToken,
    _derive_tokens,
    _DeriveAssetResolution,
    _resolve_asset_call,
    fetch_unpriceable_priced_tokens,
)
from ipor_fusion.validation import _unpriceable_token_findings


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
    asked: list[tuple[str, bytes]] = []

    def __init__(self, ctx):
        pass

    def try_aggregate(self, calls):
        return [self.answers.get((call.to, bytes(call.data))) for call in calls]

    def batch(self):
        return self

    def try_add(self, call):
        self.asked.append((call.to, bytes(call.data)))
        return SimpleNamespace(value=self.answers.get((call.to, bytes(call.data))))

    def run(self):
        pass


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
    _FakeMulticall.asked = []
    monkeypatch.setattr(unpriceable, "Multicall3", _FakeMulticall)
    ctx = MagicMock()
    ctx.chain_id = 1
    ctx.web3.eth.get_code.return_value = b"\x60\x80"
    return SimpleNamespace(
        ctx=ctx, answers=_FakeMulticall.answers, asked=_FakeMulticall.asked
    )


def _fetch(chain, substrates, balance_fuses=(), morpho_positions=None):
    return _check(chain, substrates, balance_fuses, morpho_positions).tokens


def _check(chain, substrates, balance_fuses=(), morpho_positions=None):
    return fetch_unpriceable_priced_tokens(
        chain.ctx,
        price_oracle=ORACLE,
        underlying=UNDERLYING,
        balance_fuses=list(balance_fuses),
        market_substrates=substrates,
        morpho_positions=morpho_positions,
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

    def test_get_silos_decodes_both_silos(self, chain):
        call = unpriceable._silos_call(SILO_CONFIG)
        raw = encode(["address", "address"], [SILO_0, SILO_1])
        assert call.decode(raw) == [SILO_0, SILO_1]

    def test_unreadable_vault_asset_and_silos_are_named_unread(self, chain):
        substrates = {
            IporFusionMarkets.EULER_V2: [_high(VAULT)],
            IporFusionMarkets.SILO_V2: [_plain(SILO_CONFIG)],
        }
        check = _check(chain, substrates)
        assert check.tokens == []
        assert check.unread == [
            f"getSilos() of granted Silo Config {SILO_CONFIG}",
            f"asset() of granted Euler vault {VAULT}",
        ]

    def test_morpho_loan_and_collateral(self, chain):
        chain.answers.update(
            _oracle_answers({PRICED: ZERO, UNPRICED: ZERO}, priced={PRICED})
        )
        supplied = SimpleNamespace(
            market_id="ab" * 32,
            loan_token=PRICED,
            collateral_token=UNPRICED,
            collateral=0,
            supply_assets=10,
            borrow_assets=0,
        )
        collateralized = SimpleNamespace(
            market_id="ef" * 32,
            loan_token=PRICED,
            collateral_token=UNPRICED,
            collateral=1,
            supply_assets=0,
            borrow_assets=0,
        )
        idle = SimpleNamespace(
            market_id="cd" * 32,
            loan_token=PRICED,
            collateral_token=ZERO,
            collateral=0,
            supply_assets=0,
            borrow_assets=0,
        )
        flagged = _fetch(
            chain,
            {},
            morpho_positions={
                IporFusionMarkets.MORPHO: [supplied, collateralized, idle]
            },
        )
        assert [(f.via, f.position_open) for f in flagged] == [
            (f"collateral token of Morpho market {'ab' * 32}", False),
            (f"collateral token of Morpho market {'ef' * 32}", True),
        ]
        assert all(f.token == UNPRICED for f in flagged)
        assert all(f.morpho_collateral for f in flagged)
        assert not any(f.zero_balance_reverts for f in flagged)

    @pytest.mark.parametrize(
        ("supply", "borrow", "position_open"),
        [(0, 5, True), (7, 5, True), (5, 5, None), (0, 0, False)],
    )
    def test_morpho_loan_leg_follows_the_net_amount(
        self, chain, supply, borrow, position_open
    ):
        chain.answers.update(_oracle_answers({UNPRICED: ZERO}, priced=set()))
        position = SimpleNamespace(
            market_id="ab" * 32,
            loan_token=UNPRICED,
            collateral_token=ZERO,
            collateral=0,
            supply_assets=supply,
            borrow_assets=borrow,
        )
        (flagged,) = _fetch(
            chain, {}, morpho_positions={IporFusionMarkets.MORPHO: [position]}
        )
        assert flagged.position_open is position_open
        assert not flagged.morpho_collateral

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
        market = IporFusionMarkets.AAVE_V3
        result = _fetch(
            chain,
            {market: [_plain(UNPRICED)]},
            balance_fuses=[
                BalanceFuse(market_id=market, fuse=AAVE_FUSE),
                BalanceFuse(market_id=IporFusionMarkets.MORPHO, fuse=AAVE_FUSE),
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


def _token(**overrides) -> MiddlewarePricedToken:
    fields = {
        "market_id": IporFusionMarkets.MORPHO,
        "token": UNPRICED,
        "via": f"collateral token of Morpho market {'ab' * 32}",
        "zero_balance_reverts": False,
        "position_open": False,
        "morpho_collateral": True,
    } | overrides
    return MiddlewarePricedToken(**fields)


def _findings(*tokens: MiddlewarePricedToken) -> tuple[list[str], list[str]]:
    data = SimpleNamespace(unpriceable_priced_tokens=list(tokens))
    findings = _unpriceable_token_findings(data)  # type: ignore[arg-type]
    assert {f.check for f in findings} <= {"unpriceable_token"}
    return (
        [f.message for f in findings if f.severity == "critical"],
        [f.message for f in findings if f.severity == "warning"],
    )


class TestFindings:
    def test_reverts_without_position_is_critical(self):
        (line,), warnings = _findings(
            _token(
                market_id=IporFusionMarkets.ERC20_VAULT_BALANCE,
                via="granted ERC20 substrate",
                zero_balance_reverts=True,
                position_open=None,
                morpho_collateral=False,
            )
        )
        assert warnings == []
        assert line.startswith("CRITICAL — market ERC20_VAULT_BALANCE (7)")
        assert "it has no price source" in line
        assert "even with no position" in line

    def test_open_position_is_critical(self):
        (line,), warnings = _findings(_token(position_open=True))
        assert warnings == []
        assert "the vault holds it, so balanceOf() reverts now" in line

    def test_unread_position_is_a_warning(self):
        criticals, (line,) = _findings(
            _token(
                market_id=IporFusionMarkets.DOLOMITE,
                via="granted Dolomite asset substrate",
                position_open=None,
                morpho_collateral=False,
                price_source=BROKEN_FEED,
            )
        )
        assert criticals == []
        assert line.startswith("WARNING — market DOLOMITE")
        assert f"its price source {BROKEN_FEED} reverts" in line
        assert "position not read, so current impact is unknown" in line
        assert line.endswith("fix: add a price source")

    def test_latent_morpho_collateral_is_a_warning_with_the_fuse_hint(self):
        criticals, (line,) = _findings(_token())
        assert criticals == []
        assert line.startswith("WARNING — market MORPHO (14)")
        assert "the vault holds none in this position" in line
        assert "if every Morpho market of this market is and stays supply-only" in line
        assert "MorphoOnlyLiquidityBalanceFuse" in line

    def test_latent_loan_token_gets_no_fuse_hint(self):
        _, (line,) = _findings(
            _token(
                via=f"loan token of Morpho market {'ab' * 32}",
                morpho_collateral=False,
            )
        )
        assert line.endswith("fix: add a price source")

    def test_none_when_not_run(self):
        data = SimpleNamespace(unpriceable_priced_tokens=None)
        assert _unpriceable_token_findings(data) == []  # type: ignore[arg-type]


DERIVE = IporFusionMarkets.DERIVE
DERIVE_FUSE = _addr(0x20)
USDC = _addr(0x21)
LABEL = _addr(0x22)
LABEL_2 = _addr(0x23)
TOKEN_2 = _addr(0x24)
OUTBOX = _DERIVE_WITHDRAWAL_OUTBOX[1]
CASH_VIA = "cash asset of the Derive balance fuse (report equity)"


def _derive_word(type_byte: int, address: str) -> bytes:
    return (type_byte << 248 | int(address, 16)).to_bytes(32, "big")


def _resolve_key(label: str) -> tuple[str, bytes]:
    return OUTBOX, _sel("resolveAsset(address)") + encode(["address"], [label])


class TestDerive:
    FUSES = (BalanceFuse(market_id=DERIVE, fuse=DERIVE_FUSE),)

    def _grant(self, chain, *labels, usdc=USDC):
        chain.answers[(DERIVE_FUSE, _sel("CASH_ASSET()"))] = usdc
        for label, token, native in labels:
            chain.answers[_resolve_key(label)] = _DeriveAssetResolution(token, native)

    def _check(self, chain, words, fuses=FUSES):
        return _check(chain, {DERIVE: words}, fuses)

    def test_outbox_is_the_ethereum_proxy_from_the_contract_provenance(self):
        assert OUTBOX == "0x7c743D79C9C595cE332314503d84cBBb4E85FA63"

    def test_cash_asset_and_label_token_are_flagged_when_the_oracle_reverts(
        self, chain
    ):
        self._grant(chain, (LABEL, UNPRICED, False))
        chain.answers.update(
            _oracle_answers({USDC: FEED, UNPRICED: ZERO}, priced=set())
        )
        check = self._check(chain, [_derive_word(1, LABEL)])
        assert check.unread == []
        assert check.tokens == [
            MiddlewarePricedToken(
                DERIVE,
                UNPRICED,
                f"token of granted Derive asset label {LABEL}",
                zero_balance_reverts=False,
            ),
            MiddlewarePricedToken(
                DERIVE, USDC, CASH_VIA, zero_balance_reverts=False, price_source=FEED
            ),
        ]
        assert all(t.position_open is None for t in check.tokens)

    def test_priced_tokens_are_not_flagged(self, chain):
        self._grant(chain, (LABEL, PRICED, False))
        chain.answers.update(
            _oracle_answers({USDC: ZERO, PRICED: ZERO}, priced={USDC, PRICED})
        )
        check = self._check(chain, [_derive_word(1, LABEL)])
        assert check.tokens == [] and check.unread == []
        assert _resolve_key(LABEL) in chain.asked
        assert (DERIVE_FUSE, _sel("CASH_ASSET()")) in chain.asked
        price = _sel("getAssetPrice(address)")
        assert (ORACLE, price + encode(["address"], [PRICED])) in chain.asked
        assert (ORACLE, price + encode(["address"], [USDC])) in chain.asked

    def test_native_label_has_no_token_to_price(self, chain):
        self._grant(chain, (LABEL, UNPRICED, True))
        chain.answers.update(_oracle_answers({USDC: ZERO}, priced={USDC}))
        check = self._check(chain, [_derive_word(1, LABEL)])
        assert check.tokens == [] and check.unread == []
        assert _resolve_key(LABEL) in chain.asked
        oracle_reads = [key for key in chain.asked if key[0] == ORACLE]
        assert all(encode(["address"], [UNPRICED]) not in d for _, d in oracle_reads)

    def test_unknown_label_is_unread(self, chain):
        chain.answers[(DERIVE_FUSE, _sel("CASH_ASSET()"))] = USDC
        chain.answers.update(_oracle_answers({USDC: ZERO}, priced={USDC}))
        check = self._check(chain, [_derive_word(1, LABEL)])
        assert check.tokens == []
        assert _resolve_key(LABEL) in chain.asked
        assert check.unread == [f"resolveAsset() of granted Derive asset label {LABEL}"]

    def test_other_chain_leaves_labels_unread_but_still_reads_cash_asset(self, chain):
        chain.ctx.chain_id = 42161
        self._grant(chain, (LABEL, UNPRICED, False))
        chain.answers.update(_oracle_answers({USDC: FEED}, priced=set()))
        check = self._check(chain, [_derive_word(1, LABEL), _derive_word(1, LABEL_2)])
        assert [t.token for t in check.tokens] == [USDC]
        assert len(check.unread) == 2
        assert all("chain 42161" in line for line in check.unread)

    def test_without_the_balance_fuse_only_labels_are_checked(self, chain):
        self._grant(chain, (LABEL, UNPRICED, False))
        chain.answers.update(_oracle_answers({UNPRICED: ZERO}, priced=set()))
        check = self._check(chain, [_derive_word(1, LABEL)], fuses=())
        assert [t.token for t in check.tokens] == [UNPRICED]

    def test_reverting_cash_asset_is_unread(self, chain):
        check = self._check(chain, [])
        assert check.tokens == []
        assert check.unread == [
            f"CASH_ASSET() of the Derive balance fuse {DERIVE_FUSE}"
        ]

    def test_other_substrate_types_cause_no_reads(self, chain, monkeypatch):
        words = [
            _derive_word(4, LABEL),
            _derive_word(6, LABEL_2),
            (3 << 248 | 7).to_bytes(32, "big"),
        ]
        self._grant(chain)
        chain.answers.update(_oracle_answers({USDC: ZERO}, priced={USDC}))
        reads: list[tuple[str, bytes]] = []
        original = _FakeMulticall.try_add

        def spy(self, call):
            reads.append((call.to, bytes(call.data)))
            return original(self, call)

        monkeypatch.setattr(_FakeMulticall, "try_add", spy)
        check = self._check(chain, words)
        assert check.unread == []
        assert not any(to == OUTBOX for to, _ in reads)

    def test_nothing_granted_makes_no_call(self, chain, monkeypatch):
        batch_made = []
        monkeypatch.setattr(
            _FakeMulticall, "batch", lambda self: batch_made.append(1) or self
        )
        check = self._check(chain, [], fuses=())
        assert check.tokens == [] and check.unread == []
        assert batch_made == []

    def test_malformed_asset_word_is_unread_and_never_resolved(self, chain):
        dirty = (1 << 248 | 1 << 200 | int(LABEL, 16)).to_bytes(32, "big")
        self._grant(chain, (LABEL, UNPRICED, False))
        chain.answers.update(_oracle_answers({USDC: ZERO}, priced={USDC}))
        check = self._check(chain, [dirty])
        assert check.tokens == []
        assert check.unread == [
            f"malformed Derive ASSET word 0x{dirty.hex()} "
            "(DeriveSubstrateLib rejects it)"
        ]
        assert _resolve_key(LABEL) not in chain.asked

    def test_resolve_asset_call_decodes_the_raw_return(self):
        vault, label, token = _addr(0x31), _addr(0x32), _addr(0x33)
        raw = encode(
            ["(address,uint64,address,uint8)", "address", "bool"],
            [(vault, 10**6, token, 6), label, True],
        )
        call = _resolve_asset_call(OUTBOX, LABEL)
        assert call.data == _sel("resolveAsset(address)") + encode(["address"], [LABEL])
        assert call.decode(raw) == _DeriveAssetResolution(token, True)
        assert call.decode(raw).token not in (vault, label)

    def test_truncated_return_lands_in_unread(self, chain, monkeypatch):
        good = encode(
            ["(address,uint64,address,uint8)", "address", "bool"],
            [(_addr(0x31), 1, TOKEN_2, 6), LABEL, False],
        )

        class RawMulticall:
            def __init__(self, ctx):
                self._ctx = ctx

            def batch(self):
                return Multicall3Batch(self)  # type: ignore[arg-type]

            def _fetch(self, calls):
                return [
                    (True, encode(["address"], [USDC]))
                    if call.to == DERIVE_FUSE
                    else (
                        True,
                        good
                        if call.data.endswith(bytes.fromhex(LABEL[2:]))
                        else good[:70],
                    )
                    for call in calls
                ]

        monkeypatch.setattr(unpriceable, "Multicall3", RawMulticall)
        unread: list[str] = []
        tokens = _derive_tokens(
            chain.ctx,
            list(self.FUSES),
            {DERIVE: [_derive_word(1, LABEL), _derive_word(1, LABEL_2)]},
            unread,
        )
        assert [t.token for t in tokens] == [USDC, TOKEN_2]
        assert unread == [f"resolveAsset() of granted Derive asset label {LABEL_2}"]

"""The Fuse Whitelist reader, the issues it turns into findings, and its use by
validate_vault and preflight_execute, over a faked whitelist contract."""

from unittest.mock import MagicMock, patch

import pytest
from _multicall import multicall_aware
from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

import ipor_fusion.preflight as preflight_module
from ipor_fusion.core.plasma_vault import BalanceFuse
from ipor_fusion.errors import (
    AddressNotInRegistryError,
    AddressRegistryError,
    EmptyCallResultError,
)
from ipor_fusion.fuse_whitelist import (
    FuseState,
    FuseWhitelist,
    FuseWhitelistCheck,
    WhitelistEntry,
    _entry,
    build_fuse_whitelist_json,
    check_fuses,
    fetch_fuse_whitelist_json,
    fuse_whitelist_for_chain,
    fuse_whitelist_issues,
    read_fuse_entries,
)
from ipor_fusion.registry import AddressRegistry
from ipor_fusion.types import ChainId, FuseAddress, MarketId


def _addr(byte: str) -> FuseAddress:
    return FuseAddress(Web3.to_checksum_address("0x" + byte * 20))


WHITELIST = FuseWhitelist(Web3.to_checksum_address("0x" + "aa" * 20))
EULER = MarketId(11)
CUSTOM = MarketId(9001)
TYPES = {36: "EulerV2BalanceFuse", 41: "EulerV2SupplyFuse", 38: "EulerV2BorrowFuse"}

OLD_BALANCE = _addr("01")  # deprecated EulerV2BalanceFuse
NEW_BALANCE = _addr("02")  # active EulerV2BalanceFuse
SUPPLY = _addr("03")  # active EulerV2SupplyFuse
BORROW = _addr("04")  # active EulerV2BorrowFuse
COPY = _addr("05")  # a redeployed copy, not listed
CUSTOM_FUSE = _addr("06")  # not listed, in a market with nothing listed
ZERO_ADDRESS = Web3.to_checksum_address("0x" + "00" * 20)

# fuse -> (state, type, timestamp); market -> listed fuses
LISTED = {
    OLD_BALANCE: (2, 36, 100),
    NEW_BALANCE: (1, 36, 200),
    SUPPLY: (1, 41, 300),
    BORROW: (1, 38, 400),
}
MARKETS = {EULER: [OLD_BALANCE, NEW_BALANCE, SUPPLY, BORROW]}


def _sel(signature: str) -> bytes:
    return function_signature_to_4byte_selector(signature)


def _ctx() -> MagicMock:
    def handler(to: str, data: bytes) -> bytes:
        assert to == WHITELIST.address, to
        selector = bytes(data[:4])
        if selector == _sel("getFuseTypes()"):
            return encode(["uint16[]", "string[]"], [list(TYPES), list(TYPES.values())])
        if selector == _sel("getFuseByAddress(address)"):
            (fuse,) = decode(["address"], data[4:])
            fuse = FuseAddress(Web3.to_checksum_address(fuse))
            state, fuse_type, ts = LISTED.get(fuse, (0, 0, 0))
            shown = fuse if state else "0x" + "00" * 20
            return encode(
                ["uint16", "uint16", "address", "uint32"], [state, fuse_type, shown, ts]
            )
        if selector == _sel("getFusesByMarketId(uint256)"):
            (market,) = decode(["uint256"], data[4:])
            return encode(["address[]"], [MARKETS.get(market, [])])
        raise AssertionError(f"unexpected call 0x{selector.hex()}")

    ctx = MagicMock()
    ctx.chain_id = ChainId(42161)
    ctx.default_block = 1
    ctx.at_block.return_value = ctx
    ctx.call.side_effect = multicall_aware(handler)
    return ctx


class TestRead:
    def test_an_unknown_fuse_reads_as_not_listed(self):
        entries = read_fuse_entries(_ctx(), WHITELIST, [COPY, NEW_BALANCE])

        assert entries[COPY].state is FuseState.NOT_LISTED
        assert entries[COPY].type_name == ""
        assert entries[NEW_BALANCE] == WhitelistEntry(
            NEW_BALANCE, FuseState.ACTIVE, 36, "EulerV2BalanceFuse", 200
        )

    @pytest.mark.parametrize(
        ("raw_state", "state", "status"),
        [
            (0, FuseState.DEFAULT, "state default"),
            (9, FuseState.UNKNOWN, "state unknown"),
        ],
    )
    def test_a_listed_fuse_in_another_state_is_not_read_as_not_listed(
        self, raw_state, state, status
    ):
        fuses = {COPY: EULER}
        raw = (raw_state, 36, COPY, 100)
        entry = _entry(COPY, raw, TYPES)
        check = FuseWhitelistCheck(WHITELIST.address, {COPY: entry}, {})

        assert entry.state is state
        assert entry.type_name == "EulerV2BalanceFuse"
        (issue,) = fuse_whitelist_issues(check, fuses)
        assert status in issue.message

    def test_market_fuses_are_read_only_where_a_fuse_is_not_active(self):
        check = check_fuses(_ctx(), WHITELIST, {SUPPLY: EULER, COPY: CUSTOM})

        assert set(check.market_fuses) == {CUSTOM}
        assert check.market_fuses[CUSTOM] == ()


class TestIssues:
    def test_a_deprecated_fuse_is_replaced_by_the_active_one_of_its_type(self):
        fuses = {OLD_BALANCE: EULER}
        (issue,) = fuse_whitelist_issues(
            check_fuses(_ctx(), WHITELIST, fuses), fuses, [OLD_BALANCE]
        )

        assert (issue.kind, issue.role) == ("deprecated", "balance")
        assert [e.fuse for e in issue.replacements] == [NEW_BALANCE]
        assert "warns the vault's Atomist" in issue.message
        assert f"use {NEW_BALANCE} (EulerV2BalanceFuse)" in issue.message

    def test_a_copy_gets_the_active_fuses_of_its_market_and_role(self):
        fuses = {COPY: EULER}
        (issue,) = fuse_whitelist_issues(check_fuses(_ctx(), WHITELIST, fuses), fuses)

        assert (issue.kind, issue.role) == ("not_whitelisted", "action")
        assert {e.fuse for e in issue.replacements} == {SUPPLY, BORROW}
        assert "shows the allocation in this market as unknown" in issue.message

    def test_a_fuse_of_another_role_is_never_the_replacement(self):
        claim = WhitelistEntry(BORROW, FuseState.ACTIVE, 38, "MorphoClaimFuse", 0)
        hook = WhitelistEntry(
            SUPPLY, FuseState.ACTIVE, 41, "ExternalStatePausePreHook", 0
        )
        fuses = {COPY: EULER}
        check = FuseWhitelistCheck(
            WHITELIST.address,
            {COPY: _entry(COPY, (0, 0, ZERO_ADDRESS, 0), {})},
            {EULER: (claim, hook)},
        )

        (issue,) = fuse_whitelist_issues(check, fuses, [COPY])

        assert issue.replacements == ()
        assert "no active whitelisted balance fuse" in issue.message

    def test_a_market_without_listed_fuses_points_at_the_review(self):
        fuses = {CUSTOM_FUSE: CUSTOM}
        (issue,) = fuse_whitelist_issues(check_fuses(_ctx(), WHITELIST, fuses), fuses)

        assert issue.replacements == ()
        assert "needs the IPOR whitelist review" in issue.message

    def test_active_fuses_raise_no_issue(self):
        fuses = {NEW_BALANCE: EULER, SUPPLY: EULER}
        check = check_fuses(_ctx(), WHITELIST, fuses)

        assert fuse_whitelist_issues(check, fuses, [NEW_BALANCE]) == []


@pytest.mark.parametrize(
    ("name", "role"),
    [
        ("EulerV2BalanceFuse", "balance"),
        ("UniswapV3Balance", "balance"),
        ("BalanceFuseUniversalTokenSwapperV2", "balance"),
        ("EulerV2SupplyFuse", "action"),
        ("BalanceFusesReader", None),
        ("PlasmaVaultBalanceAssetsValidationFuse", None),
        ("ExchangeRateValidatorPreHook", None),
        ("ExternalStatePausePreHook", None),
        ("CrosschainNavValidationPreHook", None),
        ("", None),
    ],
)
def test_role_comes_from_the_type_name(name, role):
    assert WhitelistEntry(COPY, FuseState.ACTIVE, 1, name, 0).role == role


class TestDocument:
    def test_active_first_and_deprecated_only_on_request(self):
        entries = check_fuses(_ctx(), WHITELIST, {OLD_BALANCE: EULER}).market_fuses[
            EULER
        ]
        active = build_fuse_whitelist_json(ChainId(42161), WHITELIST, EULER, entries)
        every = build_fuse_whitelist_json(
            ChainId(42161), WHITELIST, EULER, entries, include_deprecated=True
        )

        assert {f["state"] for f in active["fuses"]} == {"active"}
        assert len(active["fuses"]) == 3
        assert every["fuses"][-1]["fuse"] == OLD_BALANCE
        assert every["fuses"][-1]["state"] == "deprecated"
        assert active["market"] == "EULER_V2 (11)"

    def test_a_chain_without_a_whitelist_is_an_error(self):
        with (
            patch(
                "ipor_fusion.fuse_whitelist.fuse_whitelist_for_chain", return_value=None
            ),
            pytest.raises(AddressNotInRegistryError),
        ):
            fetch_fuse_whitelist_json(_ctx(), EULER)


def test_the_whitelist_comes_from_the_registry_by_name():
    registry = AddressRegistry(
        ChainId(42161), "main", {"IporFusionFuseWhitelistProxy": WHITELIST.address}
    )
    empty = AddressRegistry(ChainId(42161), "main", {})
    with patch(
        "ipor_fusion.fuse_whitelist.fetch_address_registry", return_value=registry
    ):
        found = fuse_whitelist_for_chain(ChainId(42161))
    with patch("ipor_fusion.fuse_whitelist.fetch_address_registry", return_value=empty):
        missing = fuse_whitelist_for_chain(ChainId(42161))

    assert found is not None and found.address == WHITELIST.address
    assert missing is None


class TestValidateVault:
    @staticmethod
    def _state():
        state = MagicMock()
        state.chain_id = ChainId(42161)
        state.block_number = 7
        state.fuses = [COPY]
        state.fuse_markets = {COPY: EULER}
        state.balance_fuses = [BalanceFuse(EULER, OLD_BALANCE)]
        return state

    def test_issues_become_warnings_and_a_clean_vault_an_ok_line(self):
        from ipor_fusion.validation import _fuse_whitelist_findings

        state = self._state()
        check = check_fuses(_ctx(), WHITELIST, {COPY: EULER, OLD_BALANCE: EULER})
        findings = _fuse_whitelist_findings(state, check)

        assert [(f.check, f.severity) for f in findings] == [
            ("fuse_whitelist", "warning"),
            ("fuse_whitelist", "warning"),
        ]
        state.fuses = [SUPPLY]
        state.fuse_markets = {SUPPLY: EULER}
        state.balance_fuses = [BalanceFuse(EULER, NEW_BALANCE)]
        clean = check_fuses(_ctx(), WHITELIST, {SUPPLY: EULER, NEW_BALANCE: EULER})
        (ok,) = _fuse_whitelist_findings(state, clean)
        assert ok.severity == "ok"

    def test_a_fuse_without_a_readable_market_blocks_the_ok_line(self):
        from ipor_fusion.validation import _fuse_whitelist_findings

        state = self._state()
        state.fuses = [SUPPLY, COPY]
        state.fuse_markets = {SUPPLY: EULER}
        state.balance_fuses = [BalanceFuse(EULER, NEW_BALANCE)]
        check = check_fuses(_ctx(), WHITELIST, {SUPPLY: EULER, NEW_BALANCE: EULER})

        (finding,) = _fuse_whitelist_findings(state, check)

        assert (finding.check, finding.severity) == ("incomplete_read", "warning")
        assert str(COPY) in finding.message

    def test_a_vault_with_no_readable_market_gets_no_ok_line(self):
        from ipor_fusion.validation import _fuse_whitelist_findings

        state = self._state()
        state.fuses = [COPY]
        state.fuse_markets = None
        state.balance_fuses = []
        check = check_fuses(_ctx(), WHITELIST, {})

        (finding,) = _fuse_whitelist_findings(state, check)

        assert finding.check == "incomplete_read"

    def test_a_fuse_of_market_zero_is_not_checked_or_reported(self):
        from ipor_fusion.validation import _fuse_whitelist_findings, _vault_fuses

        state = self._state()
        state.fuses = [COPY]
        state.fuse_markets = {COPY: MarketId(0)}
        state.balance_fuses = []

        assert _vault_fuses(state) == {}
        (ok,) = _fuse_whitelist_findings(state, check_fuses(_ctx(), WHITELIST, {}))
        assert ok.severity == "ok"

    @pytest.mark.parametrize(
        "error", [EmptyCallResultError("no code"), DecodingError("bad")]
    )
    def test_a_block_before_the_whitelist_is_an_incomplete_read(self, error):
        from ipor_fusion.validation import validate_vault

        state = self._state()
        with (
            patch("ipor_fusion.validation.fetch_vault_data", return_value=state),
            patch(
                "ipor_fusion.validation.validate_vault_state",
                side_effect=lambda s, fuse_whitelist: _empty_validation(),
            ),
            patch(
                "ipor_fusion.validation.read_vault_fuse_whitelist",
                side_effect=error,
            ),
        ):
            validation = validate_vault(_ctx(), state.vault)

        (finding,) = validation.findings
        assert finding.check == "incomplete_read"

    def test_an_unreadable_registry_is_an_incomplete_read(self):
        from ipor_fusion.validation import validate_vault

        state = self._state()
        with (
            patch("ipor_fusion.validation.fetch_vault_data", return_value=state),
            patch(
                "ipor_fusion.validation.validate_vault_state",
                side_effect=lambda s, fuse_whitelist: _empty_validation(),
            ),
            patch(
                "ipor_fusion.validation.fuse_whitelist_for_chain",
                side_effect=AddressRegistryError("offline"),
            ),
        ):
            validation = validate_vault(_ctx(), state.vault)

        (finding,) = validation.findings
        assert (finding.check, finding.severity) == ("incomplete_read", "warning")
        assert "AddressRegistryError" in finding.message


def _empty_validation():
    from ipor_fusion.validation import Reconciliation, VaultValidation
    from ipor_fusion.vault_erc20 import Erc20Holdings

    zero = dict.fromkeys(Reconciliation.__dataclass_fields__, 0)
    zero["on_chain_total_assets_usd"] = None
    return VaultValidation(
        vault=_addr("11"),  # type: ignore[arg-type]
        chain_id=ChainId(42161),
        block_number=7,
        block_timestamp=0,  # type: ignore[arg-type]
        asset=_addr("12"),
        asset_symbol="USDC",
        asset_decimals=6,  # type: ignore[arg-type]
        asset_price_usd=None,
        findings=(),
        reconciliation=Reconciliation(**zero),
        erc20=Erc20Holdings(),
        lending_health=None,
    )


class TestPreflight:
    def test_each_fuse_of_the_batch_that_is_not_active_is_a_warning(self):
        actions = [
            MagicMock(fuse=COPY, market_id=EULER),
            MagicMock(fuse=SUPPLY, market_id=EULER),
            MagicMock(fuse=_addr("07"), market_id=None),
        ]
        with patch.object(
            preflight_module, "fuse_whitelist_for_chain", return_value=WHITELIST
        ):
            findings = preflight_module._whitelist_findings(_ctx(), actions)

        (finding,) = findings
        assert (finding.check, finding.severity) == ("fuse_whitelist", "warning")
        assert str(COPY) in finding.message

    def test_market_zero_needs_no_registry_download(self):
        actions = [MagicMock(fuse=COPY, market_id=MarketId(0))]
        with patch.object(preflight_module, "fuse_whitelist_for_chain") as download:
            assert preflight_module._whitelist_findings(_ctx(), actions) == []
        download.assert_not_called()

    def test_an_unreadable_whitelist_is_a_warning_not_an_error(self):
        actions = [MagicMock(fuse=COPY, market_id=EULER)]
        with patch.object(
            preflight_module,
            "fuse_whitelist_for_chain",
            side_effect=AddressRegistryError("offline"),
        ):
            (finding,) = preflight_module._whitelist_findings(_ctx(), actions)

        assert finding.severity == "warning"
        assert "could not be read" in finding.message

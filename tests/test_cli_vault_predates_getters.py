"""Vaults deployed before a getter existed: `vault info` degrades the data that
getter serves and says so, instead of aborting on the empty eth_call result.

Modeled on the Arbitrum vaults deployed before the August 2024 audit, which
answer `getMarketSubstrates`, `getTotalSupplyCap` and
`getPriceOracleMiddleware` with empty data and name the oracle getter
`getPriceOracle()`.
"""

from collections.abc import Callable
from unittest.mock import MagicMock, patch

import pytest
from _multicall import multicall_aware
from click.testing import CliRunner
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion import UnsupportedVaultVersionError
from ipor_fusion.cli import config_store
from ipor_fusion.cli.config_store import FusionConfig, save_config
from ipor_fusion.cli.main import cli
from ipor_fusion.cli.vault_cmd import _print_market_substrates
from ipor_fusion.cli.vault_fetcher import (
    GET_MARKET_SUBSTRATES,
    GET_PRICE_ORACLE_MIDDLEWARE,
    GET_TOTAL_SUPPLY_CAP,
    _fetch_market_reads,
    _fetch_vault_reads,
    _VaultData,
)
from ipor_fusion.cli.vault_health import (
    _BalanceFuseTotals,
    _compute_health_check,
    _Erc20Totals,
)
from ipor_fusion.core.plasma_vault import BalanceFuse, PlasmaVault
from ipor_fusion.types import MarketId

VAULT = Web3.to_checksum_address("0x" + "11" * 20)
ASSET = Web3.to_checksum_address("0x" + "22" * 20)
ACCESS_MANAGER = Web3.to_checksum_address("0x" + "33" * 20)
ORACLE = Web3.to_checksum_address("0x" + "44" * 20)
LEGACY_ORACLE = Web3.to_checksum_address("0x" + "45" * 20)
FUSE = Web3.to_checksum_address("0x" + "55" * 20)
UINT256_MAX = 2**256 - 1

Response = bytes | Exception


def _address(value: str) -> bytes:
    return encode(["address"], [value])


def _uint(value: int) -> bytes:
    return encode(["uint256"], [value])


# What every vault generation answers.
_COMMON: dict[str, Response] = {
    "decimals()": _uint(6),
    "totalAssets()": _uint(1_000),
    "totalSupply()": _uint(900),
    "asset()": _address(ASSET),
    "getAccessManagerAddress()": _address(ACCESS_MANAGER),
    "getFuses()": encode(["address[]"], [[FUSE]]),
    "getInstantWithdrawalFuses()": encode(["address[]"], [[]]),
    "name()": encode(["string"], ["Vault"]),
    "getRewardsClaimManagerAddress()": _address("0x" + "00" * 20),
    "balanceOf(address)": _uint(5),
    "symbol()": encode(["string"], ["USDC"]),
    "getAssetPrice(address)": encode(["uint256", "uint256"], [10**8, 8]),
    "MARKET_ID()": _uint(1),
    "getDependencyBalanceGraph(uint256)": encode(["uint256[]"], [[]]),
}

# A vault that predates the getters answers them with no data, not a revert.
PRE_AUDIT: dict[str, Response] = {
    **_COMMON,
    GET_TOTAL_SUPPLY_CAP: b"",
    GET_PRICE_ORACLE_MIDDLEWARE: b"",
    "getPriceOracle()": _address(LEGACY_ORACLE),
    GET_MARKET_SUBSTRATES: b"",
}

CURRENT: dict[str, Response] = {
    **_COMMON,
    GET_TOTAL_SUPPLY_CAP: _uint(10**12),
    GET_PRICE_ORACLE_MIDDLEWARE: _address(ORACLE),
    "getPriceOracle()": ContractLogicError("execution reverted"),
    GET_MARKET_SUBSTRATES: encode(
        ["bytes32[]"], [[bytes(12) + bytes.fromhex(ASSET[2:])]]
    ),
}


def _ctx(responses: dict[str, Response]) -> MagicMock:
    by_selector = {
        function_signature_to_4byte_selector(sig): response
        for sig, response in responses.items()
    }

    def handler(_to: str, data: bytes) -> bytes:
        response = by_selector[data[:4]]
        if isinstance(response, Exception):
            raise response
        return response

    ctx = MagicMock()
    ctx.chain_id = 42161
    ctx.call.side_effect = multicall_aware(handler)
    return ctx


def _vault(ctx: MagicMock) -> PlasmaVault:
    return PlasmaVault(ctx, VAULT)


def _market_reads(responses: dict[str, Response]):
    ctx = _ctx(responses)
    pv = _vault(ctx)
    fuses = [BalanceFuse(MarketId(1), FUSE), BalanceFuse(MarketId(2), FUSE)]
    with patch.object(PlasmaVault, "get_balance_fuses", return_value=fuses):
        return _fetch_market_reads(ctx, pv, with_aave_pools=False)


class TestSelectors:
    """The getter names reported to users are the selectors actually called."""

    @pytest.mark.parametrize(
        ("signature", "build", "selector"),
        [
            (
                GET_MARKET_SUBSTRATES,
                lambda pv: pv.get_market_substrates(MarketId(1)),
                "2ede66bc",
            ),
            (GET_TOTAL_SUPPLY_CAP, lambda pv: pv.get_total_supply_cap(), None),
            (
                GET_PRICE_ORACLE_MIDDLEWARE,
                lambda pv: pv.get_price_oracle_middleware_address(),
                None,
            ),
            ("getPriceOracle()", lambda pv: pv.get_price_oracle_address(), None),
        ],
    )
    def test_signature_matches_wrapper(
        self, signature: str, build: Callable, selector: str | None
    ):
        call = build(_vault(MagicMock()))
        assert call.data[:4] == function_signature_to_4byte_selector(signature)
        if selector is not None:
            assert call.data[:4].hex() == selector


class TestMarketReads:
    def test_vault_without_get_market_substrates_reads_no_substrates(self):
        reads = _market_reads(PRE_AUDIT)

        assert reads.substrates_available is False
        assert reads.market_substrates == {}
        assert [bf.market_id for bf in reads.balance_fuses] == [1, 2]

    def test_current_vault_reads_substrates(self):
        reads = _market_reads(CURRENT)

        assert reads.substrates_available is True
        assert set(reads.market_substrates) == {1, 2}

    def test_reverting_substrate_read_stays_loud(self):
        responses = {**CURRENT, GET_MARKET_SUBSTRATES: ContractLogicError("boom")}

        with pytest.raises(ContractLogicError, match="boom"):
            _market_reads(responses)

    def test_vault_without_dependency_graph_raises_typed_error(self):
        responses = {**PRE_AUDIT, "getDependencyBalanceGraph(uint256)": b""}

        with pytest.raises(UnsupportedVaultVersionError, match="0x"):
            _market_reads(responses)


class TestVaultReads:
    def test_pre_audit_vault_degrades_supply_cap_and_oracle_getter(self):
        ctx = _ctx(PRE_AUDIT)

        reads = _fetch_vault_reads(ctx, _vault(ctx))

        assert reads["supply_cap"] == UINT256_MAX
        assert reads["price_oracle_addr"] == LEGACY_ORACLE
        assert reads["unimplemented_getters"] == [
            GET_TOTAL_SUPPLY_CAP,
            GET_PRICE_ORACLE_MIDDLEWARE,
        ]
        assert reads["total_assets"] == 1_000

    def test_current_vault_reports_no_unimplemented_getter(self):
        ctx = _ctx(CURRENT)

        reads = _fetch_vault_reads(ctx, _vault(ctx))

        assert reads["supply_cap"] == 10**12
        assert reads["price_oracle_addr"] == ORACLE
        assert reads["unimplemented_getters"] == []

    def test_vault_without_any_oracle_getter_raises_typed_error(self):
        ctx = _ctx({**PRE_AUDIT, "getPriceOracle()": b""})

        with pytest.raises(UnsupportedVaultVersionError, match="getPriceOracle"):
            _fetch_vault_reads(ctx, _vault(ctx))

    def test_vault_without_essential_getter_raises_typed_error(self):
        ctx = _ctx({**CURRENT, "getFuses()": b""})
        selector = function_signature_to_4byte_selector("getFuses()").hex()

        with pytest.raises(UnsupportedVaultVersionError, match=selector) as exc:
            _fetch_vault_reads(ctx, _vault(ctx))
        assert isinstance(exc.value, ValueError)

    def test_reverting_supply_cap_read_stays_loud(self):
        ctx = _ctx({**CURRENT, GET_TOTAL_SUPPLY_CAP: ContractLogicError("boom")})

        with pytest.raises(ContractLogicError, match="boom"):
            _fetch_vault_reads(ctx, _vault(ctx))


def _vault_data(unimplemented: list[str]) -> _VaultData:
    return _VaultData(
        block_number=1,
        is_latest=True,
        block_timestamp=0,
        share_decimals=6,
        asset_decimals=6,
        total_assets=0,
        total_supply=0,
        supply_cap=UINT256_MAX,
        asset=ASSET,
        asset_symbol="USDC",
        access_manager=ACCESS_MANAGER,
        price_oracle_addr=LEGACY_ORACLE,
        rewards_manager=None,
        withdraw_manager=None,
        asset_price_usd=None,
        fuses=[],
        balance_fuses=[BalanceFuse(MarketId(1), FUSE)],
        instant_fuses=[],
        unimplemented_getters=unimplemented,
    )


class TestReporting:
    def test_health_check_warns_per_unimplemented_getter(self):
        data = _vault_data([GET_TOTAL_SUPPLY_CAP, GET_MARKET_SUBSTRATES])

        health = _compute_health_check(
            data, _BalanceFuseTotals(), _Erc20Totals(), set()
        )

        predates = [w for w in health.warnings if "vault predates" in w]
        assert len(predates) == 2
        assert f"vault predates {GET_MARKET_SUBSTRATES}: substrates are" in predates[1]
        assert health.criticals == []

    def test_current_vault_has_no_predates_warning(self):
        health = _compute_health_check(
            _vault_data([]), _BalanceFuseTotals(), _Erc20Totals(), set()
        )

        assert not [w for w in health.warnings if "vault predates" in w]

    def test_substrates_section_says_unavailable(self, capsys):
        addrs = _print_market_substrates(
            MagicMock(), _vault_data([GET_MARKET_SUBSTRATES]), 42161, None
        )

        assert addrs == set()
        assert f"unavailable: the vault predates {GET_MARKET_SUBSTRATES}" in (
            capsys.readouterr().out
        )


class TestVaultInfoCommand:
    @pytest.fixture(autouse=True)
    def _tmp_config(self, tmp_path, monkeypatch):
        config_dir = tmp_path / "config"
        monkeypatch.setattr(config_store, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_store, "CONFIG_FILE", config_dir / "config.json")
        save_config(FusionConfig(providers={"42161": "https://rpc.example.com"}))

    @patch("ipor_fusion.cli.vault_cmd._auto_save_vault")
    @patch("ipor_fusion.cli.vault_cmd.resolve_access_manager")
    @patch("ipor_fusion.cli.vault_cmd.Web3Context")
    @patch("ipor_fusion.cli.vault_cmd._fetch_vault_data")
    def test_unsupported_vault_version_is_a_clean_error(
        self, mock_fetch, _ctx_cls, _probe, _save
    ):
        mock_fetch.side_effect = UnsupportedVaultVersionError("too old")

        result = CliRunner().invoke(
            cli, ["vault", "info", VAULT, "--chain-id", "42161"]
        )

        assert result.exit_code == 1
        assert "Error: too old" in result.output
        assert not isinstance(result.exception, UnsupportedVaultVersionError)

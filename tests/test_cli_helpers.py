# pylint: disable=unused-argument
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from unittest.mock import MagicMock, patch

import click
import pytest
from click.testing import CliRunner
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion.cli import config_store
from ipor_fusion.cli.config_store import FusionConfig, VaultEntry, save_contract_cache
from ipor_fusion.cli.vault_cmd import (
    ADDRESS,
    CHAIN,
    _build_dependency_graph_json,
    _build_share_price_json,
    _index_lending_health,
    _print_dependency_graph,
    _print_health_lines,
    _print_lending_health,
    _print_pending_requests,
    _print_substrates,
    _resolve_chain_id,
    _resolve_provider,
)
from ipor_fusion.cli.vault_fetcher import (
    _VaultData,
    _WithdrawManagerData,
    _collect_aave_substrate_assets,
    _collect_breakdown_token_addresses,
    _collect_morpho_substrates,
    _fetch_aave_positions,
    _fetch_breakdown_token_prices,
    _fetch_morpho_positions,
    _resolve_token_symbol,
    _safe_call,
)
from ipor_fusion.readers.aave_v3 import AaveV3PositionBreakdown
from ipor_fusion.readers.morpho import MorphoPositionBreakdown
from ipor_fusion.types import Amount, MorphoBlueMarketId
from ipor_fusion.cli.vault_health import (
    _BalanceFuseTotals,
    _Erc20Totals,
    _TokenInfo,
    _print_erc20_balances,
    _print_health_check,
    _print_reconciliation,
)
from ipor_fusion.readers.lending_health import (
    LendingMarketHealth,
    VaultLendingHealth,
)
from ipor_fusion.config.roles import Roles
from ipor_fusion.cli.vault_rendering import (
    _format_age,
    _format_amount,
    _format_remaining,
    _format_usd,
    _substrate_details,
)
from ipor_fusion.cli.vault_substrate import (
    _format_substrate,
    _market_name,
)
from ipor_fusion.market_ids import IporFusionMarkets


VALID_ADDR_LOWER = "0x" + "ab" * 20
VALID_ADDR_UPPER = "0x" + "AB" * 20
VALID_CHECKSUM = Web3.to_checksum_address("0x" + "ab" * 20)


def _convert_via_cli(raw_value: str) -> str:
    """Run AddressType.convert through a real Click command to get proper error handling."""
    result_holder: list[str] = []

    @click.command()
    @click.argument("addr", type=ADDRESS)
    def cmd(addr: str) -> None:
        result_holder.append(addr)

    runner = CliRunner()
    result = runner.invoke(cmd, [raw_value])
    if result.exit_code != 0:
        raise click.BadParameter(result.output.strip())
    return result_holder[0]


class TestAddressType:
    def test_valid_lowercase_with_0x(self):
        assert ADDRESS.convert(VALID_ADDR_LOWER, None, None) == VALID_CHECKSUM

    def test_valid_uppercase_with_0x(self):
        assert ADDRESS.convert(VALID_ADDR_UPPER, None, None) == VALID_CHECKSUM

    def test_valid_without_0x_prefix(self):
        assert ADDRESS.convert("ab" * 20, None, None) == VALID_CHECKSUM

    def test_valid_checksum_address(self):
        checksum = Web3.to_checksum_address("0x" + "1a2b3c4d5e" * 4)
        assert ADDRESS.convert(checksum, None, None) == checksum

    def test_bad_checksum_mixed_case(self):
        good = str(Web3.to_checksum_address("0x" + "1a2b3c4d5e" * 4))
        bad = good[:-1] + good[-1].swapcase()
        runner = CliRunner()

        @click.command()
        @click.argument("addr", type=ADDRESS)
        def cmd(addr: str) -> None:
            pass

        result = runner.invoke(cmd, [bad])
        assert result.exit_code != 0

    def test_too_short(self):
        runner = CliRunner()

        @click.command()
        @click.argument("addr", type=ADDRESS)
        def cmd(addr: str) -> None:
            pass

        result = runner.invoke(cmd, ["0x1234"])
        assert result.exit_code != 0
        assert "invalid Ethereum address" in result.output

    def test_non_hex_chars(self):
        runner = CliRunner()

        @click.command()
        @click.argument("addr", type=ADDRESS)
        def cmd(addr: str) -> None:
            pass

        result = runner.invoke(cmd, ["0x" + "zz" * 20])
        assert result.exit_code != 0
        assert "invalid Ethereum address" in result.output

    def test_non_string_input(self):
        with pytest.raises(click.exceptions.BadParameter):
            ADDRESS.convert(123, None, None)

    def test_strips_whitespace(self):
        assert ADDRESS.convert(f"  {VALID_ADDR_LOWER}  ", None, None) == VALID_CHECKSUM


class TestFormatAmount:
    def test_zero_decimals(self):
        assert _format_amount(123456, 0) == "123,456"

    def test_18_decimals(self):
        raw = 1_234_567_890_000_000_000_000
        result = _format_amount(raw, 18)
        assert result == "1,234.56789"

    def test_6_decimals(self):
        raw = 1_500_000
        result = _format_amount(raw, 6)
        assert result == "1.5"

    def test_trailing_zeros_stripped(self):
        raw = 1_000_000_000_000_000_000
        result = _format_amount(raw, 18)
        assert result == "1.0"

    def test_zero_amount(self):
        result = _format_amount(0, 18)
        assert result == "0.0"

    def test_fractional_only(self):
        raw = 500_000_000_000_000_000
        result = _format_amount(raw, 18)
        assert result == "0.5"

    def test_6_decimals_full_precision(self):
        raw = 1_234_567
        result = _format_amount(raw, 6)
        assert result == "1.234567"


class TestFormatUsd:
    def test_none_price_returns_empty(self):
        assert _format_usd(1_000_000, 6, None) == ""

    def test_valid_price(self):
        result = _format_usd(2_000_000, 6, 1.0)
        assert result == " ($2.00)"

    def test_zero_raw(self):
        result = _format_usd(0, 18, 2500.0)
        assert result == " ($0.00)"

    def test_large_value(self):
        raw = 1_000_000_000_000
        result = _format_usd(raw, 6, 1.0)
        assert result == " ($1,000,000.00)"


class TestFormatSubstrate:
    """Generic fallback (no market_id) — returns raw hex for everything."""

    def test_no_market_returns_raw_hex(self):
        raw = bytes.fromhex("ff" * 32)
        info = _format_substrate(raw)
        assert info.raw_hex == "0x" + "ff" * 32
        assert info.address == ""

    def test_wrong_length(self):
        raw = bytes.fromhex("aabb")
        info = _format_substrate(raw)
        assert info.raw_hex == "0xaabb"
        assert info.is_error is True


class TestFormatSubstratePerMarket:
    """Per-market decoders with explicit market_id."""

    # plain address markets
    def test_aave_v3_plain_address(self):
        addr_hex = "ab" * 20
        raw = bytes.fromhex("00" * 12 + addr_hex)
        info = _format_substrate(raw, market_id=1)
        assert info.address == f"0x{addr_hex}"
        assert info.type_label == ""

    # Ebisu (type<<160)
    def test_ebisu_zapper(self):
        addr_hex = "ab" * 20
        raw = bytes.fromhex("00" * 11 + "01" + addr_hex)
        info = _format_substrate(raw, market_id=39)
        assert info.address == f"0x{addr_hex}"
        assert info.type_label == "ZAPPER"

    def test_ebisu_registry(self):
        addr_hex = "cd" * 20
        raw = bytes.fromhex("00" * 11 + "02" + addr_hex)
        info = _format_substrate(raw, market_id=39)
        assert info.address == f"0x{addr_hex}"
        assert info.type_label == "REGISTRY"

    # Midas (type<<160)
    def test_midas_m_token(self):
        addr_hex = "cd" * 20
        raw = bytes.fromhex("00" * 11 + "01" + addr_hex)
        info = _format_substrate(raw, market_id=45)
        assert info.address == f"0x{addr_hex}"
        assert info.type_label == "M_TOKEN"

    def test_midas_deposit_vault(self):
        addr_hex = "cd" * 20
        raw = bytes.fromhex("00" * 11 + "02" + addr_hex)
        info = _format_substrate(raw, market_id=45)
        assert info.type_label == "DEPOSIT_VAULT"

    # Balancer (type<<160)
    def test_balancer_gauge(self):
        addr_hex = "ab" * 20
        raw = bytes.fromhex("00" * 11 + "01" + addr_hex)
        info = _format_substrate(raw, market_id=36)
        assert info.type_label == "GAUGE"

    def test_balancer_pool(self):
        addr_hex = "ab" * 20
        raw = bytes.fromhex("00" * 11 + "02" + addr_hex)
        info = _format_substrate(raw, market_id=36)
        assert info.type_label == "POOL"

    # Aave V4 (type<<248)
    def test_aave_v4_asset(self):
        addr_hex = "ab" * 20
        raw = bytes.fromhex("01" + "00" * 11 + addr_hex)
        info = _format_substrate(raw, market_id=44)
        assert info.address == f"0x{addr_hex}"
        assert info.type_label == "Asset"

    def test_aave_v4_spoke(self):
        addr_hex = "ab" * 20
        raw = bytes.fromhex("02" + "00" * 11 + addr_hex)
        info = _format_substrate(raw, market_id=44)
        assert info.type_label == "Spoke"

    # Odos (type<<248)
    def test_odos_token(self):
        addr_hex = "ab" * 20
        raw = bytes.fromhex("01" + "00" * 11 + addr_hex)
        info = _format_substrate(raw, market_id=42)
        assert info.address == f"0x{addr_hex}"
        assert info.type_label == "Token"

    def test_odos_slippage(self):
        raw = bytes.fromhex("02" + "00" * 27 + "000003e8")
        info = _format_substrate(raw, market_id=42)
        assert info.type_label == "Slippage"
        assert info.address == ""
        assert info.extra["value"] == "1000"

    # Morpho (raw bytes32)
    def test_morpho_market_id(self):
        raw = bytes.fromhex(
            "32e253d33f1594a67fc6ef51bf7a39cc4bf2d14904998dee769706fcde489ed9"
        )
        info = _format_substrate(raw, market_id=14)
        assert info.raw_hex.startswith("0x32e253")
        assert info.type_label == "morpho_market_id"
        assert info.address == ""

    # Enso (address<<96 | selector<<64)
    def test_enso(self):
        addr_hex = "ab" * 20
        selector = "12345678"
        raw = bytes.fromhex(addr_hex + selector + "00" * 8)
        info = _format_substrate(raw, market_id=38)
        assert info.address == f"0x{addr_hex}"
        assert info.extra["selector"] == f"0x{selector}"

    # Dolomite (asset<<96 | subAccountId<<88 | canBorrow<<80)
    def test_dolomite(self):
        addr_hex = "ab" * 20
        raw = bytes.fromhex(addr_hex + "05" + "01" + "00" * 10)
        info = _format_substrate(raw, market_id=46)
        assert info.address == f"0x{addr_hex}"
        assert info.extra["sub_account_id"] == "5"
        assert info.extra["can_borrow"] == "True"

    # Unknown market — raw hex with no_decoder label
    def test_unknown_market(self):
        raw = bytes.fromhex("ff" * 32)
        info = _format_substrate(raw, market_id=99999)
        assert info.raw_hex == "0x" + "ff" * 32
        assert "no_decoder" in info.type_label

    def test_non_address_32_bytes(self):
        raw = bytes.fromhex("ff" * 32)
        info = _format_substrate(raw)
        assert info.raw_hex == "0x" + "ff" * 32
        assert info.address == ""


class TestMarketName:
    def test_known_market(self):
        assert (
            _market_name(IporFusionMarkets.ERC20_VAULT_BALANCE) == "ERC20_VAULT_BALANCE"
        )

    def test_unknown_market(self):
        assert _market_name(999_999_999) == "UNKNOWN"


class TestResolveChainId:
    def test_returns_explicit_chain_id(self):
        cfg = FusionConfig()
        assert _resolve_chain_id(cfg, "0xABC", 42161) == 42161

    def test_returns_chain_id_from_saved_vault(self):
        cfg = FusionConfig(
            vaults=[VaultEntry(address="0xABC", label="test", chain_id=1)]
        )
        assert _resolve_chain_id(cfg, "0xABC", None) == 1

    def test_case_insensitive_match(self):
        cfg = FusionConfig(
            vaults=[VaultEntry(address="0xabc", label="test", chain_id=42161)]
        )
        assert _resolve_chain_id(cfg, "0xABC", None) == 42161

    def test_raises_when_unknown_vault(self):
        cfg = FusionConfig(providers={"42161": "https://arb-rpc.example.com"})
        with pytest.raises(click.UsageError, match="Unknown vault"):
            _resolve_chain_id(cfg, "0xABC", None)


class TestResolveProvider:
    def test_returns_provider_url(self):
        cfg = FusionConfig(providers={"1": "https://rpc.example.com"})
        assert _resolve_provider(cfg, 1) == "https://rpc.example.com"

    def test_raises_when_no_provider(self):
        cfg = FusionConfig()
        with pytest.raises(click.UsageError, match="No provider for chain 42161"):
            _resolve_provider(cfg, 42161)


class TestChainType:
    def test_numeric_string(self):
        assert CHAIN.convert("42161", None, None) == 42161

    def test_integer(self):
        assert CHAIN.convert(42161, None, None) == 42161

    def test_chain_name(self):
        assert CHAIN.convert("ethereum", None, None) == 1

    def test_chain_name_case_insensitive(self):
        assert CHAIN.convert("Base", None, None) == 8453

    def test_unknown_name_fails(self):
        with pytest.raises(click.exceptions.BadParameter):
            CHAIN.convert("unknown_chain", None, None)


class TestBuildSharePriceJson:
    def test_zero_supply_returns_none(self):
        data = _make_data(total_supply=0)
        assert _build_share_price_json(data) is None

    def test_basic_share_price(self):
        data = _make_data(
            total_assets=200 * 10**18,
            total_supply=100 * 10**18,
            asset_price_usd=None,
        )
        result = _build_share_price_json(data)
        assert result is not None
        assert result["asset"] == 2.0
        assert "usd" not in result

    def test_share_price_with_usd(self):
        data = _make_data(
            total_assets=200 * 10**18,
            total_supply=100 * 10**18,
            asset_price_usd=3000.0,
        )
        result = _build_share_price_json(data)
        assert result is not None
        assert result["asset"] == 2.0
        assert result["usd"] == 6000.0

    def test_fractional_share_price(self):
        data = _make_data(
            total_assets=50 * 10**18,
            total_supply=100 * 10**18,
            asset_price_usd=None,
        )
        result = _build_share_price_json(data)
        assert result is not None
        assert result["asset"] == 0.5


class TestBuildDependencyGraphJson:
    def test_none_when_no_graph(self):
        data = _make_data(dependency_graph=None)
        assert _build_dependency_graph_json(data) is None

    def test_none_when_empty_graph(self):
        data = _make_data(dependency_graph={})
        assert _build_dependency_graph_json(data) is None

    def test_known_markets(self):
        data = _make_data(
            dependency_graph={
                IporFusionMarkets.GEARBOX_FARM_DTOKEN_V3: [
                    IporFusionMarkets.GEARBOX_POOL_V3,
                ],
            }
        )
        result = _build_dependency_graph_json(data)
        assert result is not None
        assert "GEARBOX_FARM_DTOKEN_V3 (4)" in result["edges"]
        assert result["edges"]["GEARBOX_FARM_DTOKEN_V3 (4)"] == ["GEARBOX_POOL_V3 (3)"]
        assert "GEARBOX_FARM_DTOKEN_V3 (4)" in result["update_reach"]

    def test_unknown_market_id(self):
        data = _make_data(dependency_graph={999999: [7]})
        result = _build_dependency_graph_json(data)
        assert result is not None
        assert "999999" in result["edges"]
        assert result["edges"]["999999"] == ["ERC20_VAULT_BALANCE (7)"]

    def test_multiple_dependencies(self):
        data = _make_data(
            dependency_graph={
                IporFusionMarkets.UNISWAP_SWAP_V3: [
                    IporFusionMarkets.ERC20_VAULT_BALANCE,
                ],
                IporFusionMarkets.GEARBOX_FARM_DTOKEN_V3: [
                    IporFusionMarkets.GEARBOX_POOL_V3,
                ],
            }
        )
        result = _build_dependency_graph_json(data)
        assert result is not None
        assert len(result["edges"]) == 2

    def test_update_groups(self):
        data = _make_data(dependency_graph={14: [7, 100002], 100002: [7, 14]})
        result = _build_dependency_graph_json(data)
        assert result is not None
        assert len(result["update_groups"]) >= 1


class TestPrintDependencyGraph:
    def test_no_output_when_none(self, capsys):
        data = _make_data(dependency_graph=None)
        _print_dependency_graph(data)
        assert capsys.readouterr().out == ""

    def test_no_output_when_empty(self, capsys):
        data = _make_data(dependency_graph={})
        _print_dependency_graph(data)
        assert capsys.readouterr().out == ""

    def test_prints_known_markets(self, capsys):
        data = _make_data(
            dependency_graph={
                IporFusionMarkets.GEARBOX_FARM_DTOKEN_V3: [
                    IporFusionMarkets.GEARBOX_POOL_V3,
                ],
            }
        )
        _print_dependency_graph(data)
        out = capsys.readouterr().out
        assert "Dependency Balance Graph:" in out
        assert "GEARBOX_FARM_DTOKEN_V3 (4)" in out
        assert "GEARBOX_POOL_V3 (3)" in out
        assert "→" in out

    def test_prints_unknown_market(self, capsys):
        data = _make_data(dependency_graph={999999: [7]})
        _print_dependency_graph(data)
        out = capsys.readouterr().out
        assert "999999" in out
        assert "ERC20_VAULT_BALANCE (7)" in out


class TestSubstrateDetails:
    def test_all_parts(self):
        result = _substrate_details("USDC", "Circle", "ZAPPER")
        assert result == " (symbol=USDC, contract=Circle, substrate-type=ZAPPER)"

    def test_symbol_only(self):
        result = _substrate_details("USDC", "", "")
        assert result == " (symbol=USDC)"

    def test_contract_only(self):
        result = _substrate_details("", "Circle", "")
        assert result == " (contract=Circle)"

    def test_type_label_only(self):
        result = _substrate_details("", "", "REGISTRY")
        assert result == " (substrate-type=REGISTRY)"

    def test_no_parts(self):
        result = _substrate_details("", "", "")
        assert result == ""

    def test_symbol_and_contract(self):
        result = _substrate_details("WETH", "WrappedEther", "")
        assert result == " (symbol=WETH, contract=WrappedEther)"

    def test_extra_params(self):
        result = _substrate_details("USDC", "", "", extra={"trove": "0xabc"})
        assert "trove=0xabc" in result


class TestRolesGetName:
    def test_known_role(self):
        assert Roles.get_name(Roles.ALPHA_ROLE) == "ALPHA_ROLE"

    def test_unknown_role(self):
        result = Roles.get_name(999999)
        assert result.startswith("UNKNOWN_ROLE_")


class TestFormatAge:
    def test_today(self):
        import time  # pylint: disable=import-outside-toplevel

        assert _format_age(int(time.time())) == "today"

    def test_one_day_ago(self):
        import time  # pylint: disable=import-outside-toplevel

        assert _format_age(int(time.time()) - 86400) == "1 day ago"

    def test_multiple_days_ago(self):
        import time  # pylint: disable=import-outside-toplevel

        assert "days ago" in _format_age(int(time.time()) - 86400 * 5)


class TestFormatRemaining:
    def test_expired(self):
        assert _format_remaining(0) == "expired"
        assert _format_remaining(-100) == "expired"

    def test_minutes_only(self):
        result = _format_remaining(300)
        assert "5m left" in result

    def test_hours_and_minutes(self):
        result = _format_remaining(3700)
        assert "1h" in result
        assert "m left" in result

    def test_days_and_hours(self):
        result = _format_remaining(100000)
        assert "d" in result
        assert "h left" in result


class TestSafeCall:
    def test_returns_value_on_success(self):
        assert _safe_call(lambda: 42) == 42

    def test_returns_none_on_exception(self):
        def explode():
            raise ContractLogicError("boom")

        assert _safe_call(explode) is None


class TestResolveTokenSymbol:
    @pytest.fixture(autouse=True)
    def _tmp_config(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".fusion"
        cache_dir = tmp_path / ".cache"
        monkeypatch.setattr(config_store, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_store, "CONFIG_FILE", config_dir / "config.json")
        monkeypatch.setattr(config_store, "CACHE_DIR", cache_dir)
        monkeypatch.setattr(
            config_store, "CACHE_FILE", cache_dir / "contract_cache.json"
        )
        monkeypatch.setattr(
            config_store,
            "DEPLOYMENT_CACHE_FILE",
            cache_dir / "deployment_cache.json",
        )

    def test_returns_cached_symbol(self):
        save_contract_cache({"symbol:0xabc": "USDC"})
        ctx = MagicMock()
        assert _resolve_token_symbol(ctx, "0xabc") == "USDC"

    def test_returns_no_contract_for_eoa(self):
        ctx = MagicMock()
        ctx.web3.eth.get_code.return_value = b""
        result = _resolve_token_symbol(ctx, "0x" + "ab" * 20)
        assert result == "no contract"

    @patch("ipor_fusion.cli.vault_fetcher.ERC20")
    def test_fetches_symbol_from_chain(self, mock_erc20_cls):
        ctx = MagicMock()
        ctx.web3.eth.get_code.return_value = b"\x01"
        mock_erc20_cls.return_value.symbol.return_value = "WETH"
        result = _resolve_token_symbol(ctx, "0x" + "ab" * 20)
        assert result == "WETH"

    @patch("ipor_fusion.cli.vault_fetcher.ERC20")
    def test_returns_empty_on_symbol_error(self, mock_erc20_cls):
        ctx = MagicMock()
        ctx.web3.eth.get_code.return_value = b"\x01"
        mock_erc20_cls.return_value.symbol.side_effect = RuntimeError("revert")
        result = _resolve_token_symbol(ctx, "0x" + "ab" * 20)
        assert result == ""


ADDR_1 = "0x1111111111111111111111111111111111111111"
ADDR_2 = "0x2222222222222222222222222222222222222222"
ADDR_ORACLE = "0x4444444444444444444444444444444444444444"


@dataclass
class FakeBalanceFuse:
    market_id: int
    fuse: str


class TestPrintErc20Balances:
    @pytest.fixture(autouse=True)
    def _tmp_config(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".fusion"
        cache_dir = tmp_path / ".cache"
        monkeypatch.setattr(config_store, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_store, "CONFIG_FILE", config_dir / "config.json")
        monkeypatch.setattr(config_store, "CACHE_DIR", cache_dir)
        monkeypatch.setattr(
            config_store, "CACHE_FILE", cache_dir / "contract_cache.json"
        )
        monkeypatch.setattr(
            config_store,
            "DEPLOYMENT_CACHE_FILE",
            cache_dir / "deployment_cache.json",
        )

    def test_no_erc20_market(self, capsys):

        ctx = MagicMock()
        pv = MagicMock()
        data = _VaultData(
            block_label="1",
            block_timestamp=0,
            share_decimals=18,
            asset_decimals=18,
            total_assets=0,
            total_supply=0,
            supply_cap=0,
            asset=ADDR_2,
            asset_symbol="X",
            access_manager=ADDR_1,
            price_oracle_addr=ADDR_ORACLE,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=None,
            fuses=[],
            balance_fuses=[FakeBalanceFuse(market_id=999, fuse=ADDR_1)],
            instant_fuses=[],
        )
        _print_erc20_balances(ctx, pv, data)
        captured = capsys.readouterr()
        assert "(no ERC20_VAULT_BALANCE market)" in captured.out

    @patch("ipor_fusion.cli.vault_health._resolve_token_symbol", return_value="USDC")
    @patch("ipor_fusion.cli.vault_health.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_health.ERC20")
    def test_with_erc20_tokens(
        self, mock_erc20_cls, mock_oracle_cls, mock_resolve, capsys
    ):

        ctx = MagicMock()
        pv = MagicMock()
        pv.address = ADDR_1

        addr_bytes = bytes.fromhex("00" * 12 + "ab" * 20)
        pv.get_market_substrates.return_value = [addr_bytes]

        mock_erc20 = MagicMock()
        mock_erc20.decimals.return_value = 6
        mock_erc20.balance_of.return_value = 1_000_000
        mock_erc20_cls.return_value = mock_erc20

        mock_price = MagicMock()
        mock_price.readable.return_value = 1.0
        mock_oracle_cls.return_value.get_asset_price.return_value = mock_price

        data = _VaultData(
            block_label="1",
            block_timestamp=0,
            share_decimals=18,
            asset_decimals=18,
            total_assets=0,
            total_supply=0,
            supply_cap=0,
            asset=ADDR_2,
            asset_symbol="X",
            access_manager=ADDR_1,
            price_oracle_addr=ADDR_ORACLE,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=None,
            fuses=[],
            balance_fuses=[
                FakeBalanceFuse(
                    market_id=IporFusionMarkets.ERC20_VAULT_BALANCE, fuse=ADDR_1
                )
            ],
            instant_fuses=[],
        )
        _print_erc20_balances(ctx, pv, data)
        captured = capsys.readouterr()
        assert "USDC" in captured.out
        assert "$1.00" in captured.out

    @patch("ipor_fusion.cli.vault_health._resolve_token_symbol", return_value="?")
    @patch("ipor_fusion.cli.vault_health.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_health.ERC20")
    def test_with_error_balances(
        self, mock_erc20_cls, mock_oracle_cls, mock_resolve, capsys
    ):

        ctx = MagicMock()
        pv = MagicMock()
        pv.address = ADDR_1

        addr_bytes = bytes.fromhex("00" * 12 + "ab" * 20)
        pv.get_market_substrates.return_value = [addr_bytes]

        mock_erc20 = MagicMock()
        mock_erc20.decimals.side_effect = ContractLogicError("fail")
        mock_erc20.balance_of.side_effect = ContractLogicError("fail")
        mock_erc20_cls.return_value = mock_erc20
        mock_oracle_cls.return_value.get_asset_price.side_effect = ContractLogicError(
            "fail"
        )

        data = _VaultData(
            block_label="1",
            block_timestamp=0,
            share_decimals=18,
            asset_decimals=18,
            total_assets=0,
            total_supply=0,
            supply_cap=0,
            asset=ADDR_2,
            asset_symbol="X",
            access_manager=ADDR_1,
            price_oracle_addr=ADDR_ORACLE,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=None,
            fuses=[],
            balance_fuses=[
                FakeBalanceFuse(
                    market_id=IporFusionMarkets.ERC20_VAULT_BALANCE, fuse=ADDR_1
                )
            ],
            instant_fuses=[],
        )
        _print_erc20_balances(ctx, pv, data)
        captured = capsys.readouterr()
        assert "error" in captured.out

    @patch("ipor_fusion.cli.vault_health.PriceOracleMiddleware")
    def test_no_token_addrs(self, mock_oracle_cls, capsys):

        ctx = MagicMock()
        pv = MagicMock()
        pv.address = ADDR_1
        pv.get_market_substrates.return_value = []

        data = _VaultData(
            block_label="1",
            block_timestamp=0,
            share_decimals=18,
            asset_decimals=18,
            total_assets=0,
            total_supply=0,
            supply_cap=0,
            asset=ADDR_2,
            asset_symbol="X",
            access_manager=ADDR_1,
            price_oracle_addr=ADDR_ORACLE,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=None,
            fuses=[],
            balance_fuses=[
                FakeBalanceFuse(
                    market_id=IporFusionMarkets.ERC20_VAULT_BALANCE, fuse=ADDR_1
                )
            ],
            instant_fuses=[],
        )
        _print_erc20_balances(ctx, pv, data)
        captured = capsys.readouterr()
        assert "(none)" in captured.out


class TestPrintSubstrates:
    @pytest.fixture(autouse=True)
    def _tmp_config(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".fusion"
        cache_dir = tmp_path / ".cache"
        monkeypatch.setattr(config_store, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_store, "CONFIG_FILE", config_dir / "config.json")
        monkeypatch.setattr(config_store, "CACHE_DIR", cache_dir)
        monkeypatch.setattr(
            config_store, "CACHE_FILE", cache_dir / "contract_cache.json"
        )
        monkeypatch.setattr(
            config_store,
            "DEPLOYMENT_CACHE_FILE",
            cache_dir / "deployment_cache.json",
        )

    def test_no_substrates(self, capsys):
        ctx = MagicMock()
        pv = MagicMock()
        pv.get_market_substrates.return_value = []
        _print_substrates(ctx, pv, [FakeBalanceFuse(market_id=1, fuse=ADDR_1)], 1, None)
        captured = capsys.readouterr()
        assert "(none)" in captured.out

    @patch("ipor_fusion.cli.vault_cmd.get_contract_name", return_value="TestContract")
    @patch("ipor_fusion.cli.vault_cmd._resolve_token_symbol", return_value="WETH")
    def test_with_substrates(self, mock_resolve, mock_get_name, capsys):
        ctx = MagicMock()
        pv = MagicMock()
        addr_bytes = bytes.fromhex("00" * 12 + "ab" * 20)
        pv.get_market_substrates.return_value = [addr_bytes]

        _print_substrates(
            ctx, pv, [FakeBalanceFuse(market_id=7, fuse=ADDR_1)], 1, "key"
        )
        captured = capsys.readouterr()
        assert "WETH" in captured.out
        assert "TestContract" in captured.out

    def test_with_morpho_substrate(self, capsys):
        ctx = MagicMock()
        pv = MagicMock()
        raw_bytes32 = bytes.fromhex("ff" * 32)
        pv.get_market_substrates.return_value = [raw_bytes32]

        _print_substrates(
            ctx, pv, [FakeBalanceFuse(market_id=14, fuse=ADDR_1)], 1, None
        )
        captured = capsys.readouterr()
        assert "morpho_market_id" in captured.out
        assert "[encoding error]" not in captured.out

    def test_with_encoding_error_substrate(self, capsys):
        ctx = MagicMock()
        pv = MagicMock()
        bad_bytes = bytes.fromhex("ff" * 16)
        pv.get_market_substrates.return_value = [bad_bytes]

        _print_substrates(ctx, pv, [FakeBalanceFuse(market_id=7, fuse=ADDR_1)], 1, None)
        captured = capsys.readouterr()
        assert "[encoding error]" in captured.out


class TestAddressTypeChecksumException:
    @patch("ipor_fusion.cli.vault_cmd.Web3.to_checksum_address")
    def test_checksum_exception_caught(self, mock_checksum):
        mock_checksum.side_effect = Exception("unexpected error")
        mixed = "0xAbCdEf1234567890aBcDeF1234567890AbCdEf12"
        with pytest.raises(
            click.exceptions.BadParameter, match="invalid address checksum"
        ):
            ADDRESS.convert(mixed, None, None)


class TestPrintReconciliation:
    def test_within_threshold(self, capsys):
        data = _VaultData(
            block_label="1",
            block_timestamp=0,
            share_decimals=18,
            asset_decimals=18,
            total_assets=100 * 10**18,
            total_supply=0,
            supply_cap=0,
            asset=ADDR_2,
            asset_symbol="WETH",
            access_manager=ADDR_1,
            price_oracle_addr=ADDR_1,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=2000.0,
            fuses=[],
            balance_fuses=[],
            instant_fuses=[],
        )
        bf = _BalanceFuseTotals(raw_total=60 * 10**18, usd_total=120000.0)
        erc20 = _Erc20Totals(
            raw_asset_total=40 * 10**18,
            usd_total=80000.0,
            underlying_balance_raw=40 * 10**18,
            token_info={
                ADDR_2.lower(): _TokenInfo(
                    symbol="WETH",
                    balance_str="40.0",
                    usd_value=80000.0,
                )
            },
        )
        _print_reconciliation(data, bf, erc20)
        captured = capsys.readouterr()
        assert "Balance Reconciliation:" in captured.out
        assert "Balance fuses total:" in captured.out
        assert "Underlying on vault:" in captured.out
        assert "Delta:" in captured.out
        assert "MISMATCH" not in captured.out

    def test_mismatch_over_1pct(self, capsys):
        data = _VaultData(
            block_label="1",
            block_timestamp=0,
            share_decimals=18,
            asset_decimals=18,
            total_assets=100 * 10**18,
            total_supply=0,
            supply_cap=0,
            asset=ADDR_2,
            asset_symbol="WETH",
            access_manager=ADDR_1,
            price_oracle_addr=ADDR_1,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=2000.0,
            fuses=[],
            balance_fuses=[],
            instant_fuses=[],
        )
        bf = _BalanceFuseTotals(raw_total=80 * 10**18, usd_total=160000.0)
        erc20 = _Erc20Totals(
            raw_asset_total=40 * 10**18,
            usd_total=80000.0,
            underlying_balance_raw=40 * 10**18,
            token_info={
                ADDR_2.lower(): _TokenInfo(
                    symbol="WETH",
                    balance_str="40.0",
                    usd_value=80000.0,
                )
            },
        )
        _print_reconciliation(data, bf, erc20)
        captured = capsys.readouterr()
        assert "MISMATCH" in captured.out


class TestErc20BalancesNotes:
    @pytest.fixture(autouse=True)
    def _tmp_config(self, tmp_path, monkeypatch):
        config_dir = tmp_path / ".fusion"
        cache_dir = tmp_path / ".cache"
        monkeypatch.setattr(config_store, "CONFIG_DIR", config_dir)
        monkeypatch.setattr(config_store, "CONFIG_FILE", config_dir / "config.json")
        monkeypatch.setattr(config_store, "CACHE_DIR", cache_dir)
        monkeypatch.setattr(
            config_store, "CACHE_FILE", cache_dir / "contract_cache.json"
        )
        monkeypatch.setattr(
            config_store,
            "DEPLOYMENT_CACHE_FILE",
            cache_dir / "deployment_cache.json",
        )

    @patch("ipor_fusion.cli.vault_health._resolve_token_symbol", return_value="TKN")
    @patch("ipor_fusion.cli.vault_health.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_health.ERC20")
    def test_balance_zero_note(
        self, mock_erc20_cls, mock_oracle_cls, mock_resolve, capsys
    ):
        ctx = MagicMock()
        pv = MagicMock()
        pv.address = ADDR_1

        addr_bytes = bytes.fromhex("00" * 12 + "ab" * 20)
        pv.get_market_substrates.return_value = [addr_bytes]

        mock_erc20 = MagicMock()
        mock_erc20.decimals.return_value = 18
        mock_erc20.balance_of.return_value = 0
        mock_erc20_cls.return_value = mock_erc20

        mock_price = MagicMock()
        mock_price.readable.return_value = 1.0
        mock_oracle_cls.return_value.get_asset_price.return_value = mock_price

        data = _VaultData(
            block_label="1",
            block_timestamp=0,
            share_decimals=18,
            asset_decimals=18,
            total_assets=0,
            total_supply=0,
            supply_cap=0,
            asset=ADDR_2,
            asset_symbol="X",
            access_manager=ADDR_1,
            price_oracle_addr=ADDR_1,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=1.0,
            fuses=[],
            balance_fuses=[
                FakeBalanceFuse(
                    market_id=IporFusionMarkets.ERC20_VAULT_BALANCE, fuse=ADDR_1
                )
            ],
            instant_fuses=[],
        )
        totals = _print_erc20_balances(ctx, pv, data)
        captured = capsys.readouterr()
        assert "balance=0" in captured.out
        assert isinstance(totals.raw_asset_total, int)

    @patch("ipor_fusion.cli.vault_health._resolve_token_symbol", return_value="WETH")
    @patch("ipor_fusion.cli.vault_health.PriceOracleMiddleware")
    @patch("ipor_fusion.cli.vault_health.ERC20")
    def test_underlying_and_stale_notes(
        self, mock_erc20_cls, mock_oracle_cls, mock_resolve, capsys
    ):
        ctx = MagicMock()
        pv = MagicMock()
        pv.address = ADDR_1

        # Two substrates: underlying asset (ADDR_2) and another token
        underlying_bytes = bytes.fromhex("00" * 12 + ADDR_2[2:].lower())
        other_bytes = bytes.fromhex("00" * 12 + "cc" * 20)
        pv.get_market_substrates.return_value = [underlying_bytes, other_bytes]
        pv.total_assets_in_market.return_value = 1  # tiny cached value

        mock_erc20 = MagicMock()
        mock_erc20.decimals.return_value = 18
        mock_erc20.balance_of.return_value = 10 * 10**18
        mock_erc20_cls.return_value = mock_erc20

        mock_price = MagicMock()
        mock_price.readable.return_value = 2000.0
        mock_oracle_cls.return_value.get_asset_price.return_value = mock_price

        data = _VaultData(
            block_label="1",
            block_timestamp=0,
            share_decimals=18,
            asset_decimals=18,
            total_assets=100 * 10**18,
            total_supply=0,
            supply_cap=0,
            asset=ADDR_2,
            asset_symbol="WETH",
            access_manager=ADDR_1,
            price_oracle_addr=ADDR_1,
            rewards_manager=None,
            withdraw_manager=None,
            asset_price_usd=2000.0,
            fuses=[],
            balance_fuses=[
                FakeBalanceFuse(
                    market_id=IporFusionMarkets.ERC20_VAULT_BALANCE, fuse=ADDR_1
                )
            ],
            instant_fuses=[],
        )
        totals = _print_erc20_balances(ctx, pv, data)
        captured = capsys.readouterr()
        assert "underlying asset" in captured.out
        assert "not reflected in totalAssets" in captured.out
        assert totals.token_addrs_on_vault


def _make_data(**overrides):
    defaults = {
        "block_label": "1",
        "block_timestamp": 0,
        "share_decimals": 18,
        "asset_decimals": 18,
        "total_assets": 100 * 10**18,
        "total_supply": 0,
        "supply_cap": 0,
        "asset": ADDR_2,
        "vault_name": "Test Vault",
        "asset_symbol": "WETH",
        "access_manager": ADDR_1,
        "price_oracle_addr": ADDR_1,
        "rewards_manager": None,
        "withdraw_manager": None,
        "asset_price_usd": 2000.0,
        "fuses": [],
        "balance_fuses": [],
        "instant_fuses": [],
        "dependency_graph": None,
        "lending_health": None,
    }
    defaults.update(overrides)
    return _VaultData(**defaults)


class TestHealthCheck:
    def test_balanced(self, capsys):
        data = _make_data()
        bf = _BalanceFuseTotals(raw_total=100 * 10**18)
        erc20 = _Erc20Totals()
        _print_health_check(data, bf, erc20, set())
        captured = capsys.readouterr()
        assert "0.00%" in captured.out

    def test_imbalanced(self, capsys):
        data = _make_data()
        bf = _BalanceFuseTotals(raw_total=50 * 10**18)
        erc20 = _Erc20Totals()
        _print_health_check(data, bf, erc20, set())
        captured = capsys.readouterr()
        assert "50.00%" in captured.out

    def test_stale_cache(self, capsys):
        data = _make_data()
        bf = _BalanceFuseTotals(raw_total=100 * 10**18)
        erc20 = _Erc20Totals(raw_asset_total=50 * 10**18, cached_bf_value=1 * 10**18)
        _print_health_check(data, bf, erc20, set())
        captured = capsys.readouterr()
        assert "updateMarketsBalances" in captured.out

    def test_uncovered_token(self, capsys):
        data = _make_data()
        bf = _BalanceFuseTotals(raw_total=100 * 10**18)
        erc20 = _Erc20Totals(
            token_addrs_on_vault={"0xabc"},
            token_info={
                "0xabc": _TokenInfo(symbol="TKN", balance_str="100", usd_value=500.0)
            },
        )
        _print_health_check(data, bf, erc20, set())
        captured = capsys.readouterr()
        assert "not in any balance fuse substrate" in captured.out

    def test_underlying_not_flagged(self, capsys):
        data = _make_data()
        bf = _BalanceFuseTotals(raw_total=100 * 10**18)
        erc20 = _Erc20Totals(token_addrs_on_vault={ADDR_2.lower()})
        _print_health_check(data, bf, erc20, set())
        captured = capsys.readouterr()
        assert "not in any" not in captured.out

    def test_no_price_feed(self, capsys):
        data = _make_data()
        bf = _BalanceFuseTotals(raw_total=100 * 10**18)
        erc20 = _Erc20Totals(tokens_without_price=["0xabc (TKN)"])
        _print_health_check(data, bf, erc20, set())
        captured = capsys.readouterr()
        assert "No price feed" in captured.out


def _make_lending_market(**overrides):
    defaults = {
        "protocol": "morpho",
        "market_id": 1,
        "market_name": "Morpho",
        "current_ltv": 0.5,
        "max_ltv": 0.8,
        "health_factor": 1.6,
        "total_collateral_usd": None,
        "total_debt_usd": None,
        "ltv_usage_percent": 62.5,
    }
    defaults.update(overrides)
    return LendingMarketHealth(**defaults)


class TestPrintLendingHealth:
    def test_no_lending_positions(self, capsys):
        data = _make_data()
        _print_lending_health(MagicMock(), data)
        captured = capsys.readouterr()
        assert "no lending positions" in captured.out

    def test_orphan_morpho_health_renders_without_breakdown(self, capsys):
        m = _make_lending_market(substrate_id="ab" * 32, ltv_usage_percent=50.0)
        m.protocol = "morpho"
        data = _make_data(lending_health=VaultLendingHealth(markets=[m]))
        _print_lending_health(MagicMock(), data)
        out = capsys.readouterr().out
        assert "Position Breakdown:" in out
        assert f"morpho market 0x{'ab' * 32}" in out
        assert "Status:        OK" in out

    def test_orphan_aave_health_renders_without_breakdown(self, capsys):
        m = _make_lending_market(health_factor=1.4, ltv_usage_percent=70.0)
        m.protocol = "aave_v3"
        m.market_id = 1
        data = _make_data(lending_health=VaultLendingHealth(markets=[m]))
        _print_lending_health(MagicMock(), data)
        out = capsys.readouterr().out
        assert "AAVE_V3 (1):" in out
        assert "Health Factor:" in out

    def test_orphan_health_critical_status_colored(self, capsys):
        m = _make_lending_market(health_factor=1.03)
        m.protocol = "aave_v3"
        m.market_id = 1
        data = _make_data(lending_health=VaultLendingHealth(markets=[m]))
        _print_lending_health(MagicMock(), data)
        assert "CRITICAL" in capsys.readouterr().out

    def test_none_ltv_shows_na(self, capsys):
        m = _make_lending_market(
            current_ltv=None,
            health_factor=None,
            ltv_usage_percent=None,
        )
        m.protocol = "aave_v3"
        m.market_id = 1
        data = _make_data(lending_health=VaultLendingHealth(markets=[m]))
        _print_lending_health(MagicMock(), data)
        assert "N/A" in capsys.readouterr().out


class TestHealthCheckLendingIntegration:
    def test_lending_ok_in_health_check(self, capsys):
        m = _make_lending_market(ltv_usage_percent=50.0)
        data = _make_data(lending_health=VaultLendingHealth(markets=[m]))
        bf = _BalanceFuseTotals(raw_total=100 * 10**18)
        erc20 = _Erc20Totals()
        _print_health_check(data, bf, erc20, set())
        captured = capsys.readouterr()
        assert "morpho Morpho" in captured.out

    def test_lending_warning_in_health_check(self, capsys):
        m = _make_lending_market(health_factor=1.08)
        data = _make_data(lending_health=VaultLendingHealth(markets=[m]))
        bf = _BalanceFuseTotals(raw_total=100 * 10**18)
        erc20 = _Erc20Totals()
        _print_health_check(data, bf, erc20, set())
        captured = capsys.readouterr()
        assert "WARNING" in captured.out

    def test_lending_critical_in_health_check(self, capsys):
        m = _make_lending_market(health_factor=1.03)
        data = _make_data(lending_health=VaultLendingHealth(markets=[m]))
        bf = _BalanceFuseTotals(raw_total=100 * 10**18)
        erc20 = _Erc20Totals()
        _print_health_check(data, bf, erc20, set())
        captured = capsys.readouterr()
        assert "CRITICAL" in captured.out
        assert "NEAR LIQUIDATION" in captured.out

    def test_lending_none_usage_skipped(self, capsys):
        m = _make_lending_market(ltv_usage_percent=None, health_factor=None)
        data = _make_data(lending_health=VaultLendingHealth(markets=[m]))
        bf = _BalanceFuseTotals(raw_total=100 * 10**18)
        erc20 = _Erc20Totals()
        _print_health_check(data, bf, erc20, set())
        captured = capsys.readouterr()
        assert "morpho" not in captured.out


# ── Lending Position Breakdown ─────────────────────────────────────────


_VAULT_ADDR = Web3.to_checksum_address("0x" + "11" * 20)
_TOKEN_USDC = Web3.to_checksum_address("0x" + "22" * 20)
_TOKEN_WETH = Web3.to_checksum_address("0x" + "33" * 20)


def _morpho_breakdown(market_id: str = "ab" * 32):
    return MorphoPositionBreakdown(
        market_id=MorphoBlueMarketId(market_id),
        loan_token=_TOKEN_USDC,
        collateral_token=_TOKEN_WETH,
        collateral=Amount(10 * 10**18),
        borrow_assets=Amount(5_000 * 10**6),
        supply_assets=Amount(0),
    )


def _aave_breakdown(asset=_TOKEN_USDC, supply=0, variable_debt=0, stable_debt=0):
    return AaveV3PositionBreakdown(
        asset=asset,
        a_token=Web3.to_checksum_address("0x" + "44" * 20),
        variable_debt_token=Web3.to_checksum_address("0x" + "55" * 20),
        stable_debt_token=Web3.to_checksum_address("0x" + "66" * 20),
        supply=Amount(supply),
        variable_debt=Amount(variable_debt),
        stable_debt=Amount(stable_debt),
    )


class TestPrintLendingPositionBreakdown:
    def test_no_positions_shows_no_lending(self, capsys):
        data = _make_data()
        _print_lending_health(MagicMock(), data)
        captured = capsys.readouterr()
        assert "no lending positions" in captured.out

    @patch("ipor_fusion.cli.vault_fetcher._resolve_token_decimals", return_value=18)
    @patch("ipor_fusion.cli.vault_fetcher._resolve_token_symbol", return_value="WETH")
    def test_morpho_breakdown_renders_three_lines(
        self, _mock_sym, _mock_dec, capsys
    ):
        data = _make_data(morpho_positions={14: [_morpho_breakdown()]})
        _print_lending_health(MagicMock(), data)
        captured = capsys.readouterr()
        assert "Position Breakdown:" in captured.out
        assert "morpho market 0x" in captured.out
        assert "Collateral:" in captured.out
        assert "Borrow:" in captured.out
        assert "Supply:" in captured.out

    @patch("ipor_fusion.cli.vault_fetcher._resolve_token_decimals", return_value=6)
    @patch("ipor_fusion.cli.vault_fetcher._resolve_token_symbol", return_value="USDC")
    def test_aave_breakdown_omits_zero_stable_debt(
        self, _mock_sym, _mock_dec, capsys
    ):
        data = _make_data(
            aave_positions={
                1: [_aave_breakdown(supply=100, variable_debt=50, stable_debt=0)]
            }
        )
        _print_lending_health(MagicMock(), data)
        captured = capsys.readouterr()
        assert "asset 0x" in captured.out
        assert "Supply:" in captured.out
        assert "Variable Debt:" in captured.out
        assert "Stable Debt:" not in captured.out

    @patch("ipor_fusion.cli.vault_fetcher._resolve_token_decimals", return_value=6)
    @patch("ipor_fusion.cli.vault_fetcher._resolve_token_symbol", return_value="USDC")
    def test_aave_breakdown_includes_stable_debt_when_nonzero(
        self, _mock_sym, _mock_dec, capsys
    ):
        data = _make_data(
            aave_positions={
                1: [_aave_breakdown(supply=0, variable_debt=0, stable_debt=42)]
            }
        )
        _print_lending_health(MagicMock(), data)
        captured = capsys.readouterr()
        assert "Stable Debt:" in captured.out


class TestInlineHealth:
    def test_index_lending_health_splits_by_protocol(self):
        morpho_m = _make_lending_market(substrate_id="ab" * 32)
        morpho_m.protocol = "morpho"
        aave_m = _make_lending_market()
        aave_m.protocol = "aave_v3"
        aave_m.market_id = 1
        lh = VaultLendingHealth(markets=[morpho_m, aave_m])

        morpho_index, aave_index = _index_lending_health(lh)

        assert "ab" * 32 in morpho_index
        assert 1 in aave_index

    def test_index_lending_health_handles_none(self):
        morpho_index, aave_index = _index_lending_health(None)
        assert not morpho_index
        assert not aave_index

    def test_health_lines_ok_no_color(self, capsys):
        m = _make_lending_market(ltv_usage_percent=50.0, health_factor=1.6)
        _print_health_lines(m, indent="    ")
        out = capsys.readouterr().out
        assert "LTV:" in out
        assert "Health Factor: 1.6000" in out
        assert "Status:        OK" in out

    def test_health_lines_warning_yellow(self, capsys):
        m = _make_lending_market(health_factor=1.08)
        _print_health_lines(m, indent="    ")
        out = capsys.readouterr().out
        assert "WARNING" in out

    def test_health_lines_critical_red(self, capsys):
        m = _make_lending_market(health_factor=1.03)
        _print_health_lines(m, indent="    ")
        out = capsys.readouterr().out
        assert "CRITICAL" in out

    @patch("ipor_fusion.cli.vault_cmd._resolve_token_symbol")
    @patch("ipor_fusion.cli.vault_fetcher._resolve_token_decimals", return_value=18)
    @patch("ipor_fusion.cli.vault_fetcher._resolve_token_symbol", return_value="WETH")
    def test_morpho_market_renders_inline_health(
        self, _mock_fetcher_sym, _mock_dec, mock_cmd_sym, capsys
    ):
        mock_cmd_sym.side_effect = lambda _ctx, addr: {
            _TOKEN_USDC: "USDC",
            _TOKEN_WETH: "WETH",
        }.get(addr, "?")
        market = _make_lending_market(substrate_id="ab" * 32, ltv_usage_percent=50.0)
        market.protocol = "morpho"
        data = _make_data(
            lending_health=VaultLendingHealth(markets=[market]),
            morpho_positions={14: [_morpho_breakdown(market_id="ab" * 32)]},
        )
        _print_lending_health(MagicMock(), data)
        out = capsys.readouterr().out
        # Per-substrate header lists the token pair
        assert f"morpho market 0x{'ab' * 32} (WETH/USDC)" in out
        # Inline health below breakdown
        assert "Collateral:" in out
        assert "Borrow:" in out
        assert "Supply:" in out
        assert "Health Factor:" in out
        assert "Status:" in out

    @patch("ipor_fusion.cli.vault_cmd._resolve_token_symbol", return_value="USDC")
    @patch("ipor_fusion.cli.vault_fetcher._resolve_token_decimals", return_value=6)
    @patch("ipor_fusion.cli.vault_fetcher._resolve_token_symbol", return_value="USDC")
    def test_aave_market_renders_inline_health_after_assets(
        self, _mock_f_sym, _mock_dec, _mock_cmd_sym, capsys
    ):
        market = _make_lending_market(ltv_usage_percent=70.0, health_factor=1.4)
        market.protocol = "aave_v3"
        market.market_id = 1
        data = _make_data(
            lending_health=VaultLendingHealth(markets=[market]),
            aave_positions={1: [_aave_breakdown(supply=100, variable_debt=50)]},
        )
        _print_lending_health(MagicMock(), data)
        out = capsys.readouterr().out
        assert "asset 0x" in out
        # Health appears once for the aggregated Aave market
        assert out.count("Health Factor:") == 1
        assert out.count("LTV:") == 1


# ── vault_fetcher Aave / Morpho substrate collectors and fetchers ──────


class TestFetcherCollectors:
    def test_collect_morpho_substrates_filters_non_morpho(self):
        morpho_id = bytes.fromhex("aa" * 32)
        out = _collect_morpho_substrates(
            {14: [morpho_id], 1: [bytes.fromhex("bb" * 32)]}
        )
        assert 14 in out and 1 not in out
        assert len(out[14]) == 1

    def test_collect_morpho_substrates_skips_wrong_length(self):
        bad = bytes.fromhex("aa" * 16)  # wrong length
        out = _collect_morpho_substrates({14: [bad]})
        assert not out

    def test_collect_aave_substrate_assets_returns_addresses(self):
        # Aave substrate = zero-padded address (12 zero bytes + 20 address bytes)
        addr_bytes = bytes(12) + bytes.fromhex("22" * 20)
        out = _collect_aave_substrate_assets({1: [addr_bytes]})
        assert 1 in out
        assert out[1] == [Web3.to_checksum_address("0x" + "22" * 20)]

    def test_collect_aave_substrate_assets_filters_non_aave(self):
        addr_bytes = bytes(12) + bytes.fromhex("22" * 20)
        out = _collect_aave_substrate_assets({14: [addr_bytes]})
        assert not out


class TestFetchAavePositions:
    @patch("ipor_fusion.cli.vault_fetcher.AaveV3Reader")
    def test_returns_none_when_chain_unsupported(self, _mock_reader_cls):
        with ThreadPoolExecutor() as pool:
            result = _fetch_aave_positions(MagicMock(), pool, _VAULT_ADDR, 999, {})
        assert result is None

    @patch("ipor_fusion.cli.vault_fetcher.AaveV3Reader")
    def test_returns_none_when_no_aave_substrates(self, _mock_reader_cls):
        with ThreadPoolExecutor() as pool:
            result = _fetch_aave_positions(MagicMock(), pool, _VAULT_ADDR, 1, {})
        assert result is None

    @patch("ipor_fusion.cli.vault_fetcher.AaveV3Reader")
    def test_drops_empty_breakdowns(self, mock_reader_cls):
        mock_reader = MagicMock()
        mock_reader.position_breakdown.return_value = _aave_breakdown(
            supply=0, variable_debt=0, stable_debt=0
        )
        mock_reader_cls.return_value = mock_reader

        addr_bytes = bytes(12) + bytes.fromhex("22" * 20)
        with ThreadPoolExecutor() as pool:
            result = _fetch_aave_positions(
                MagicMock(), pool, _VAULT_ADDR, 1, {1: [addr_bytes]}
            )
        assert result is None

    @patch("ipor_fusion.cli.vault_fetcher.AaveV3Reader")
    def test_returns_breakdowns_for_active_positions(self, mock_reader_cls):
        mock_reader = MagicMock()
        mock_reader.position_breakdown.return_value = _aave_breakdown(supply=42)
        mock_reader_cls.return_value = mock_reader

        addr_bytes = bytes(12) + bytes.fromhex("22" * 20)
        with ThreadPoolExecutor() as pool:
            result = _fetch_aave_positions(
                MagicMock(), pool, _VAULT_ADDR, 1, {1: [addr_bytes]}
            )
        assert result == {1: [_aave_breakdown(supply=42)]}


class TestFetchMorphoPositions:
    @patch("ipor_fusion.cli.vault_fetcher.MorphoReader")
    def test_returns_none_when_no_morpho_substrates(self, _mock_reader_cls):
        with ThreadPoolExecutor() as pool:
            result = _fetch_morpho_positions(MagicMock(), pool, _VAULT_ADDR, {})
        assert result is None

    @patch("ipor_fusion.cli.vault_fetcher.MorphoReader")
    def test_returns_breakdowns_for_morpho_substrates(self, mock_reader_cls):
        mock_reader = MagicMock()
        mock_reader.position_breakdown.return_value = _morpho_breakdown()
        mock_reader_cls.return_value = mock_reader

        morpho_sub = bytes.fromhex("ab" * 32)
        with ThreadPoolExecutor() as pool:
            result = _fetch_morpho_positions(
                MagicMock(), pool, _VAULT_ADDR, {14: [morpho_sub]}
            )
        assert result == {14: [_morpho_breakdown()]}


class TestBreakdownTokenPrices:
    def test_collect_addresses_unions_morpho_and_aave(self):
        morpho = {14: [_morpho_breakdown()]}
        aave = {1: [_aave_breakdown(asset=_TOKEN_WETH)]}
        addrs = _collect_breakdown_token_addresses(morpho, aave)
        # morpho contributes loan_token + collateral_token
        assert _TOKEN_USDC in addrs
        assert _TOKEN_WETH in addrs

    def test_collect_addresses_handles_none(self):
        assert _collect_breakdown_token_addresses(None, None) == set()

    def test_fetch_prices_returns_none_for_empty_set(self):
        with ThreadPoolExecutor() as pool:
            assert _fetch_breakdown_token_prices(pool, MagicMock(), set()) is None

    def test_fetch_prices_keys_lowercase_and_skips_unknown(self):
        good_price = MagicMock()
        good_price.readable.return_value = 1234.56
        oracle = MagicMock()
        oracle.get_asset_price.side_effect = [
            good_price,
            ContractLogicError("no source"),
        ]

        with ThreadPoolExecutor() as pool:
            result = _fetch_breakdown_token_prices(
                pool, oracle, {_TOKEN_USDC, _TOKEN_WETH}
            )

        assert result is not None
        assert len(result) == 1
        for addr in result:  # pylint: disable=not-an-iterable
            assert addr == addr.lower()


class TestBreakdownAmountJson:
    @patch("ipor_fusion.cli.vault_cmd._resolve_token_decimals", return_value=6)
    @patch("ipor_fusion.cli.vault_cmd._resolve_token_symbol", return_value="USDC")
    def test_includes_symbol_decimals_formatted_and_usd(self, _sym, _dec):
        from ipor_fusion.cli.vault_cmd import (  # pylint: disable=import-outside-toplevel
            _build_breakdown_amount_json,
        )

        prices = {_TOKEN_USDC.lower(): 1.0}  # pylint: disable=no-member
        entry = _build_breakdown_amount_json(
            MagicMock(), 1_000_000, _TOKEN_USDC, prices
        )
        assert entry["raw"] == 1_000_000
        assert entry["token"] == _TOKEN_USDC
        assert entry["symbol"] == "USDC"
        assert entry["decimals"] == 6
        assert entry["formatted"] == "1.0"
        assert entry["usd"] == 1.0

    @patch("ipor_fusion.cli.vault_cmd._resolve_token_decimals", return_value=6)
    @patch("ipor_fusion.cli.vault_cmd._resolve_token_symbol", return_value="USDC")
    def test_omits_usd_when_no_price(self, _sym, _dec):
        from ipor_fusion.cli.vault_cmd import (  # pylint: disable=import-outside-toplevel
            _build_breakdown_amount_json,
        )

        entry = _build_breakdown_amount_json(MagicMock(), 1_000_000, _TOKEN_USDC, None)
        assert "usd" not in entry
        assert entry["formatted"] == "1.0"


class TestFormatTokenAmountUsd:
    @patch("ipor_fusion.cli.vault_cmd._resolve_token_decimals", return_value=6)
    @patch("ipor_fusion.cli.vault_cmd._resolve_token_symbol", return_value="USDC")
    def test_appends_usd_when_price_known(self, _sym, _dec):
        from ipor_fusion.cli.vault_cmd import (  # pylint: disable=import-outside-toplevel
            _format_token_amount,
        )

        prices = {_TOKEN_USDC.lower(): 1.0}  # pylint: disable=no-member
        out = _format_token_amount(MagicMock(), 1_000_000, _TOKEN_USDC, prices)
        assert "1.0 USDC" in out
        assert "$1.00" in out

    @patch("ipor_fusion.cli.vault_cmd._resolve_token_decimals", return_value=6)
    @patch("ipor_fusion.cli.vault_cmd._resolve_token_symbol", return_value="USDC")
    def test_no_usd_suffix_when_price_missing(self, _sym, _dec):
        from ipor_fusion.cli.vault_cmd import (  # pylint: disable=import-outside-toplevel
            _format_token_amount,
        )

        out = _format_token_amount(MagicMock(), 1_000_000, _TOKEN_USDC, {})
        assert "$" not in out


class TestPrintPendingRequests:
    def test_no_withdraw_manager_data(self, capsys):
        data = _make_data()
        pv = MagicMock()
        _print_pending_requests(data, pv)
        captured = capsys.readouterr()
        assert captured.out == ""

    def test_empty_pending_requests(self, capsys):
        data = _make_data(
            withdraw_manager=ADDR_1,
            withdraw_manager_data=_WithdrawManagerData(
                withdraw_window=86400,
                request_fee=0,
                withdraw_fee=0,
                shares_to_release=0,
                last_release_funds_timestamp=0,
                pending_requests=[],
            ),
        )
        pv = MagicMock()
        _print_pending_requests(data, pv)
        captured = capsys.readouterr()
        assert "Pending requests: (none)" in captured.out

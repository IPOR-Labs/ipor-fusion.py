# pylint: disable=unused-argument
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
    _print_pending_requests,
    _print_substrates,
    _resolve_chain_id,
    _resolve_provider,
)
from ipor_fusion.cli.vault_fetcher import (
    _VaultData,
    _WithdrawManagerData,
    _resolve_token_symbol,
    _safe_call,
)
from ipor_fusion.cli.vault_health import (
    _BalanceFuseTotals,
    _Erc20Totals,
    _TokenInfo,
    _print_erc20_balances,
    _print_health_check,
    _print_reconciliation,
)
from ipor_fusion.cli.vault_rendering import (
    _format_amount,
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

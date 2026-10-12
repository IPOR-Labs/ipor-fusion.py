"""`fusion vault validate`: verdict, findings, JSON shape and exit status."""

from __future__ import annotations

import json
from dataclasses import replace
from unittest.mock import MagicMock, patch

import pytest
from _validation import holding, validate
from click.testing import CliRunner
from web3 import Web3

from ipor_fusion import Finding, VaultValidation
from ipor_fusion.cli import config_store
from ipor_fusion.cli.config_store import FusionConfig, save_config
from ipor_fusion.cli.main import cli
from ipor_fusion.cli.vault_fetcher import _VaultData
from ipor_fusion.errors import NotPlasmaVaultError, UnsupportedVaultVersionError
from ipor_fusion.validation import Erc20Holdings

VAULT = Web3.to_checksum_address("0x" + "11" * 20)
ASSET = Web3.to_checksum_address("0x" + "a1" * 20)
TOKEN = Web3.to_checksum_address("0x" + "b1" * 20)


@pytest.fixture(autouse=True)
def tmp_config(tmp_path, monkeypatch):
    config_dir = tmp_path / ".fusion"
    monkeypatch.setattr(config_store, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_store, "CONFIG_FILE", config_dir / "config.json")
    save_config(FusionConfig(providers={"1": "https://rpc.example.com"}))


def _data() -> _VaultData:
    return _VaultData(
        block_number=21_000_000,
        is_latest=False,
        block_timestamp=1_700_000_000,
        share_decimals=6,
        asset_decimals=6,
        total_assets=100 * 10**6,
        total_supply=100 * 10**6,
        supply_cap=0,
        asset=ASSET,
        asset_symbol="USDC",
        access_manager=ASSET,
        price_oracle_addr=ASSET,
        rewards_manager=None,
        withdraw_manager=None,
        asset_price_usd=1.0,
        fuses=[],
        balance_fuses=[],
        instant_fuses=[],
    )


def _validation(*findings: Finding) -> VaultValidation:
    erc20 = Erc20Holdings(tokens=(holding(TOKEN, "TKN", 6, 10**6, 1.0),))
    validation = validate(_data(), 100 * 10**6, erc20)
    return replace(validation, vault=VAULT, chain_id=1, findings=findings)


CRITICAL = Finding("orphan_fuse", "critical", "CRITICAL — orphan")
WARNING = Finding("uncovered_erc20", "warning", "heading:\n    TKN 1.0")
PASSED = Finding("total_assets_reconciliation", "ok", "balanced")


def _invoke(validation: VaultValidation, *args: str):
    with (
        patch("ipor_fusion.cli.vault_cmd.Web3Context") as ctx_cls,
        patch("ipor_fusion.cli.vault_cmd.resolve_access_manager"),
        patch(
            "ipor_fusion.cli.vault_cmd.validate_vault", return_value=validation
        ) as validate_vault,
    ):
        ctx_cls.from_url.return_value = MagicMock()
        result = CliRunner().invoke(
            cli, ["vault", "validate", VAULT, "--chain-id", "ethereum", *args]
        )
    return result, validate_vault


def test_critical_verdict_exits_one():
    result, _ = _invoke(_validation(WARNING, CRITICAL, PASSED))

    assert result.exit_code == 1, result.output
    lines = result.output.splitlines()
    assert "Verdict: CRITICAL" in lines
    assert lines[lines.index("Criticals (1):") + 1] == "  CRITICAL — orphan"
    warnings_at = lines.index("Warnings (1):")
    assert lines[warnings_at + 1 : warnings_at + 3] == ["  heading:", "      TKN 1.0"]
    assert lines[lines.index("Passed (1):") + 1] == "  balanced"


def test_warning_verdict_exits_zero():
    result, validate_vault = _invoke(_validation(WARNING), "--block-number", "123")

    assert result.exit_code == 0, result.output
    assert "Verdict: WARNING" in result.output
    assert "Criticals" not in result.output
    args, kwargs = validate_vault.call_args
    assert args[1:] == (VAULT, 123)
    assert callable(kwargs["resolve_symbol"])


def test_ok_verdict_prints_the_block():
    result, _ = _invoke(_validation(PASSED))

    assert result.exit_code == 0, result.output
    assert result.output.splitlines()[:4] == [
        f"Vault:   {VAULT}",
        "Chain:   ethereum (chain-id=1)",
        "Block:   21000000 (2023-11-14T22:13:20Z)",
        "Verdict: OK",
    ]


def test_json_output():
    result, _ = _invoke(_validation(CRITICAL, PASSED), "--json")

    assert result.exit_code == 1, result.output
    payload = json.loads(result.output)
    assert payload["verdict"] == "critical"
    assert payload["criticals"] == ["CRITICAL — orphan"]
    assert payload["ok"] == ["balanced"]
    assert payload["findings"][0] == {
        "check": "orphan_fuse",
        "severity": "critical",
        "message": "CRITICAL — orphan",
    }
    assert payload["asset"]["symbol"] == "USDC"
    assert payload["block"] == 21_000_000
    assert payload["reconciliation"]["sum"]["raw"] == 100 * 10**6
    assert payload["erc20_balances"][0]["symbol"] == "TKN"
    assert payload["lending_health"] is None


def test_not_a_plasma_vault_is_a_usage_error():
    with (
        patch("ipor_fusion.cli.vault_cmd.Web3Context"),
        patch(
            "ipor_fusion.cli.vault_cmd.resolve_access_manager",
            side_effect=NotPlasmaVaultError("not a Plasma Vault"),
        ),
    ):
        result = CliRunner().invoke(
            cli, ["vault", "validate", VAULT, "--chain-id", "1"]
        )

    assert result.exit_code == 2
    assert "not a Plasma Vault" in result.output


def test_unsupported_vault_version_is_reported():
    with (
        patch("ipor_fusion.cli.vault_cmd.Web3Context"),
        patch("ipor_fusion.cli.vault_cmd.resolve_access_manager"),
        patch(
            "ipor_fusion.cli.vault_cmd.validate_vault",
            side_effect=UnsupportedVaultVersionError("too old"),
        ),
    ):
        result = CliRunner().invoke(
            cli, ["vault", "validate", VAULT, "--chain-id", "1"]
        )

    assert result.exit_code == 1
    assert "too old" in result.output

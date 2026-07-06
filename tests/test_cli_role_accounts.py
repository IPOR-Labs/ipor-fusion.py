"""Tests for the `vault role-accounts` CLI command."""

import json
from unittest.mock import MagicMock, patch

import pytest
import requests
from click.testing import CliRunner
from web3.exceptions import Web3RPCError

from ipor_fusion.cli import config_store
from ipor_fusion.cli.config_store import FusionConfig, save_config
from ipor_fusion.cli.main import cli
from ipor_fusion.cli.vault_cmd import _fetch_role_accounts_json
from ipor_fusion.core.access import RoleAccount
from ipor_fusion.errors import NotAPlasmaVaultError
from ipor_fusion.types import Period, RoleId

VAULT = "0x2222222222222222222222222222222222222222"
MANAGER = "0x5555555555555555555555555555555555555555"
ALICE = "0xAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA"
BOB = "0xbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbbb"


@pytest.fixture
def tmp_config(tmp_path, monkeypatch):
    config_dir = tmp_path / ".fusion"
    monkeypatch.setattr(config_store, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_store, "CONFIG_FILE", config_dir / "config.json")
    save_config(FusionConfig(providers={"1": "https://rpc.example.com"}))


def _role_account(role_id: int, account: str, delay: int = 0) -> RoleAccount:
    return RoleAccount(
        account=account,  # type: ignore[arg-type]
        role_id=RoleId(role_id),
        is_member=True,
        execution_delay=Period(delay),
    )


def _mock_manager(accounts: list[RoleAccount]) -> MagicMock:
    manager = MagicMock()
    manager.address = MANAGER
    manager.get_all_role_accounts.return_value = accounts
    manager.get_accounts_with_role.return_value = accounts
    return manager


@patch("ipor_fusion.cli.vault_cmd.resolve_access_manager")
@patch("ipor_fusion.cli.vault_cmd.Web3Context")
class TestRoleAccounts:
    def test_table_output(self, _ctx, mock_resolve, tmp_config):
        mock_resolve.return_value = _mock_manager(
            [_role_account(100, BOB), _role_account(1, ALICE, delay=60)]
        )

        result = CliRunner().invoke(
            cli, ["vault", "role-accounts", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code == 0
        assert MANAGER in result.output
        assert "OWNER_ROLE" in result.output
        assert "ATOMIST_ROLE" in result.output

    def test_json_output_sorted(self, _ctx, mock_resolve, tmp_config):
        mock_resolve.return_value = _mock_manager(
            [_role_account(100, BOB), _role_account(1, ALICE, delay=60)]
        )

        result = CliRunner().invoke(
            cli, ["vault", "role-accounts", VAULT, "--chain-id", "1", "--json"]
        )

        assert result.exit_code == 0
        payload = json.loads(result.output)
        assert payload["access_manager"] == MANAGER
        assert payload["role_filter"] is None
        # Sorted by (account.lower(), role_id).
        assert [(a["account"], a["role_id"]) for a in payload["accounts"]] == [
            (ALICE, 1),
            (BOB, 100),
        ]
        assert payload["accounts"][0]["role_name"] == "OWNER_ROLE"
        assert payload["accounts"][0]["execution_delay"] == 60

    def test_role_filter(self, _ctx, mock_resolve, tmp_config):
        manager = _mock_manager([_role_account(100, BOB)])
        mock_resolve.return_value = manager

        result = CliRunner().invoke(
            cli,
            [
                "vault",
                "role-accounts",
                VAULT,
                "--chain-id",
                "1",
                "--role",
                "atomist",
                "--json",
            ],
        )

        assert result.exit_code == 0
        manager.get_accounts_with_role.assert_called_once_with(100)
        manager.get_all_role_accounts.assert_not_called()
        assert json.loads(result.output)["role_filter"] == "ATOMIST_ROLE"

    def test_unknown_role_is_usage_error_before_rpc(
        self, mock_ctx_cls, mock_resolve, tmp_config
    ):
        result = CliRunner().invoke(
            cli,
            ["vault", "role-accounts", VAULT, "--chain-id", "1", "--role", "bishop"],
        )

        assert result.exit_code != 0
        assert "Valid: ADMIN_ROLE" in result.output
        # Pure input validation must not need a provider.
        mock_ctx_cls.from_url.assert_not_called()
        mock_resolve.assert_not_called()

    def test_unknown_vault_needs_chain_id(self, _ctx, mock_resolve, tmp_config):
        result = CliRunner().invoke(cli, ["vault", "role-accounts", VAULT])

        assert result.exit_code != 0
        assert "--chain-id" in result.output

    def test_not_a_vault_is_usage_error(self, _ctx, mock_resolve, tmp_config):
        mock_resolve.side_effect = NotAPlasmaVaultError("not a Plasma Vault")

        result = CliRunner().invoke(
            cli, ["vault", "role-accounts", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code != 0
        assert "not a Plasma Vault" in result.output

    def test_scan_rejection_is_friendly_error(self, _ctx, mock_resolve, tmp_config):
        manager = _mock_manager([])
        manager.get_all_role_accounts.side_effect = Web3RPCError(
            "query returned more than 10000 results"
        )
        mock_resolve.return_value = manager

        result = CliRunner().invoke(
            cli, ["vault", "role-accounts", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code != 0
        assert "eth_getLogs" in result.output
        assert "Traceback" not in result.output

    def test_scan_transport_failure_is_friendly_error(
        self, _ctx, mock_resolve, tmp_config
    ):
        manager = _mock_manager([])
        manager.get_all_role_accounts.side_effect = requests.exceptions.ReadTimeout(
            "provider timed out"
        )
        mock_resolve.return_value = manager

        result = CliRunner().invoke(
            cli, ["vault", "role-accounts", VAULT, "--chain-id", "1"]
        )

        assert result.exit_code != 0
        assert "eth_getLogs" in result.output
        assert "Traceback" not in result.output


class TestFetchRoleAccountsJson:
    @staticmethod
    def _data() -> MagicMock:
        data = MagicMock()
        data.access_manager = MANAGER
        return data

    def test_transport_failure_degrades_to_none(self):
        ctx = MagicMock()
        ctx.get_logs.side_effect = requests.exceptions.ReadTimeout("timed out")

        assert _fetch_role_accounts_json(ctx, self._data()) is None

    def test_rpc_rejection_degrades_to_none(self):
        ctx = MagicMock()
        ctx.get_logs.side_effect = Web3RPCError("log range too large")

        assert _fetch_role_accounts_json(ctx, self._data()) is None

    def test_unexpected_errors_propagate(self):
        ctx = MagicMock()
        ctx.get_logs.side_effect = RuntimeError("bug, not a provider issue")

        with pytest.raises(RuntimeError, match="bug"):
            _fetch_role_accounts_json(ctx, self._data())

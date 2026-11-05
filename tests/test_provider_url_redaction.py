"""No error text carries a provider URL's path, query or userinfo: they hold API
keys. Every path runs the real HTTP stack against a local socket, so the text
under test is what requests and web3 actually produce."""

from __future__ import annotations

import asyncio
import json
import logging
import socket
import sys
import threading
from collections.abc import Iterator
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from unittest.mock import MagicMock

import pytest
import requests
from mcp.server.fastmcp.exceptions import ToolError

from ipor_fusion.cli import config_store
from ipor_fusion.cli.config_store import FusionConfig, save_config
from ipor_fusion.cli.main import main
from ipor_fusion.errors import get_revert_reason
from ipor_fusion.mcp import server
from ipor_fusion.readers.lending_health import _reason
from ipor_fusion.redaction import mask_provider_url, redact_provider_urls
from ipor_fusion.vault_reads import _scan_or_none

VAULT = "0x4c4f752fa54dafaa7770b2c6d6f0d6d7e5cf8b2f"
ACCESS_MANAGER_WORD = "0x" + "00" * 12 + "11" * 20
CHAIN_ID = 8453
SECRETS = ("SECRETKEY123", "SECRETQ456")
PROVIDER_PATH = f"/v2/{SECRETS[0]}?apikey={SECRETS[1]}"
USAGE_ERROR_EXIT = 2


class _Rpc(BaseHTTPRequestHandler):
    """Refuses every method in ``forbidden`` with a 403, answers the rest."""

    forbidden: frozenset[str] = frozenset()

    def do_POST(self) -> None:
        body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
        if body["method"] in self.forbidden:
            self.send_error(403)
            return
        answers = {
            "eth_chainId": hex(CHAIN_ID),
            "eth_blockNumber": "0x4",
            "eth_call": ACCESS_MANAGER_WORD,
            "eth_getCode": "0x60",
        }
        payload = json.dumps(
            {"jsonrpc": "2.0", "id": body["id"], "result": answers[body["method"]]}
        ).encode()
        self.send_response(200)
        self.send_header("Content-Length", str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    # The parameter name is the base class's.
    def log_message(self, format: str, *args: object) -> None:  # noqa: A002
        del format, args


def _serve(refused: frozenset[str]) -> Iterator[str]:
    class Handler(_Rpc):
        forbidden = refused

    httpd = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}{PROVIDER_PATH}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join()


@pytest.fixture
def refusing_provider() -> Iterator[str]:
    yield from _serve(frozenset({"eth_chainId", "eth_call", "eth_getLogs"}))


@pytest.fixture
def closed_provider() -> str:
    with socket.socket() as probe:
        probe.bind(("127.0.0.1", 0))
        port = probe.getsockname()[1]
    return f"http://127.0.0.1:{port}{PROVIDER_PATH}"


@pytest.fixture
def scan_refusing_provider() -> Iterator[str]:
    yield from _serve(frozenset({"eth_getLogs"}))


@pytest.fixture
def config_paths(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setattr(config_store, "CONFIG_DIR", tmp_path)
    monkeypatch.setattr(config_store, "CONFIG_FILE", tmp_path / "config.json")
    return tmp_path / "config.json"


def _use_provider(url: str) -> None:
    save_config(FusionConfig(providers={str(CHAIN_ID): url}))


def _run_cli(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    *args: str,
) -> tuple[int | str | None, str]:
    monkeypatch.setattr(sys, "argv", ["fusion", *args])
    with pytest.raises(SystemExit) as exit_info:
        main()
    captured = capsys.readouterr()
    return exit_info.value.code, captured.out + captured.err


def _assert_no_secret(text: str) -> None:
    assert "/v2/" not in text
    assert "apikey" not in text
    for secret in SECRETS:
        assert secret not in text


@pytest.mark.parametrize("provider", ["refusing_provider", "closed_provider"])
class TestProviderFailure:
    def test_cli(
        self,
        provider: str,
        request: pytest.FixtureRequest,
        config_paths: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        del config_paths
        _use_provider(request.getfixturevalue(provider))

        code, output = _run_cli(
            monkeypatch, capsys, "vault", "info", VAULT, "--chain-id", str(CHAIN_ID)
        )

        assert code == 1
        assert "127.0.0.1" in output
        _assert_no_secret(output)

    def test_mcp_tool(
        self,
        provider: str,
        request: pytest.FixtureRequest,
        config_paths: Path,
    ):
        del config_paths
        _use_provider(request.getfixturevalue(provider))

        with pytest.raises(ToolError) as info:
            asyncio.run(
                server.mcp.call_tool(
                    "vault_info",
                    {"vault_address": VAULT, "chain_id": CHAIN_ID},
                )
            )

        assert "127.0.0.1" in str(info.value)
        _assert_no_secret(str(info.value))


class TestLogScanFailure:
    def test_cli_role_accounts(
        self,
        scan_refusing_provider: str,
        config_paths: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        del config_paths
        _use_provider(scan_refusing_provider)

        code, output = _run_cli(
            monkeypatch,
            capsys,
            "vault",
            "role-accounts",
            VAULT,
            "--chain-id",
            str(CHAIN_ID),
        )

        assert code == 1
        assert "LogScanError" in output
        _assert_no_secret(output)

    def test_mcp_role_accounts(
        self,
        scan_refusing_provider: str,
        config_paths: Path,
    ):
        del config_paths
        _use_provider(scan_refusing_provider)

        with pytest.raises(ToolError) as info:
            asyncio.run(
                server.mcp.call_tool(
                    "vault_role_accounts",
                    {"vault_address": VAULT, "chain_id": CHAIN_ID},
                )
            )

        assert "eth_getLogs scan" in str(info.value)
        _assert_no_secret(str(info.value))


class TestCliErrorChannels:
    def test_usage_error_with_a_url(
        self,
        config_paths: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        del config_paths
        url = f"http://127.0.0.1:1{PROVIDER_PATH}"

        code, output = _run_cli(monkeypatch, capsys, "vault", "info", url)

        assert code == USAGE_ERROR_EXIT
        _assert_no_secret(output)

    def test_invalid_config_echoes_no_value(
        self,
        config_paths: Path,
        monkeypatch: pytest.MonkeyPatch,
        capsys: pytest.CaptureFixture[str],
    ):
        config_paths.write_text(
            json.dumps(
                {
                    "providers": {"1": ["SECRETKEY123"]},
                    "etherscan_api_key": ["SECRETQ456"],
                }
            ),
            encoding="utf-8",
        )

        code, output = _run_cli(monkeypatch, capsys, "config", "show")

        assert code == 1
        assert "providers.1" in output
        assert "etherscan_api_key" in output
        for secret in SECRETS:
            assert secret not in output


class TestDebugLogs:
    FAILURE = requests.ConnectionError(
        f"Max retries exceeded with url: {PROVIDER_PATH}"
    )

    def _assert_redacted(self, caplog: pytest.LogCaptureFixture) -> None:
        assert "<redacted>" in caplog.text
        _assert_no_secret(caplog.text)

    def test_unavailable_scan(self, caplog: pytest.LogCaptureFixture):
        def scan() -> None:
            raise self.FAILURE

        with caplog.at_level(logging.DEBUG):
            assert _scan_or_none(scan) is None

        self._assert_redacted(caplog)

    def test_failed_lending_read(self, caplog: pytest.LogCaptureFixture):
        with caplog.at_level(logging.DEBUG):
            _reason("market data", self.FAILURE)

        self._assert_redacted(caplog)

    def test_undecodable_revert(self, caplog: pytest.LogCaptureFixture):
        web3 = MagicMock()
        web3.eth.get_transaction.side_effect = self.FAILURE

        with caplog.at_level(logging.DEBUG):
            reason = get_revert_reason(web3, MagicMock(), MagicMock())

        assert reason is None
        self._assert_redacted(caplog)


class TestRedactProviderUrls:
    @pytest.mark.parametrize(
        ("text", "expected"),
        [
            (
                "403 Client Error: Forbidden for url: https://rpc.example/v2/KEY?a=KEY2",
                "403 Client Error: Forbidden for url: https://rpc.example",
            ),
            (
                "Pool(host='rpc.example', port=443): Max retries exceeded with url: "
                "/v2/KEY?a=KEY2 (Caused by Boom())",
                "Pool(host='rpc.example', port=443): Max retries exceeded with url: "
                "<redacted> (Caused by Boom())",
            ),
            ("dial wss://user:KEY@rpc.example:8546/ws", "dial wss://rpc.example:8546"),
            ("to 'http://[::1]:8545/KEY'", "to 'http://[::1]:8545'"),
            ("see (https://rpc.example/KEY).", "see (https://rpc.example)."),
            ("two http://a.example/K1 and http://b.example/K2", None),
            ("address: 0xhttp://rpc.example/KEY", "address: ***"),
            (
                "for url: https://rpc.example/v2/a'KEY?k=KEY2",
                "for url: https://rpc.example",
            ),
            (
                "with url: /v2/a'KEY?k=KEY2 (Caused by Boom())",
                "with url: <redacted> (Caused by Boom())",
            ),
            ("at https://rpc.example'KEY/path", "at ***"),
            ("no url here: just a message", "no url here: just a message"),
        ],
    )
    def test_text(self, text: str, expected: str | None):
        if expected is None:
            expected = "two http://a.example and http://b.example"
        assert redact_provider_urls(text) == expected

    @pytest.mark.parametrize(
        "url",
        [
            "https://user:pass/word@rpc.example/path",
            "http://[not-ipv6/KEY",
            "https://bad host/KEY",
        ],
    )
    def test_unparsable_authority_is_masked_whole(self, url: str):
        assert mask_provider_url(url) == "***"

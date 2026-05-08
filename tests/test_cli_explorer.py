import json
from unittest.mock import MagicMock, patch

import pytest

from ipor_fusion.cli import config_store
from ipor_fusion.cli.explorer import (
    _fetch_contract_creation_tx,
    _fetch_contract_name,
    _fetch_getsourcecode,
    get_contract_name,
    get_deployment_tx,
)


@pytest.fixture(autouse=True)
def _patch_paths(tmp_path, monkeypatch):
    config_dir = tmp_path / ".fusion"
    cache_dir = tmp_path / ".cache"
    monkeypatch.setattr(config_store, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_store, "CONFIG_FILE", config_dir / "config.json")
    monkeypatch.setattr(config_store, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(config_store, "CACHE_FILE", cache_dir / "contract_cache.json")


def _mock_urlopen(response_data: bytes):
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_data
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestGetContractName:
    def test_returns_cached_value(self, tmp_path):
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()
        cache = {"1:0xABC": "MyToken"}
        (cache_dir / "contract_cache.json").write_text(
            json.dumps(cache), encoding="utf-8"
        )

        with patch("ipor_fusion.cli.explorer.urlopen") as mock_url:
            result = get_contract_name(1, "0xABC", api_key="key123")

        assert result == "MyToken"
        mock_url.assert_not_called()

    @patch("ipor_fusion.cli.explorer.urlopen")
    def test_fetches_and_caches(self, mock_urlopen_fn, tmp_path):
        response = {
            "status": "1",
            "result": [{"ContractName": "PlasmaVault"}],
        }
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())

        result = get_contract_name(1, "0xDEF", api_key="key123")

        assert result == "PlasmaVault"
        mock_urlopen_fn.assert_called_once()

        cache_file = tmp_path / ".cache" / "contract_cache.json"
        assert cache_file.exists()
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        assert cached["1:0xDEF"] == "PlasmaVault"

    def test_returns_empty_for_unsupported_chain(self):
        result = get_contract_name(99999, "0xABC", api_key="key123")
        assert result == ""

    def test_returns_empty_when_no_api_key(self):
        result = get_contract_name(1, "0xABC", api_key=None)
        assert result == ""


class TestFetchContractName:
    def test_unsupported_chain_returns_none(self):
        result = _fetch_contract_name(99999, "0xABC", api_key="key123")
        assert result is None

    def test_no_api_key_returns_none(self):
        result = _fetch_contract_name(1, "0xABC", api_key=None)
        assert result is None

    @patch("ipor_fusion.cli.explorer.urlopen")
    def test_network_error_returns_none(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = OSError("connection refused")

        result = _fetch_contract_name(1, "0xABC", api_key="key123")

        assert result is None

    @patch("ipor_fusion.cli.explorer.urlopen")
    def test_invalid_json_returns_none(self, mock_urlopen_fn):
        mock_resp = _mock_urlopen(b"not valid json {{{")
        mock_urlopen_fn.return_value = mock_resp

        result = _fetch_contract_name(1, "0xABC", api_key="key123")

        assert result is None

    @patch("ipor_fusion.cli.explorer.urlopen")
    def test_status_not_one_returns_none(self, mock_urlopen_fn):
        response = {"status": "0", "result": []}
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())

        result = _fetch_contract_name(1, "0xABC", api_key="key123")

        assert result is None

    @patch("ipor_fusion.cli.explorer.urlopen")
    def test_empty_contract_name_returns_none(self, mock_urlopen_fn):
        response = {"status": "1", "result": [{"ContractName": ""}]}
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())

        result = _fetch_contract_name(1, "0xABC", api_key="key123")

        assert result is None


class TestFetchGetSourceCode:
    def test_unsupported_chain_returns_none(self):
        assert _fetch_getsourcecode(99999, "0xABC", api_key="key123") is None

    def test_no_api_key_returns_none(self):
        assert _fetch_getsourcecode(1, "0xABC", api_key=None) is None

    @patch("ipor_fusion.cli.explorer.urlopen")
    def test_returns_full_result_dict(self, mock_urlopen_fn):
        response = {
            "status": "1",
            "result": [{"ContractName": "PlasmaVault", "CompilerVersion": "v0.8.20"}],
        }
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())

        result = _fetch_getsourcecode(1, "0xABC", api_key="key123")

        assert result is not None
        assert result["ContractName"] == "PlasmaVault"
        assert result["CompilerVersion"] == "v0.8.20"

    @patch("ipor_fusion.cli.explorer.urlopen")
    def test_network_error_returns_none(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = OSError("connection refused")
        assert _fetch_getsourcecode(1, "0xABC", api_key="key123") is None


class TestFetchContractCreationTx:
    def test_unsupported_chain(self):
        tx, err = _fetch_contract_creation_tx(99999, "0xABC", api_key="key123")
        assert tx is None
        assert err == "chain-not-supported"

    def test_no_api_key(self):
        tx, err = _fetch_contract_creation_tx(1, "0xABC", api_key=None)
        assert tx is None
        assert err == "no-api-key"

    @patch("ipor_fusion.cli.explorer.urlopen")
    def test_paid_tier_required(self, mock_urlopen_fn):
        response = {
            "status": "0",
            "message": "NOTOK",
            "result": (
                "Free API access is not supported for this chain. "
                "Please upgrade your api plan for full chain coverage."
            ),
        }
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())
        tx, err = _fetch_contract_creation_tx(8453, "0xABC", api_key="key123")
        assert tx is None
        assert err == "etherscan-paid-tier-required"

    @patch("ipor_fusion.cli.explorer.urlopen")
    def test_no_data_found_is_not_an_error(self, mock_urlopen_fn):
        response = {"status": "0", "message": "No data found", "result": []}
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())
        tx, err = _fetch_contract_creation_tx(1, "0xABC", api_key="key123")
        assert tx is None
        assert err is None

    @patch("ipor_fusion.cli.explorer.urlopen")
    def test_success_returns_tx_hash(self, mock_urlopen_fn):
        response = {
            "status": "1",
            "result": [{"txHash": "0xdeadbeef"}],
        }
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())
        tx, err = _fetch_contract_creation_tx(1, "0xABC", api_key="key123")
        assert tx == "0xdeadbeef"
        assert err is None

    @patch("ipor_fusion.cli.explorer.urlopen")
    def test_network_error(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = OSError("connection refused")
        tx, err = _fetch_contract_creation_tx(1, "0xABC", api_key="key123")
        assert tx is None
        assert err == "fetch-failed"

    def test_get_deployment_tx_delegates(self):
        # Smoke test the public alias.
        tx, err = get_deployment_tx(99999, "0xABC", api_key="key123")
        assert tx is None
        assert err == "chain-not-supported"

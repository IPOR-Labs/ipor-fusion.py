import json
from unittest.mock import MagicMock, patch

import pytest

from ipor_fusion.cli import config_store
from ipor_fusion.cli.explorer import (
    _fetch_contract_name,
    get_contract_name,
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

import json
from unittest.mock import MagicMock, patch

import pytest

from ipor_fusion.cache import NullCache
from ipor_fusion.explorer import (
    _fetch_contract_creation_tx,
    _fetch_contract_name,
    _fetch_getsourcecode,
    get_contract_name,
    get_deployment_tx,
)

ABC = "0x" + "aB" * 20
DEF = "0x" + "dE" * 20


@pytest.fixture(autouse=True)
def _cache_home(tmp_path, monkeypatch):
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))


def _mock_urlopen(response_data: bytes):
    mock_resp = MagicMock()
    mock_resp.read.return_value = response_data
    mock_resp.__enter__ = MagicMock(return_value=mock_resp)
    mock_resp.__exit__ = MagicMock(return_value=False)
    return mock_resp


class TestGetContractName:
    def test_returns_cached_value(self, tmp_path):
        path = tmp_path / ".cache" / "ipor-fusion" / "contract_cache.json"
        path.parent.mkdir(parents=True)
        # A 3.x name entry (chain:address in the caller's spelling) still hits.
        path.write_text(json.dumps({f"1:{ABC}": "MyToken"}), encoding="utf-8")

        with patch("ipor_fusion.explorer.urlopen") as mock_url:
            result = get_contract_name(1, ABC, api_key="key123")

        assert result == "MyToken"
        mock_url.assert_not_called()

    @patch("ipor_fusion.explorer.urlopen")
    def test_fetches_and_caches(self, mock_urlopen_fn, tmp_path):
        response = {
            "status": "1",
            "result": [{"ContractName": "PlasmaVault"}],
        }
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())

        result = get_contract_name(1, DEF, api_key="key123")

        assert result == "PlasmaVault"
        mock_urlopen_fn.assert_called_once()

        cache_file = tmp_path / ".cache" / "ipor-fusion" / "contract_cache.json"
        cached = json.loads(cache_file.read_text(encoding="utf-8"))
        assert cached == {f"1:{DEF.lower()}:name": "PlasmaVault"}

    @patch("ipor_fusion.explorer.urlopen")
    def test_an_injected_cache_replaces_the_file(self, mock_urlopen_fn, tmp_path):
        response = {"status": "1", "result": [{"ContractName": "PlasmaVault"}]}
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())

        assert (
            get_contract_name(1, DEF, api_key="key123", cache=NullCache())
            == "PlasmaVault"
        )
        assert (
            get_contract_name(1, DEF, api_key="key123", cache=NullCache())
            == "PlasmaVault"
        )

        assert mock_urlopen_fn.call_count == 2
        assert not (tmp_path / ".cache").exists()

    @patch("ipor_fusion.explorer.urlopen")
    def test_a_failing_cache_does_not_fail_the_lookup(self, mock_urlopen_fn):
        response = {"status": "1", "result": [{"ContractName": "PlasmaVault"}]}
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())
        broken = MagicMock()
        broken.get.side_effect = OSError("read-only file system")
        broken.put.side_effect = OSError("read-only file system")

        assert (
            get_contract_name(1, DEF, api_key="key123", cache=broken) == "PlasmaVault"
        )
        broken.put.assert_called_once()

    def test_returns_empty_for_unsupported_chain(self):
        result = get_contract_name(99999, ABC, api_key="key123")
        assert result == ""

    def test_returns_empty_when_no_api_key(self):
        result = get_contract_name(1, ABC, api_key=None)
        assert result == ""


class TestFetchContractName:
    def test_unsupported_chain_returns_none(self):
        result = _fetch_contract_name(99999, "0xABC", api_key="key123")
        assert result is None

    def test_no_api_key_returns_none(self):
        result = _fetch_contract_name(1, "0xABC", api_key=None)
        assert result is None

    @patch("ipor_fusion.explorer.urlopen")
    def test_network_error_returns_none(self, mock_urlopen_fn):
        mock_urlopen_fn.side_effect = OSError("connection refused")

        result = _fetch_contract_name(1, "0xABC", api_key="key123")

        assert result is None

    @patch("ipor_fusion.explorer.urlopen")
    def test_invalid_json_returns_none(self, mock_urlopen_fn):
        mock_resp = _mock_urlopen(b"not valid json {{{")
        mock_urlopen_fn.return_value = mock_resp

        result = _fetch_contract_name(1, "0xABC", api_key="key123")

        assert result is None

    @patch("ipor_fusion.explorer.urlopen")
    def test_status_not_one_returns_none(self, mock_urlopen_fn):
        response = {"status": "0", "result": []}
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())

        result = _fetch_contract_name(1, "0xABC", api_key="key123")

        assert result is None

    @patch("ipor_fusion.explorer.urlopen")
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

    @patch("ipor_fusion.explorer.urlopen")
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

    @patch("ipor_fusion.explorer.urlopen")
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

    @patch("ipor_fusion.explorer.urlopen")
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

    @patch("ipor_fusion.explorer.urlopen")
    def test_no_data_found_is_not_an_error(self, mock_urlopen_fn):
        response = {"status": "0", "message": "No data found", "result": []}
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())
        tx, err = _fetch_contract_creation_tx(1, "0xABC", api_key="key123")
        assert tx is None
        assert err is None

    @patch("ipor_fusion.explorer.urlopen")
    def test_success_returns_tx_hash(self, mock_urlopen_fn):
        response = {
            "status": "1",
            "result": [{"txHash": "0x" + "de" * 32}],
        }
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())
        tx, err = _fetch_contract_creation_tx(1, "0xABC", api_key="key123")
        assert tx == "0x" + "de" * 32
        assert err is None

    @patch("ipor_fusion.explorer.urlopen")
    @pytest.mark.parametrize("raw", ["0xdeadbeef", 123, ["bad"], {"bad": True}])
    def test_a_malformed_tx_hash_is_reported_not_raised(self, mock_urlopen_fn, raw):
        response = {"status": "1", "result": [{"txHash": raw}]}
        mock_urlopen_fn.return_value = _mock_urlopen(json.dumps(response).encode())
        tx, err = _fetch_contract_creation_tx(1, "0xABC", api_key="key123")
        assert tx is None
        assert err == "malformed-tx-hash"

    @patch("ipor_fusion.explorer.urlopen")
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

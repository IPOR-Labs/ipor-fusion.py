import json

import pytest

from ipor_fusion import cache
from ipor_fusion.cache import load_contract_cache, save_contract_cache


@pytest.fixture(autouse=True)
def _patch_paths(tmp_path, monkeypatch):
    cache_dir = tmp_path / ".cache"
    monkeypatch.setattr(cache, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(cache, "CACHE_FILE", cache_dir / "contract_cache.json")


class TestLoadContractCache:
    def test_returns_empty_dict_when_no_file(self):
        assert load_contract_cache() == {}

    def test_loads_existing_cache(self, tmp_path):
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir()
        cache = {"0xABC": "ERC20", "0xDEF": "PlasmaVault"}
        (cache_dir / "contract_cache.json").write_text(
            json.dumps(cache), encoding="utf-8"
        )

        assert load_contract_cache() == cache


class TestSaveContractCache:
    def test_creates_directory_and_writes_json(self, tmp_path):
        cache = {"0xABC": "ERC20"}
        save_contract_cache(cache)

        cache_file = tmp_path / ".cache" / "contract_cache.json"
        assert cache_file.exists()
        assert json.loads(cache_file.read_text(encoding="utf-8")) == cache

    def test_roundtrip(self):
        original = {"0x111": "TokenA", "0x222": "TokenB"}
        save_contract_cache(original)
        loaded = load_contract_cache()
        assert loaded == original

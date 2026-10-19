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


class TestCanonicalKeys:
    CHECKSUM = "0xAbCdEf0123456789aBcDeF0123456789AbCdEf01"

    def _write(self, tmp_path, name, entries):
        cache_dir = tmp_path / ".cache"
        cache_dir.mkdir(exist_ok=True)
        (cache_dir / name).write_text(json.dumps(entries), encoding="utf-8")

    def test_a_key_lowercases_only_the_address(self):
        assert cache.cache_key("symbol", self.CHECKSUM) == (
            "symbol:" + self.CHECKSUM.lower()
        )
        assert cache.cache_key(1, self.CHECKSUM) == "1:" + self.CHECKSUM.lower()

    def test_a_legacy_key_spelled_by_the_caller_still_hits(self, tmp_path):
        self._write(tmp_path, "contract_cache.json", {f"1:{self.CHECKSUM}": "Vault"})
        assert load_contract_cache()[cache.cache_key(1, self.CHECKSUM)] == "Vault"

    def test_an_already_canonical_entry_wins_over_a_duplicate(self, tmp_path):
        self._write(
            tmp_path,
            "contract_cache.json",
            {
                f"symbol:{self.CHECKSUM.lower()}": "NEW",
                f"symbol:{self.CHECKSUM}": "OLD",
            },
        )
        assert load_contract_cache() == {f"symbol:{self.CHECKSUM.lower()}": "NEW"}

    def test_an_update_rewrites_legacy_keys(self, tmp_path):
        self._write(tmp_path, "contract_cache.json", {f"1:{self.CHECKSUM}": "Vault"})
        cache.update_contract_cache(cache.cache_key("decimals", self.CHECKSUM), "6")
        on_disk = json.loads(
            (tmp_path / ".cache" / "contract_cache.json").read_text(encoding="utf-8")
        )
        assert on_disk == {
            f"1:{self.CHECKSUM.lower()}": "Vault",
            f"decimals:{self.CHECKSUM.lower()}": "6",
        }

    def test_deployment_entries_are_canonical(self, tmp_path, monkeypatch):
        monkeypatch.setattr(
            cache,
            "DEPLOYMENT_CACHE_FILE",
            tmp_path / ".cache" / "deployment_cache.json",
        )
        self._write(
            tmp_path,
            "deployment_cache.json",
            {f"1:{self.CHECKSUM}": {"block": 5, "timestamp": 6}},
        )
        loaded = cache.load_deployment_cache()
        assert loaded[cache.cache_key(1, self.CHECKSUM)] == {"block": 5, "timestamp": 6}

import json

import pytest

from ipor_fusion.cli import config_store
from ipor_fusion.cli.config_store import (
    FusionConfig,
    VaultEntry,
    load_config,
    load_contract_cache,
    save_config,
    save_contract_cache,
)


@pytest.fixture(autouse=True)
def _patch_paths(tmp_path, monkeypatch):
    config_dir = tmp_path / ".fusion"
    cache_dir = tmp_path / ".cache"
    monkeypatch.setattr(config_store, "CONFIG_DIR", config_dir)
    monkeypatch.setattr(config_store, "CONFIG_FILE", config_dir / "config.json")
    monkeypatch.setattr(config_store, "CACHE_DIR", cache_dir)
    monkeypatch.setattr(config_store, "CACHE_FILE", cache_dir / "contract_cache.json")


class TestDataclassDefaults:
    def test_vault_entry_stores_fields(self):
        entry = VaultEntry(address="0xABC", label="main", chain_id=1)
        assert entry.address == "0xABC"
        assert entry.label == "main"
        assert entry.chain_id == 1

    def test_fusion_config_defaults(self):
        cfg = FusionConfig()
        assert not cfg.providers
        assert cfg.etherscan_api_key is None
        assert cfg.default_vault is None
        assert cfg.vaults == []


class TestLoadConfig:
    def test_returns_empty_when_no_file(self):
        cfg = load_config()
        assert cfg == FusionConfig()

    def test_loads_full_data(self, tmp_path):
        config_dir = tmp_path / ".fusion"
        config_dir.mkdir()
        data = {
            "providers": {"1": "https://rpc.example.com"},
            "etherscan_api_key": "abc123",
            "default_vault": "0xVAULT",
            "vaults": [{"address": "0xVAULT", "label": "test", "chain_id": 42161}],
        }
        (config_dir / "config.json").write_text(json.dumps(data), encoding="utf-8")

        cfg = load_config()

        assert cfg.providers == {"1": "https://rpc.example.com"}
        assert cfg.etherscan_api_key == "abc123"
        assert cfg.default_vault == "0xVAULT"
        assert len(cfg.vaults) == 1
        assert cfg.vaults[0] == VaultEntry(
            address="0xVAULT", label="test", chain_id=42161
        )

    def test_loads_partial_data(self, tmp_path):
        config_dir = tmp_path / ".fusion"
        config_dir.mkdir()
        (config_dir / "config.json").write_text(
            json.dumps({"providers": {"1": "http://localhost:8545"}}),
            encoding="utf-8",
        )

        cfg = load_config()

        assert cfg.providers == {"1": "http://localhost:8545"}
        assert cfg.etherscan_api_key is None
        assert cfg.default_vault is None
        assert cfg.vaults == []


class TestSaveConfig:
    def test_creates_directory_and_writes_json(self, tmp_path):
        cfg = FusionConfig(
            providers={"1": "https://rpc.example.com"},
            etherscan_api_key="key",
            default_vault="0xABC",
            vaults=[VaultEntry(address="0xABC", label="v1", chain_id=1)],
        )
        save_config(cfg)

        config_file = tmp_path / ".fusion" / "config.json"
        assert config_file.exists()
        raw = json.loads(config_file.read_text(encoding="utf-8"))
        assert raw["providers"] == {"1": "https://rpc.example.com"}
        assert raw["etherscan_api_key"] == "key"
        assert raw["vaults"][0]["address"] == "0xABC"

    def test_roundtrip(self):
        original = FusionConfig(
            providers={"42161": "https://arb-rpc.example.com"},
            etherscan_api_key="secret",
            default_vault="0xDEF",
            vaults=[
                VaultEntry(address="0xDEF", label="arb-vault", chain_id=42161),
                VaultEntry(address="0x123", label="eth-vault", chain_id=1),
            ],
        )
        save_config(original)
        loaded = load_config()

        assert loaded.providers == original.providers
        assert loaded.etherscan_api_key == original.etherscan_api_key
        assert loaded.default_vault == original.default_vault
        assert loaded.vaults == original.vaults


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

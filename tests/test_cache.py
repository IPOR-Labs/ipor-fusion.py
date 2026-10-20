import json
import logging
import os
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from ipor_fusion import (
    FileContractCache,
    NullCache,
    Web3Context,
    cache_key,
    default_contract_cache,
    fetch_deployment_info,
    resolve_token_decimals,
    resolve_token_symbol,
    vault_data,
)
from ipor_fusion.types import ChainId, parse_plasma_vault_address, parse_tx_hash

ETHEREUM = ChainId(1)
BASE = ChainId(8453)
CHECKSUM = "0xAbCdEf0123456789aBcDeF0123456789AbCdEf01"
LOWER = CHECKSUM.lower()
VAULT = parse_plasma_vault_address(CHECKSUM)


@pytest.fixture
def path(tmp_path: Path) -> Path:
    return tmp_path / "cache" / "contract_cache.json"


def _write(path: Path, entries: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(entries), encoding="utf-8")


def _on_disk(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


class TestKeys:
    def test_a_key_lowercases_the_address(self):
        assert cache_key(ETHEREUM, CHECKSUM, "symbol") == (ETHEREUM, LOWER, "symbol")

    def test_a_malformed_address_is_rejected(self):
        with pytest.raises(ValueError):
            cache_key(ETHEREUM, "0xabc", "symbol")

    def test_the_same_address_on_two_chains_is_two_entries(self, path):
        cache = FileContractCache(path)
        cache.put(
            {
                cache_key(ETHEREUM, CHECKSUM, "symbol"): "USDC",
                cache_key(BASE, CHECKSUM, "symbol"): "USDbC",
            }
        )
        reread = FileContractCache(path)
        assert reread.get([cache_key(BASE, LOWER, "symbol")]) == {
            cache_key(BASE, LOWER, "symbol"): "USDbC"
        }
        assert _on_disk(path) == {
            f"1:{LOWER}:symbol": "USDC",
            f"8453:{LOWER}:symbol": "USDbC",
        }


class TestFileContractCache:
    def test_a_missing_file_is_empty_and_not_created_by_a_read(self, path):
        assert FileContractCache(path).get([cache_key(ETHEREUM, LOWER, "name")]) == {}
        assert not path.exists()

    def test_entries_written_by_another_process_are_kept(self, path):
        first, second = FileContractCache(path), FileContractCache(path)
        first.get([])  # loaded before the other writer
        second.put({cache_key(ETHEREUM, LOWER, "name"): "Vault"})
        first.put({cache_key(BASE, LOWER, "name"): "Other"})
        assert _on_disk(path) == {
            f"1:{LOWER}:name": "Vault",
            f"8453:{LOWER}:name": "Other",
        }

    def test_3x_entries_keep_names_and_drop_chainless_tokens(self, path):
        _write(
            path,
            {
                f"1:{CHECKSUM}": "Vault",
                f"symbol:{LOWER}": "USDC",
                f"decimals:{LOWER}": "6",
                "1:0xnot-an-address": "Junk",
                f"1:{LOWER}:unknown": "Junk",
                f"1:{LOWER}:decimals": 6,
            },
        )
        cache = FileContractCache(path)
        assert cache.get(
            [cache_key(ETHEREUM, LOWER, f) for f in ("name", "symbol", "decimals")]
        ) == {cache_key(ETHEREUM, LOWER, "name"): "Vault"}

    def test_a_canonical_entry_wins_over_a_3x_spelling(self, path):
        _write(path, {f"1:{LOWER}:name": "NEW", f"1:{CHECKSUM}": "OLD"})
        key = cache_key(ETHEREUM, LOWER, "name")
        assert FileContractCache(path).get([key]) == {key: "NEW"}

    @pytest.mark.parametrize("content", ["{not json", "[1, 2]", "\x00\x01"])
    def test_a_corrupt_file_reads_empty_and_is_rewritten(self, path, content, caplog):
        path.parent.mkdir(parents=True)
        path.write_text(content, encoding="utf-8")
        cache = FileContractCache(path)
        key = cache_key(ETHEREUM, LOWER, "name")
        with caplog.at_level(logging.WARNING, logger="ipor_fusion.cache"):
            assert cache.get([key]) == {}
        assert "ignored" in caplog.text
        cache.put({key: "Vault"})
        assert _on_disk(path) == {f"1:{LOWER}:name": "Vault"}

    def test_a_write_replaces_the_file_atomically(self, path):
        cache = FileContractCache(path)
        cache.put({cache_key(ETHEREUM, LOWER, "name"): "Vault"})
        with patch("ipor_fusion.cache.os.replace", side_effect=OSError("disk full")):
            cache.put({cache_key(BASE, LOWER, "name"): "Other"})
        # The failed write left the old file whole and no temporary file behind.
        assert _on_disk(path) == {f"1:{LOWER}:name": "Vault"}
        assert sorted(p.name for p in path.parent.iterdir()) == [path.name]
        # This process still sees what it could not persist.
        key = cache_key(BASE, LOWER, "name")
        assert cache.get([key]) == {key: "Other"}

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
    def test_a_read_only_directory_degrades_to_memory(self, path, caplog):
        path.parent.mkdir(parents=True)
        path.parent.chmod(0o500)
        try:
            cache = FileContractCache(path)
            key = cache_key(ETHEREUM, LOWER, "symbol")
            with caplog.at_level(logging.WARNING, logger="ipor_fusion.cache"):
                cache.put({key: "USDC"})
            assert "not written" in caplog.text
            assert cache.get([key]) == {key: "USDC"}
            assert not path.exists()
        finally:
            path.parent.chmod(0o700)

    @pytest.mark.skipif(os.geteuid() == 0, reason="root ignores permissions")
    def test_an_unreadable_file_reads_empty(self, path):
        _write(path, {f"1:{LOWER}:name": "Vault"})
        path.chmod(0o000)
        try:
            assert (
                FileContractCache(path).get([cache_key(ETHEREUM, LOWER, "name")]) == {}
            )
        finally:
            path.chmod(0o600)


class TestDefaultAndInjection:
    def test_the_default_follows_xdg_cache_home_at_call_time(
        self, tmp_path, monkeypatch
    ):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "a"))
        first = default_contract_cache()
        assert first.path == tmp_path / "a" / "ipor-fusion" / "contract_cache.json"
        assert default_contract_cache() is first
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "b"))
        assert default_contract_cache().path.parent.parent == tmp_path / "b"

    def test_a_context_uses_the_default_unless_given_one(self, tmp_path, monkeypatch):
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert Web3Context(MagicMock(), ETHEREUM).cache is default_contract_cache()
        null = NullCache()
        ctx = Web3Context(MagicMock(), ETHEREUM, cache=null)
        assert ctx.cache is null
        assert ctx.at_block(5).cache is null

    def test_from_url_passes_the_cache(self):
        null = NullCache()
        with patch("ipor_fusion.core.context.Web3") as web3_cls:
            web3_cls.return_value.eth.chain_id = 1
            ctx = Web3Context.from_url("http://localhost:0", cache=null)
        assert ctx.cache is null

    def test_null_cache_stores_nothing(self):
        cache = NullCache()
        key = cache_key(ETHEREUM, LOWER, "name")
        cache.put({key: "Vault"})
        assert cache.get([key]) == {}


def _ctx(cache, chain_id=ETHEREUM, code=b"\x01") -> MagicMock:
    ctx = MagicMock(chain_id=chain_id, cache=cache, default_block="latest")
    ctx.web3.eth.get_code.return_value = code
    return ctx


class TestTokenResolvers:
    @patch("ipor_fusion.vault_data.ERC20")
    def test_symbols_are_cached_per_chain(self, erc20, path):
        cache = FileContractCache(path)
        erc20.return_value.symbol.return_value.call.return_value = "USDC"
        assert resolve_token_symbol(_ctx(cache), CHECKSUM) == "USDC"
        erc20.return_value.symbol.return_value.call.return_value = "USDbC"
        assert resolve_token_symbol(_ctx(cache, BASE), CHECKSUM) == "USDbC"
        assert resolve_token_symbol(_ctx(cache), LOWER) == "USDC"
        assert _on_disk(path) == {
            f"1:{LOWER}:symbol": "USDC",
            f"8453:{LOWER}:symbol": "USDbC",
        }

    @patch("ipor_fusion.vault_data.ERC20")
    def test_no_contract_is_not_cached(self, erc20, path):
        cache = FileContractCache(path)
        assert resolve_token_symbol(_ctx(cache, code=b""), CHECKSUM) == "no contract"
        assert resolve_token_decimals(_ctx(cache, code=b""), CHECKSUM) is None
        assert not path.exists()
        erc20.return_value.symbol.return_value.call.return_value = "LATE"
        erc20.return_value.decimals.return_value.call.return_value = 18
        assert resolve_token_symbol(_ctx(cache), CHECKSUM) == "LATE"
        assert resolve_token_decimals(_ctx(cache), CHECKSUM) == 18

    @patch("ipor_fusion.vault_data.ERC20")
    def test_decimals_are_cached_per_chain(self, erc20, path):
        cache = FileContractCache(path)
        erc20.return_value.decimals.return_value.call.return_value = 6
        assert resolve_token_decimals(_ctx(cache), CHECKSUM) == 6
        ctx = _ctx(cache)
        assert resolve_token_decimals(ctx, CHECKSUM) == 6
        ctx.web3.eth.get_code.assert_not_called()
        assert _on_disk(path) == {f"1:{LOWER}:decimals": "6"}

    def test_token_metadata_reads_and_fills_the_contexts_cache(self, path):
        cache = FileContractCache(path)
        cache.put({cache_key(ETHEREUM, LOWER, "symbol"): "MAINNET"})
        multicall = MagicMock()
        multicall.return_value.try_aggregate.return_value = ["BASE", 6]
        with (
            patch.object(vault_data, "Multicall3", multicall),
            patch.object(
                vault_data, "_collect_breakdown_token_addresses", return_value=[LOWER]
            ),
        ):
            symbols, decimals = vault_data._token_metadata(
                _ctx(cache, BASE),
                SimpleNamespace(market_substrates={}),  # type: ignore[arg-type]
                SimpleNamespace(morpho_positions=[], aave_positions=[]),  # type: ignore[arg-type]
            )
        assert (symbols, decimals) == ({LOWER: "BASE"}, {LOWER: 6})
        assert _on_disk(path) == {
            f"1:{LOWER}:symbol": "MAINNET",
            f"8453:{LOWER}:decimals": "6",
            f"8453:{LOWER}:symbol": "BASE",
        }


TX = parse_tx_hash("0x" + "11" * 32)


def _deployment_ctx(cache) -> MagicMock:
    ctx = _ctx(cache)
    ctx.web3.eth.get_transaction.return_value = {"blockNumber": 100}
    ctx.web3.eth.get_block.return_value = {"timestamp": 1_700_000_000}
    return ctx


class TestFetchDeploymentInfo:
    @patch("ipor_fusion.vault_data.get_deployment_tx", return_value=(TX, None))
    def test_resolved_once_then_cached(self, get_tx, path):
        cache = FileContractCache(path)
        assert fetch_deployment_info(_deployment_ctx(cache), VAULT) == (
            100,
            1_700_000_000,
            None,
        )
        assert fetch_deployment_info(_deployment_ctx(cache), VAULT) == (
            100,
            1_700_000_000,
            None,
        )
        get_tx.assert_called_once()
        assert _on_disk(path) == {
            f"1:{LOWER}:deployment_block": "100",
            f"1:{LOWER}:deployment_timestamp": "1700000000",
        }

    @patch("ipor_fusion.vault_data.get_deployment_tx", return_value=(TX, None))
    def test_an_rpc_failure_is_rpc_fetch_failed(self, _get_tx):
        ctx = _deployment_ctx(NullCache())
        ctx.web3.eth.get_block.side_effect = OSError("connection reset")
        assert fetch_deployment_info(ctx, VAULT) == (None, None, "rpc-fetch-failed")

    @patch("ipor_fusion.vault_data.get_deployment_tx", return_value=(TX, None))
    def test_a_cache_failure_is_not_an_error(self, _get_tx):
        broken = MagicMock()
        broken.get.side_effect = OSError("unreadable")
        broken.put.side_effect = OSError("read-only file system")
        assert fetch_deployment_info(_deployment_ctx(broken), VAULT) == (
            100,
            1_700_000_000,
            None,
        )
        broken.put.assert_called_once()

    @patch("ipor_fusion.vault_data.get_deployment_tx", return_value=(TX, None))
    @patch("ipor_fusion.cache.os.replace", side_effect=OSError("read-only"))
    def test_an_unwritable_file_cache_is_not_an_error(self, _replace, _get_tx, path):
        ctx = _deployment_ctx(FileContractCache(path))
        assert fetch_deployment_info(ctx, VAULT) == (100, 1_700_000_000, None)

    @patch(
        "ipor_fusion.vault_data.get_deployment_tx",
        return_value=(None, "no-api-key"),
    )
    def test_the_explorer_error_passes_through(self, _get_tx):
        ctx = _deployment_ctx(NullCache())
        assert fetch_deployment_info(ctx, VAULT) == (None, None, "no-api-key")

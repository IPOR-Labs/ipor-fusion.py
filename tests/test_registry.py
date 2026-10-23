"""The ipor-abi address registry client, offline: downloads are faked."""

import json

import pytest
from web3 import Web3

import ipor_fusion.registry as registry_module
from ipor_fusion import (
    REGISTRY_CHAIN_IDS,
    AddressNotInRegistryError,
    AddressRegistry,
    AddressRegistryError,
    UnsupportedChainError,
    build_address_list_json,
    build_address_lookup_json,
    build_address_names_json,
    fetch_address_registries,
    fetch_address_registry,
    registry_url,
)
from ipor_fusion.chains import CHAIN_NAMES
from ipor_fusion.types import ChainId

BASE, ETHEREUM = ChainId(8453), ChainId(1)
PROXY = "0x1455717668fa96534f675856347a973fa907e922"
FUSE = "0x26fD6EF391E98C78CfCA27e00c3d15be4D941625"
COMMIT = "3b158d0fe813d6be2980306fee99b19d67873b65"


@pytest.fixture(autouse=True)
def _fresh_cache(monkeypatch):
    monkeypatch.setattr(registry_module, "_cache", {})


@pytest.fixture
def downloads(monkeypatch):
    """Serve fake files per chain; record every URL read."""
    files: dict[str, object] = {
        "base": {"IporFusionFactoryProxy": PROXY, "SupplyFuseAaveV3": FUSE},
        "ethereum": {"SupplyFuseAaveV3": FUSE, "SupplyFuseAaveV3Lido": PROXY},
    }
    seen: list[str] = []

    def download(url: str, _timeout: float) -> bytes:
        seen.append(url)
        chain = url.split("/mainnet-")[1].removesuffix("-fusion/addresses.json")
        return json.dumps(files.get(chain, {})).encode()

    monkeypatch.setattr(registry_module, "_download", download)
    return seen


def test_registry_chains_are_named_chains():
    assert REGISTRY_CHAIN_IDS <= set(CHAIN_NAMES)


def test_url_names_the_chain_file_at_the_ref():
    assert registry_url(BASE, COMMIT) == (
        "https://raw.githubusercontent.com/IPOR-Labs/ipor-abi/"
        f"{COMMIT}/mainnet/mainnet-base-fusion/addresses.json"
    )


@pytest.mark.parametrize("ref", ["", "main?x=1", "../main", "a b", "main#frag"])
def test_url_rejects_anything_but_a_ref(ref):
    with pytest.raises(ValueError, match="not a git ref"):
        registry_url(BASE, ref)


def test_a_chain_without_a_deployment_file_is_unsupported():
    with pytest.raises(UnsupportedChainError, match="no Fusion deployment file"):
        registry_url(ChainId(10))


def test_fetch_checksums_and_looks_up(downloads):
    registry = fetch_address_registry(BASE)

    assert registry.address("IporFusionFactoryProxy") == Web3.to_checksum_address(PROXY)
    assert registry.names_of(PROXY.upper().replace("0X", "0x")) == [
        "IporFusionFactoryProxy"
    ]
    assert set(registry.find("aavev3")) == {"SupplyFuseAaveV3"}
    with pytest.raises(AddressNotInRegistryError, match="'Nope' is not in the base"):
        registry.address("Nope")


def test_a_branch_is_cached_for_ten_minutes_and_a_commit_for_good(
    downloads, monkeypatch
):
    now = [1000.0]
    monkeypatch.setattr(registry_module.time, "monotonic", lambda: now[0])
    fetch_address_registry(BASE)
    fetch_address_registry(BASE, ref=COMMIT)
    now[0] += 599
    fetch_address_registry(BASE)
    assert len(downloads) == 2
    now[0] += 2
    fetch_address_registry(BASE)
    fetch_address_registry(BASE, ref=COMMIT)
    assert len(downloads) == 3


@pytest.mark.parametrize(
    ("body", "message"),
    [
        (b"not json", "is not JSON"),
        (b"[]", "is not a JSON object"),
        (b'{"X": "0x12"}', "'X' is not an address"),
        (b'{"X": 5}', "'X' is not an address"),
    ],
)
def test_a_malformed_file_raises(monkeypatch, body, message):
    monkeypatch.setattr(registry_module, "_download", lambda _url, _t: body)
    with pytest.raises(AddressRegistryError, match=message):
        fetch_address_registry(BASE)


def test_an_unreachable_registry_raises(monkeypatch):
    def unreachable(_url, timeout):
        raise OSError("connection refused")

    monkeypatch.setattr(registry_module, "urlopen", unreachable)
    with pytest.raises(AddressRegistryError, match="connection refused"):
        fetch_address_registry(BASE)


def test_registries_default_to_every_chain_in_order(downloads):
    registries = fetch_address_registries()
    assert [r.chain_id for r in registries] == sorted(REGISTRY_CHAIN_IDS)


def _registries() -> list[AddressRegistry]:
    return fetch_address_registries([BASE, ETHEREUM])


def test_lookup_by_name_is_a_substring_on_every_chain(downloads):
    document = build_address_lookup_json(" supplyfuseaavev3 ", _registries())
    assert document["query"] == "supplyfuseaavev3"
    assert document["query_type"] == "name"
    assert [(m["chain_id"], m["name"]) for m in document["matches"]] == [
        (1, "SupplyFuseAaveV3"),
        (1, "SupplyFuseAaveV3Lido"),
        (8453, "SupplyFuseAaveV3"),
    ]
    assert document["match_count"] == 3


def test_lookup_by_address_is_exact_and_names_each_chain(downloads):
    document = build_address_lookup_json(PROXY, _registries())
    assert document["query_type"] == "address"
    assert [(m["chain"], m["name"]) for m in document["matches"]] == [
        ("ethereum", "SupplyFuseAaveV3Lido"),
        ("base", "IporFusionFactoryProxy"),
    ]


def test_names_collect_chains_per_name(downloads):
    document = build_address_names_json(_registries())
    assert document["chain_ids"] == [1, 8453]
    assert document["names"] == [
        {"name": "IporFusionFactoryProxy", "chain_ids": [8453]},
        {"name": "SupplyFuseAaveV3", "chain_ids": [1, 8453]},
        {"name": "SupplyFuseAaveV3Lido", "chain_ids": [1]},
    ]


def test_list_is_the_sorted_map(downloads):
    document = build_address_list_json(fetch_address_registry(BASE))
    assert document["chain"] == "base"
    assert document["ref"] == "main"
    assert document["count"] == 2
    assert list(document["addresses"]) == ["IporFusionFactoryProxy", "SupplyFuseAaveV3"]

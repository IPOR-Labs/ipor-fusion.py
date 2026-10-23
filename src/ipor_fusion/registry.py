"""IPOR Fusion deployment addresses by contract name, from the public ipor-abi
registry (``mainnet/mainnet-<chain>-fusion/addresses.json``): fuses, factories,
managers and oracles, the names the deployment uses (``SupplyFuseAaveV3``)."""

from __future__ import annotations

import json
import re
import threading
import time
from collections.abc import Iterable, Mapping
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass
from http.client import HTTPException
from typing import Literal, TypedDict
from urllib.request import urlopen

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.chains import CHAIN_NAMES
from ipor_fusion.errors import (
    AddressNotInRegistryError,
    AddressRegistryError,
    UnsupportedChainError,
)
from ipor_fusion.types import ChainId

REGISTRY_REPOSITORY = "IPOR-Labs/ipor-abi"

#: Chains with a ``mainnet-<chain>-fusion`` deployment file in the registry.
REGISTRY_CHAIN_IDS: frozenset[ChainId] = frozenset(
    ChainId(cid)
    for cid in (
        1,
        14,
        130,
        143,
        239,
        999,
        3637,
        4663,
        8453,
        9745,
        42161,
        43114,
        57073,
        747474,
    )
)

# A moving ref (a branch) is re-read after this many seconds; a full commit
# hash names fixed content and is kept for the process's life.
_MOVING_REF_TTL = 600.0
_COMMIT = re.compile(r"[0-9a-f]{40}")
_REF = re.compile(r"[A-Za-z0-9._/-]+")
_ADDRESS = re.compile(r"0x[0-9a-fA-F]{40}")


@dataclass(frozen=True)
class AddressRegistry:
    """One chain's ``{contract name: address}`` map at registry ``ref``."""

    chain_id: ChainId
    ref: str
    addresses: Mapping[str, ChecksumAddress]

    def address(self, name: str) -> ChecksumAddress:
        """The address named exactly ``name``; raises
        `AddressNotInRegistryError` when the chain has no such contract."""
        try:
            return self.addresses[name]
        except KeyError:
            raise AddressNotInRegistryError(
                f"{name!r} is not in the {CHAIN_NAMES[self.chain_id]} registry"
                f" at {self.ref}"
            ) from None

    def names_of(self, address: str) -> list[str]:
        """Every name ``address`` is registered under (some contracts have two)."""
        wanted = address.lower()
        return sorted(n for n, a in self.addresses.items() if a.lower() == wanted)

    def find(self, query: str) -> dict[str, ChecksumAddress]:
        """The entries whose name contains ``query``, ignoring case."""
        needle = query.lower()
        return {n: a for n, a in self.addresses.items() if needle in n.lower()}


_cache: dict[tuple[ChainId, str], tuple[float, AddressRegistry]] = {}
_cache_lock = threading.Lock()


def registry_url(chain_id: ChainId, ref: str = "main") -> str:
    """The raw ``addresses.json`` URL of ``chain_id`` at ``ref``."""
    if chain_id not in REGISTRY_CHAIN_IDS:
        supported = ", ".join(
            f"{CHAIN_NAMES[c]} ({c})" for c in sorted(REGISTRY_CHAIN_IDS)
        )
        raise UnsupportedChainError(
            f"chain {chain_id} has no Fusion deployment file in {REGISTRY_REPOSITORY};"
            f" chains with one: {supported}"
        )
    if not _REF.fullmatch(ref) or ".." in ref:
        raise ValueError(f"not a git ref: {ref!r}")
    return (
        f"https://raw.githubusercontent.com/{REGISTRY_REPOSITORY}/{ref}/mainnet/"
        f"mainnet-{CHAIN_NAMES[chain_id]}-fusion/addresses.json"
    )


def fetch_address_registry(
    chain_id: ChainId, *, ref: str = "main", timeout: float = 20.0
) -> AddressRegistry:
    """The registry of ``chain_id`` at ``ref`` (a branch, tag or commit hash).

    Cached in the process: a commit hash for good, a branch or tag for ten
    minutes. Raises `UnsupportedChainError` for a chain without a deployment
    file and `AddressRegistryError` when the file cannot be read or parsed.
    """
    url = registry_url(chain_id, ref)
    key = (chain_id, ref)
    with _cache_lock:
        cached = _cache.get(key)
    if cached is not None and (
        _COMMIT.fullmatch(ref) or time.monotonic() - cached[0] < _MOVING_REF_TTL
    ):
        return cached[1]
    registry = AddressRegistry(chain_id, ref, _parse(_download(url, timeout), url))
    with _cache_lock:
        _cache[key] = (time.monotonic(), registry)
    return registry


def _download(url: str, timeout: float) -> bytes:
    try:
        with urlopen(url, timeout=timeout) as response:  # noqa: S310  # fixed https host
            return response.read()
    except (OSError, HTTPException) as exc:
        raise AddressRegistryError(f"cannot read {url}: {exc}") from exc


def _parse(raw: bytes, url: str) -> dict[str, ChecksumAddress]:
    try:
        body: object = json.loads(raw)
    except ValueError as exc:
        raise AddressRegistryError(f"{url} is not JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise AddressRegistryError(f"{url} is not a JSON object")
    addresses: dict[str, ChecksumAddress] = {}
    for name, value in body.items():
        if not isinstance(value, str) or not _ADDRESS.fullmatch(value):
            raise AddressRegistryError(f"{url}: {name!r} is not an address: {value!r}")
        addresses[str(name)] = Web3.to_checksum_address(value)
    return addresses


def fetch_address_registries(
    chain_ids: Iterable[ChainId] | None = None, *, ref: str = "main"
) -> list[AddressRegistry]:
    """The registries of ``chain_ids`` (default: every chain with one), read
    in parallel, ordered by chain id."""
    chains = sorted(REGISTRY_CHAIN_IDS if chain_ids is None else set(chain_ids))
    with ThreadPoolExecutor(max_workers=8) as pool:
        return list(pool.map(lambda c: fetch_address_registry(c, ref=ref), chains))


# ── Documents shared by the CLI (--json) and the MCP tools ─────────────────


class AddressMatchJson(TypedDict):
    chain_id: ChainId
    chain: str
    name: str
    address: ChecksumAddress


class AddressLookupJson(TypedDict):
    query: str
    query_type: Literal["address", "name"]
    match_count: int
    matches: list[AddressMatchJson]


class AddressNameJson(TypedDict):
    name: str
    chain_ids: list[ChainId]


class AddressNamesJson(TypedDict):
    chain_ids: list[ChainId]
    names: list[AddressNameJson]


class AddressListJson(TypedDict):
    chain_id: ChainId
    chain: str
    ref: str
    count: int
    addresses: dict[str, ChecksumAddress]


def build_address_lookup_json(
    query: str, registries: Iterable[AddressRegistry]
) -> AddressLookupJson:
    """Matches of ``query`` across ``registries``: an address (``0x`` and 40 hex
    digits) matches exactly, anything else as a case-insensitive name substring."""
    text = query.strip()
    is_address = bool(_ADDRESS.fullmatch(text))
    matches: list[AddressMatchJson] = []
    for registry in registries:
        found = (
            {n: registry.addresses[n] for n in registry.names_of(text)}
            if is_address
            else registry.find(text)
        )
        matches += [
            AddressMatchJson(
                chain_id=registry.chain_id,
                chain=CHAIN_NAMES[registry.chain_id],
                name=name,
                address=address,
            )
            for name, address in sorted(found.items())
        ]
    return AddressLookupJson(
        query=text,
        query_type="address" if is_address else "name",
        match_count=len(matches),
        matches=matches,
    )


def build_address_names_json(registries: Iterable[AddressRegistry]) -> AddressNamesJson:
    """Every contract name in ``registries``, with the chains that have it."""
    chains: list[ChainId] = []
    by_name: dict[str, list[ChainId]] = {}
    for registry in registries:
        chains.append(registry.chain_id)
        for name in registry.addresses:
            by_name.setdefault(name, []).append(registry.chain_id)
    return AddressNamesJson(
        chain_ids=chains,
        names=[
            AddressNameJson(name=name, chain_ids=ids)
            for name, ids in sorted(by_name.items())
        ],
    )


def build_address_list_json(registry: AddressRegistry) -> AddressListJson:
    return AddressListJson(
        chain_id=registry.chain_id,
        chain=CHAIN_NAMES[registry.chain_id],
        ref=registry.ref,
        count=len(registry.addresses),
        addresses=dict(sorted(registry.addresses.items())),
    )

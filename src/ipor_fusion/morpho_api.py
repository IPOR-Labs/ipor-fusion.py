"""Minimal client for the Morpho public GraphQL API (`blue-api.morpho.org`).

Used by `fusion market morpho-blue` to discover MetaMorpho vaults supplying a
market and their `PublicAllocator` flow caps, and by `fusion market meta-morpho`
to inspect a vault's roles, caps, and allocations. Supports both MetaMorpho V1
(`vaultByAddress`) and Morpho Vault V2 (`vaultV2ByAddress`). Stdlib-only.
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from http.client import HTTPException
from typing import Any
from urllib.request import Request, urlopen

from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3 import Web3
from web3.types import Timestamp

from ipor_fusion.errors import MorphoApiError
from ipor_fusion.types import (
    Amount,
    ChainId,
    Decimals,
    MorphoBlueMarketId,
    parse_morpho_market_id,
)

MORPHO_API_URL = "https://blue-api.morpho.org/graphql"

# Singleton PublicAllocator deployments per chain. Updated 2026-04 from the
# Morpho API (`publicAllocators` query). Anyone can call `reallocateTo` on
# this contract — the vault must have granted it the ALLOCATOR_ROLE for
# public reallocation to actually go through.
PUBLIC_ALLOCATOR_ADDRESSES: dict[ChainId, ChecksumAddress] = {
    ChainId(1): Web3.to_checksum_address(
        "0xfd32fA2ca22c76dD6E550706Ad913FC6CE91c75D"
    ),  # Ethereum
    ChainId(10): Web3.to_checksum_address(
        "0x0d68a97324E602E02799CD83B42D337207B40658"
    ),  # OP Mainnet
    ChainId(137): Web3.to_checksum_address(
        "0xfac15aff53ADd2ff80C2962127C434E8615Df0d3"
    ),  # Polygon
    ChainId(8453): Web3.to_checksum_address(
        "0xA090dD1a701408Df1d4d0B85b716c87565f90467"
    ),  # Base
    ChainId(42161): Web3.to_checksum_address(
        "0x769583Af5e9D03589F159EbEC31Cc2c23E8C355E"
    ),  # Arbitrum One
    ChainId(130): Web3.to_checksum_address(
        "0xB0c9a107fA17c779B3378210A7a593e88938C7C9"
    ),  # Unichain
    ChainId(480): Web3.to_checksum_address(
        "0xef9889B4e443DEd35FA0Bd060f2104Cca94e6A43"
    ),  # World Chain
    ChainId(143): Web3.to_checksum_address(
        "0xfd70575B732F9482F4197FE1075492e114E97302"
    ),  # Monad
    ChainId(999): Web3.to_checksum_address(
        "0x517505be22D9068687334e69ae7a02fC77edf4Fc"
    ),  # HyperEVM
    ChainId(988): Web3.to_checksum_address(
        "0xbCB063D4B6D479b209C186e462828CBACaC82DbE"
    ),  # Stable
    ChainId(747474): Web3.to_checksum_address(
        "0x39EB6Da5e88194C82B13491Df2e8B3E213eD2412"
    ),  # Katana
}

_MARKET_QUERY = """
query MarketWithVaults($marketId: String!, $chainId: Int!) {
  marketById(marketId: $marketId, chainId: $chainId) {
    marketId
    lltv
    loanAsset { address symbol decimals }
    collateralAsset { address symbol decimals }
    oracle { address }
    irmAddress
    state {
      supplyAssets
      borrowAssets
      liquidityAssets
      utilization
      supplyApy
      borrowApy
      fee
      timestamp
    }
    supplyingVaults {
      address
      symbol
      name
      asset { symbol decimals }
      state {
        totalAssets
        allocation { market { marketId } supplyAssets supplyCap }
      }
      allocators { address }
      publicAllocatorConfig {
        fee
        admin
        flowCaps {
          market { marketId }
          maxIn
          maxOut
        }
      }
    }
  }
}
"""


@dataclass(slots=True)
class VaultFlowCap:
    """PublicAllocator flow caps for a (vault, market) pair."""

    fee_wei: int
    max_in: Amount
    max_out: Amount
    admin: ChecksumAddress | None  # the address that can configure these flow caps


@dataclass(slots=True)
class VaultAllocation:
    """A MetaMorpho vault's exposure to a single Morpho Blue market."""

    vault_address: ChecksumAddress
    vault_name: str
    vault_symbol: str
    asset_symbol: str
    asset_decimals: Decimals
    total_assets: Amount
    supply_assets: Amount  # currently allocated to *this* market
    supply_cap: Amount  # cap configured by curator for *this* market
    allocators: list[ChecksumAddress]  # addresses with ALLOCATOR_ROLE on the vault
    flow_cap: VaultFlowCap | None  # None if PublicAllocator not configured


@dataclass(slots=True)
class MorphoApiMarket:
    """Snapshot of a Morpho Blue market plus the vaults supplying to it."""

    market_id: MorphoBlueMarketId
    lltv: int
    loan_token: ChecksumAddress
    loan_symbol: str
    loan_decimals: Decimals
    collateral_token: ChecksumAddress
    collateral_symbol: str
    collateral_decimals: Decimals
    oracle: ChecksumAddress
    irm: ChecksumAddress
    supply_assets: Amount
    borrow_assets: Amount
    liquidity_assets: Amount
    utilization: float
    supply_apy: float
    borrow_apy: float
    fee_wad: int
    timestamp: Timestamp
    vaults: list[VaultAllocation]


@contextmanager
def _response_shape() -> Iterator[None]:
    """Report any failure to read the answer's shape as `MorphoApiError`."""
    try:
        yield
    except MorphoApiError:
        raise
    except (
        KeyError,
        IndexError,
        TypeError,
        ValueError,
        AttributeError,
        OverflowError,  # int() of a JSON number beyond float range (inf)
    ) as exc:
        raise MorphoApiError(
            f"Morpho API returned an unexpected response: {type(exc).__name__}: {exc}"
        ) from exc


def _post_query(
    query: str,
    variables: dict[str, Any],
    timeout: float,
    *,
    tolerate_not_found: bool = True,
) -> dict[str, Any]:
    """POST a GraphQL query and return the decoded JSON object. GraphQL errors
    raise, except NOT_FOUND ones when ``tolerate_not_found``: those come back
    with ``data: null`` and the caller decides."""
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = Request(  # noqa: S310  # fixed https endpoint, not user input
        MORPHO_API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed URL
            raw = resp.read()
    except (OSError, HTTPException) as exc:
        raise MorphoApiError(f"Morpho API unreachable: {exc}") from exc
    try:
        body = json.loads(raw)
    except ValueError as exc:
        raise MorphoApiError(f"Morpho API returned invalid JSON: {exc}") from exc
    if not isinstance(body, dict):
        raise MorphoApiError(
            f"Morpho API returned {type(body).__name__}, not a JSON object"
        )
    with _response_shape():
        if errors := body.get("errors"):
            if not tolerate_not_found or {e.get("status") for e in errors} - {
                "NOT_FOUND"
            }:
                raise MorphoApiError(f"Morpho API error: {errors}")
    return body


def fetch_market(
    market_id: MorphoBlueMarketId, chain_id: ChainId, *, timeout: float = 10.0
) -> MorphoApiMarket:
    """Fetch a Morpho Blue market with its supplying vaults from the public API.

    `market_id` is the 0x-prefixed market unique key. `chain_id` is the EVM chain
    ID (1 = mainnet, 8453 = base, ...). Raises `MorphoApiError` on network failure
    or if the market is not indexed, and on a response it cannot parse.
    """
    body = _post_query(
        _MARKET_QUERY,
        {"marketId": market_id, "chainId": chain_id},
        timeout,
        tolerate_not_found=False,
    )
    with _response_shape():
        market = (body.get("data") or {}).get("marketById")
        if not market:
            raise MorphoApiError(
                f"Market {market_id} not found on chain {chain_id} in Morpho index."
            )
        return _parse_market(market)


def _parse_market(raw: dict) -> MorphoApiMarket:
    state = raw.get("state") or {}
    market_id = _api_market_id(raw.get("marketId"))
    return MorphoApiMarket(
        market_id=market_id,
        lltv=int(raw["lltv"]),
        loan_token=_api_address(raw["loanAsset"]["address"]),
        loan_symbol=raw["loanAsset"]["symbol"],
        loan_decimals=Decimals(int(raw["loanAsset"]["decimals"])),
        collateral_token=_api_address(raw["collateralAsset"]["address"]),
        collateral_symbol=raw["collateralAsset"]["symbol"],
        collateral_decimals=Decimals(int(raw["collateralAsset"]["decimals"])),
        oracle=_api_address(raw["oracle"]["address"]),
        irm=_api_address(raw["irmAddress"]),
        supply_assets=Amount(int(state.get("supplyAssets") or 0)),
        borrow_assets=Amount(int(state.get("borrowAssets") or 0)),
        liquidity_assets=Amount(int(state.get("liquidityAssets") or 0)),
        utilization=float(state.get("utilization") or 0.0),
        supply_apy=float(state.get("supplyApy") or 0.0),
        borrow_apy=float(state.get("borrowApy") or 0.0),
        fee_wad=int(state.get("fee") or 0),
        timestamp=Timestamp(int(state.get("timestamp") or 0)),
        vaults=[_parse_vault(v, market_id) for v in raw.get("supplyingVaults") or []],
    )


_VAULT_V2_QUERY = """
query VaultV2($address: String!, $chainId: Int!) {
  vaultV2ByAddress(address: $address, chainId: $chainId) {
    address name symbol
    asset { address symbol decimals }
    totalAssets idleAssets liquidity sharePrice
    performanceFee performanceFeeRecipient
    managementFee managementFeeRecipient
    maxApy
    owner { address }
    curator { address }
    allocators { allocator { address } }
    sentinels { sentinel { address } }
    liquidityAdapter {
      __typename
      ... on MorphoMarketV1Adapter { address type assets }
      ... on MetaMorphoAdapter { address type assets metaMorpho { address symbol } }
    }
    adapters {
      items {
        __typename
        ... on MorphoMarketV1Adapter { address type assets }
        ... on MetaMorphoAdapter { address type assets metaMorpho { address symbol } }
      }
    }
    caps {
      items { id idData type absoluteCap relativeCap allocation }
    }
  }
}
"""

_VAULT_V1_QUERY = """
query VaultV1($address: String!, $chainId: Int!) {
  vaultByAddress(address: $address, chainId: $chainId) {
    address name symbol
    asset { address symbol decimals }
    state {
      totalAssets fee owner curator guardian feeRecipient
      allocation {
        market {
          marketId lltv
          loanAsset { symbol decimals }
          collateralAsset { symbol }
          state { supplyAssets borrowAssets supplyApy }
        }
        supplyAssets supplyCap
      }
    }
    allocators { address }
    publicAllocatorConfig {
      fee admin
      flowCaps { market { marketId } maxIn maxOut }
    }
  }
}
"""


@dataclass(slots=True)
class VaultV2Cap:
    """A single cap entry on a Morpho Vault V2.

    `cap_id` is the bytes32 cap key (keccak256 of `id_data`). `cap_type` is one
    of `MarketV1` (Morpho Blue market), `Adapter` (per-adapter cap),
    `Collateral` (cap by collateral token across markets), or `Unknown`.
    For `MarketV1`, `market_id`/`loan_token`/`collateral_token`/`oracle`/`irm`/
    `lltv` are decoded from `id_data`; for other types they are `None`.
    """

    cap_id: str
    cap_type: str
    id_data: str
    absolute_cap: Amount
    relative_cap_wad: int
    allocation: Amount
    market_id: MorphoBlueMarketId | None
    loan_token: ChecksumAddress | None
    collateral_token: ChecksumAddress | None
    oracle: ChecksumAddress | None
    irm: ChecksumAddress | None
    lltv: int | None

    @property
    def room(self) -> Amount:
        """Free space below the absolute cap (clamped at 0)."""
        return Amount(max(self.absolute_cap - self.allocation, 0))


@dataclass(slots=True)
class VaultV2Adapter:
    """An adapter contract attached to a Vault V2 (e.g. Morpho adapter)."""

    address: ChecksumAddress
    adapter_type: str
    assets: Amount
    inner_vault: (
        ChecksumAddress | None
    )  # for MorphoVaultV2Adapter: the underlying market vault


@dataclass(slots=True)
class VaultV2Info:
    """Snapshot of a Morpho Vault V2."""

    address: ChecksumAddress
    name: str
    symbol: str
    asset_address: ChecksumAddress
    asset_symbol: str
    asset_decimals: Decimals
    total_assets: Amount
    idle_assets: Amount
    liquidity: Amount
    share_price: float
    max_apy: float
    performance_fee: float
    performance_fee_recipient: ChecksumAddress | None
    management_fee: float
    management_fee_recipient: ChecksumAddress | None
    owner: ChecksumAddress
    curator: ChecksumAddress | None
    allocators: list[ChecksumAddress]
    sentinels: list[ChecksumAddress]
    liquidity_adapter: VaultV2Adapter | None
    adapters: list[VaultV2Adapter]
    caps: list[VaultV2Cap] = field(default_factory=list)


@dataclass(slots=True)
class VaultV1MarketAllocation:
    """A MetaMorpho V1 allocation entry."""

    market_id: MorphoBlueMarketId
    lltv: int
    loan_symbol: str
    loan_decimals: Decimals
    collateral_symbol: str
    market_supply_assets: Amount
    market_borrow_assets: Amount
    market_supply_apy: float
    supply_assets: Amount
    supply_cap: Amount

    @property
    def cap_room(self) -> Amount:
        return Amount(max(self.supply_cap - self.supply_assets, 0))


@dataclass(slots=True)
class VaultV1Info:
    """Snapshot of a MetaMorpho V1 vault."""

    address: ChecksumAddress
    name: str
    symbol: str
    asset_address: ChecksumAddress
    asset_symbol: str
    asset_decimals: Decimals
    total_assets: Amount
    fee_wad: int
    owner: ChecksumAddress
    curator: ChecksumAddress | None
    guardian: ChecksumAddress | None
    fee_recipient: ChecksumAddress | None
    allocators: list[ChecksumAddress]
    allocations: list[VaultV1MarketAllocation]
    public_allocator: VaultFlowCap | None  # vault-level (no flowCap filtering)
    public_allocator_flow_caps: dict[
        MorphoBlueMarketId, tuple[int, int]
    ]  # (maxIn, maxOut)


def fetch_vault(
    address: ChecksumAddress, chain_id: ChainId, *, timeout: float = 10.0
) -> VaultV2Info | VaultV1Info:
    """Fetch a Morpho vault by address. Tries V2 first, falls back to V1.

    Raises `MorphoApiError` if the address is not a Morpho vault on this chain,
    and on a response it cannot parse.
    """
    address = Web3.to_checksum_address(address)
    v2_body = _post_query(
        _VAULT_V2_QUERY, {"address": address, "chainId": chain_id}, timeout
    )
    with _response_shape():
        v2 = (v2_body.get("data") or {}).get("vaultV2ByAddress")
        if v2:
            return _parse_vault_v2(v2)
    v1_body = _post_query(
        _VAULT_V1_QUERY, {"address": address, "chainId": chain_id}, timeout
    )
    with _response_shape():
        v1 = (v1_body.get("data") or {}).get("vaultByAddress")
        if v1:
            return _parse_vault_v1(v1)
    raise MorphoApiError(
        f"Vault {address} not found on chain {chain_id} (neither V1 nor V2)."
    )


def _parse_vault_v2(raw: dict) -> VaultV2Info:
    asset = raw.get("asset") or {}
    liq_adapter = raw.get("liquidityAdapter") or {}
    adapters = [
        _parse_v2_adapter(a) for a in (raw.get("adapters") or {}).get("items", []) if a
    ]
    caps = [_parse_v2_cap(c) for c in (raw.get("caps") or {}).get("items", []) if c]
    return VaultV2Info(
        address=_api_address(raw.get("address")),
        name=raw.get("name") or "",
        symbol=raw.get("symbol") or "",
        asset_address=_api_address(asset.get("address")),
        asset_symbol=asset.get("symbol") or "",
        asset_decimals=Decimals(int(asset.get("decimals") or 0)),
        total_assets=Amount(int(raw.get("totalAssets") or 0)),
        idle_assets=Amount(int(raw.get("idleAssets") or 0)),
        liquidity=Amount(int(raw.get("liquidity") or 0)),
        share_price=float(raw.get("sharePrice") or 0.0),
        max_apy=float(raw.get("maxApy") or 0.0),
        performance_fee=float(raw.get("performanceFee") or 0.0),
        performance_fee_recipient=_api_optional_address(
            raw.get("performanceFeeRecipient")
        ),
        management_fee=float(raw.get("managementFee") or 0.0),
        management_fee_recipient=_api_optional_address(
            raw.get("managementFeeRecipient")
        ),
        owner=_api_address((raw.get("owner") or {}).get("address")),
        curator=_api_optional_address((raw.get("curator") or {}).get("address")),
        allocators=[
            _api_address(a["allocator"]["address"])
            for a in raw.get("allocators") or []
            if (a.get("allocator") or {}).get("address")
        ],
        sentinels=[
            _api_address(s["sentinel"]["address"])
            for s in raw.get("sentinels") or []
            if (s.get("sentinel") or {}).get("address")
        ],
        liquidity_adapter=_parse_v2_adapter(liq_adapter) if liq_adapter else None,
        adapters=adapters,
        caps=caps,
    )


def _parse_v2_adapter(raw: dict) -> VaultV2Adapter:
    return VaultV2Adapter(
        address=_api_address(raw.get("address")),
        adapter_type=raw.get("type") or raw.get("__typename") or "",
        assets=Amount(int(raw.get("assets") or 0)),
        inner_vault=_api_optional_address((raw.get("metaMorpho") or {}).get("address")),
    )


def _parse_v2_cap(raw: dict) -> VaultV2Cap:
    cap_type = raw.get("type") or "Unknown"
    id_data = raw.get("idData") or "0x"
    market_id, loan, collat, oracle, irm, lltv = (None, None, None, None, None, None)
    if cap_type == "MarketV1":
        decoded = _decode_market_v1_id_data(id_data)
        if decoded:
            loan, collat, oracle, irm, lltv = decoded
            market_id = _morpho_blue_market_id(loan, collat, oracle, irm, lltv)
    return VaultV2Cap(
        cap_id=raw.get("id") or "",
        cap_type=cap_type,
        id_data=id_data,
        absolute_cap=Amount(int(raw.get("absoluteCap") or 0)),
        relative_cap_wad=int(raw.get("relativeCap") or 0),
        allocation=Amount(int(raw.get("allocation") or 0)),
        market_id=market_id,
        loan_token=loan,
        collateral_token=collat,
        oracle=oracle,
        irm=irm,
        lltv=lltv,
    )


def _decode_market_v1_id_data(
    id_data: str,
) -> (
    tuple[ChecksumAddress, ChecksumAddress, ChecksumAddress, ChecksumAddress, int]
    | None
):
    """Decode a Vault V2 `MarketV1`-type cap idData into MarketParams.

    Vault V2 encodes the cap idData as `abi.encode(string idType, address adapter,
    address loan, address collateral, address oracle, address irm, uint256 lltv)`,
    where `idType` is the literal string `"this/marketParams"`. Returns
    `(loan, collateral, oracle, irm, lltv)` on success, or `None` if the payload
    does not match.
    """
    raw = id_data.removeprefix("0x")
    try:
        data = bytes.fromhex(raw)
        decoded_list = list(
            decode(
                [
                    "string",
                    "address",
                    "address",
                    "address",
                    "address",
                    "address",
                    "uint256",
                ],
                data,
            )
        )
    except Exception:
        # eth_abi raises various subclasses (DecodingError, InsufficientDataBytes, ...)
        # for malformed payloads — treat them all as "not a MarketV1 cap".
        return None
    if len(decoded_list) != 7 or decoded_list[0] != "this/marketParams":
        return None
    loan, collat, oracle, irm, lltv = (
        decoded_list[2],
        decoded_list[3],
        decoded_list[4],
        decoded_list[5],
        decoded_list[6],
    )
    return (
        Web3.to_checksum_address(loan),
        Web3.to_checksum_address(collat),
        Web3.to_checksum_address(oracle),
        Web3.to_checksum_address(irm),
        int(lltv),
    )


def _api_address(value: object) -> ChecksumAddress:
    if not isinstance(value, str) or not Web3.is_address(value):
        raise MorphoApiError(f"Morpho API returned an invalid address: {value!r}")
    return Web3.to_checksum_address(value)


def _api_optional_address(value: object) -> ChecksumAddress | None:
    return None if value is None or value == "" else _api_address(value)


def _api_market_id(value: object) -> MorphoBlueMarketId:
    try:
        return parse_morpho_market_id(str(value or ""))
    except ValueError as exc:
        raise MorphoApiError(
            f"Morpho API returned an invalid market id: {value!r}"
        ) from exc


def _morpho_blue_market_id(
    loan: str, collateral: str, oracle: str, irm: str, lltv: int
) -> MorphoBlueMarketId:
    """Compute a Morpho Blue market unique key from MarketParams.

    `marketId = keccak256(abi.encode(loanToken, collateralToken, oracle, irm, lltv))`
    — used by Morpho Blue's `idToMarketParams` mapping.
    """
    encoded = encode(
        ["address", "address", "address", "address", "uint256"],
        [loan, collateral, oracle, irm, lltv],
    )
    return parse_morpho_market_id(keccak(encoded))


def _parse_vault_v1(raw: dict) -> VaultV1Info:
    asset = raw.get("asset") or {}
    state = raw.get("state") or {}
    allocations = []
    for entry in state.get("allocation") or []:
        market = entry.get("market") or {}
        m_state = market.get("state") or {}
        loan_asset = market.get("loanAsset") or {}
        collat_asset = market.get("collateralAsset") or {}
        allocations.append(
            VaultV1MarketAllocation(
                market_id=_api_market_id(market.get("marketId")),
                lltv=int(market.get("lltv") or 0),
                loan_symbol=loan_asset.get("symbol") or "",
                loan_decimals=Decimals(int(loan_asset.get("decimals") or 0)),
                collateral_symbol=collat_asset.get("symbol") or "",
                market_supply_assets=Amount(int(m_state.get("supplyAssets") or 0)),
                market_borrow_assets=Amount(int(m_state.get("borrowAssets") or 0)),
                market_supply_apy=float(m_state.get("supplyApy") or 0.0),
                supply_assets=Amount(int(entry.get("supplyAssets") or 0)),
                supply_cap=Amount(int(entry.get("supplyCap") or 0)),
            )
        )

    pac = raw.get("publicAllocatorConfig")
    flow_cap_summary: VaultFlowCap | None = None
    flow_caps: dict[MorphoBlueMarketId, tuple[int, int]] = {}
    if pac:
        flow_cap_summary = VaultFlowCap(
            fee_wei=int(pac.get("fee") or 0),
            max_in=Amount(0),
            max_out=Amount(0),
            admin=_api_optional_address(pac.get("admin")),
        )
        for fc in pac.get("flowCaps") or []:
            if raw_mid := (fc.get("market") or {}).get("marketId"):
                flow_caps[_api_market_id(raw_mid)] = (
                    int(fc.get("maxIn") or 0),
                    int(fc.get("maxOut") or 0),
                )

    return VaultV1Info(
        address=_api_address(raw.get("address")),
        name=raw.get("name") or "",
        symbol=raw.get("symbol") or "",
        asset_address=_api_address(asset.get("address")),
        asset_symbol=asset.get("symbol") or "",
        asset_decimals=Decimals(int(asset.get("decimals") or 0)),
        total_assets=Amount(int(state.get("totalAssets") or 0)),
        fee_wad=int(state.get("fee") or 0),
        owner=_api_address(state.get("owner")),
        curator=_api_optional_address(state.get("curator")),
        guardian=_api_optional_address(state.get("guardian")),
        fee_recipient=_api_optional_address(state.get("feeRecipient")),
        allocators=[
            _api_address(a["address"])
            for a in raw.get("allocators") or []
            if a.get("address")
        ],
        allocations=allocations,
        public_allocator=flow_cap_summary,
        public_allocator_flow_caps=flow_caps,
    )


def _is_market(entry: dict, market_id: MorphoBlueMarketId) -> bool:
    """Whether an allocation or flow-cap ``entry`` names ``market_id``, in any
    spelling the API returns."""
    raw = (entry.get("market") or {}).get("marketId")
    try:
        return parse_morpho_market_id(str(raw or "")) == market_id
    except ValueError:
        return False


def _parse_vault(raw: dict, market_id: MorphoBlueMarketId) -> VaultAllocation:
    asset = raw.get("asset") or {}
    state = raw.get("state") or {}
    allocation_for_market: dict = next(
        (a for a in (state.get("allocation") or []) if _is_market(a, market_id)),
        {},
    )
    pac = raw.get("publicAllocatorConfig")
    flow_cap: VaultFlowCap | None = None
    if pac:
        flow_for_market = next(
            (fc for fc in (pac.get("flowCaps") or []) if _is_market(fc, market_id)),
            None,
        )
        if flow_for_market:
            flow_cap = VaultFlowCap(
                fee_wei=int(pac.get("fee") or 0),
                max_in=Amount(int(flow_for_market.get("maxIn") or 0)),
                max_out=Amount(int(flow_for_market.get("maxOut") or 0)),
                admin=_api_optional_address(pac.get("admin")),
            )
    allocators = [
        _api_address(a["address"])
        for a in (raw.get("allocators") or [])
        if a.get("address")
    ]
    return VaultAllocation(
        vault_address=_api_address(raw["address"]),
        vault_name=raw.get("name") or "",
        vault_symbol=raw.get("symbol") or "",
        asset_symbol=asset.get("symbol") or "",
        asset_decimals=Decimals(int(asset.get("decimals") or 0)),
        total_assets=Amount(int(state.get("totalAssets") or 0)),
        supply_assets=Amount(int(allocation_for_market.get("supplyAssets") or 0)),
        supply_cap=Amount(int(allocation_for_market.get("supplyCap") or 0)),
        allocators=allocators,
        flow_cap=flow_cap,
    )

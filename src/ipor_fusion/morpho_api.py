"""Minimal client for the Morpho public GraphQL API (`blue-api.morpho.org`).

Used by `fusion market morpho-blue` to discover MetaMorpho vaults supplying a
market and their `PublicAllocator` flow caps, and by `fusion market meta-morpho`
to inspect a vault's roles, caps, and allocations. Supports both MetaMorpho V1
(`vaultByAddress`) and Morpho Vault V2 (`vaultV2ByAddress`). Stdlib-only.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from urllib.error import URLError
from urllib.request import Request, urlopen

from eth_abi import decode, encode
from eth_utils import keccak
from web3 import Web3

MORPHO_API_URL = "https://blue-api.morpho.org/graphql"

# Singleton PublicAllocator deployments per chain. Updated 2026-04 from the
# Morpho API (`publicAllocators` query). Anyone can call `reallocateTo` on
# this contract — the vault must have granted it the ALLOCATOR_ROLE for
# public reallocation to actually go through.
PUBLIC_ALLOCATOR_ADDRESSES: dict[int, str] = {
    1: "0xfd32fA2ca22c76dD6E550706Ad913FC6CE91c75D",  # Ethereum
    10: "0x0d68a97324E602E02799CD83B42D337207B40658",  # OP Mainnet
    137: "0xfac15aff53ADd2ff80C2962127C434E8615Df0d3",  # Polygon
    8453: "0xA090dD1a701408Df1d4d0B85b716c87565f90467",  # Base
    42161: "0x769583Af5e9D03589F159EbEC31Cc2c23E8C355E",  # Arbitrum One
    130: "0xB0c9a107fA17c779B3378210A7a593e88938C7C9",  # Unichain
    480: "0xef9889B4e443DEd35FA0Bd060f2104Cca94e6A43",  # World Chain
    143: "0xfd70575B732F9482F4197FE1075492e114E97302",  # Monad
    999: "0x517505be22D9068687334e69ae7a02fC77edf4Fc",  # HyperEVM
    988: "0xbCB063D4B6D479b209C186e462828CBACaC82DbE",  # Stable
    747474: "0x39EB6Da5e88194C82B13491Df2e8B3E213eD2412",  # Katana
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
    max_in: int
    max_out: int
    admin: str | None  # the address that can configure these flow caps


@dataclass(slots=True)
class VaultAllocation:
    """A MetaMorpho vault's exposure to a single Morpho Blue market."""

    vault_address: str
    vault_name: str
    vault_symbol: str
    asset_symbol: str
    asset_decimals: int
    total_assets: int
    supply_assets: int  # currently allocated to *this* market
    supply_cap: int  # cap configured by curator for *this* market
    allocators: list[str]  # addresses with ALLOCATOR_ROLE on the vault
    flow_cap: VaultFlowCap | None  # None if PublicAllocator not configured


@dataclass(slots=True)
class MorphoApiMarket:
    """Snapshot of a Morpho Blue market plus the vaults supplying to it."""

    market_id: str
    lltv: int
    loan_token: str
    loan_symbol: str
    loan_decimals: int
    collateral_token: str
    collateral_symbol: str
    collateral_decimals: int
    oracle: str
    irm: str
    supply_assets: int
    borrow_assets: int
    liquidity_assets: int
    utilization: float
    supply_apy: float
    borrow_apy: float
    fee_wad: int
    timestamp: int
    vaults: list[VaultAllocation]


class MorphoApiError(RuntimeError):
    """Raised when the Morpho API returns an error or is unreachable."""


def fetch_market(
    market_id: str, chain_id: int, *, timeout: float = 10.0
) -> MorphoApiMarket:
    """Fetch a Morpho Blue market with its supplying vaults from the public API.

    `market_id` is the 0x-prefixed market unique key. `chain_id` is the EVM chain
    ID (1 = mainnet, 8453 = base, ...). Raises `MorphoApiError` on network failure
    or if the market is not indexed.
    """
    if not market_id.startswith("0x"):
        market_id = "0x" + market_id
    payload = json.dumps(
        {
            "query": _MARKET_QUERY,
            "variables": {"marketId": market_id, "chainId": chain_id},
        }
    ).encode()
    req = Request(  # noqa: S310  # fixed https endpoint, not user input
        MORPHO_API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed URL
            body = json.loads(resp.read())
    except (URLError, TimeoutError) as exc:
        raise MorphoApiError(f"Morpho API unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MorphoApiError(f"Morpho API returned invalid JSON: {exc}") from exc

    if errors := body.get("errors"):
        raise MorphoApiError(f"Morpho API error: {errors}")
    market = (body.get("data") or {}).get("marketById")
    if not market:
        raise MorphoApiError(
            f"Market {market_id} not found on chain {chain_id} in Morpho index."
        )
    return _parse_market(market)


def _parse_market(raw: dict) -> MorphoApiMarket:
    state = raw.get("state") or {}
    return MorphoApiMarket(
        market_id=raw["marketId"],
        lltv=int(raw["lltv"]),
        loan_token=raw["loanAsset"]["address"],
        loan_symbol=raw["loanAsset"]["symbol"],
        loan_decimals=int(raw["loanAsset"]["decimals"]),
        collateral_token=raw["collateralAsset"]["address"],
        collateral_symbol=raw["collateralAsset"]["symbol"],
        collateral_decimals=int(raw["collateralAsset"]["decimals"]),
        oracle=raw["oracle"]["address"],
        irm=raw["irmAddress"],
        supply_assets=int(state.get("supplyAssets") or 0),
        borrow_assets=int(state.get("borrowAssets") or 0),
        liquidity_assets=int(state.get("liquidityAssets") or 0),
        utilization=float(state.get("utilization") or 0.0),
        supply_apy=float(state.get("supplyApy") or 0.0),
        borrow_apy=float(state.get("borrowApy") or 0.0),
        fee_wad=int(state.get("fee") or 0),
        timestamp=int(state.get("timestamp") or 0),
        vaults=[
            _parse_vault(v, raw["marketId"]) for v in raw.get("supplyingVaults") or []
        ],
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
    absolute_cap: int
    relative_cap_wad: int
    allocation: int
    market_id: str | None
    loan_token: str | None
    collateral_token: str | None
    oracle: str | None
    irm: str | None
    lltv: int | None

    @property
    def room(self) -> int:
        """Free space below the absolute cap (clamped at 0)."""
        return max(self.absolute_cap - self.allocation, 0)


@dataclass(slots=True)
class VaultV2Adapter:
    """An adapter contract attached to a Vault V2 (e.g. Morpho adapter)."""

    address: str
    adapter_type: str
    assets: int
    inner_vault: str | None  # for MorphoVaultV2Adapter: the underlying market vault


@dataclass(slots=True)
class VaultV2Info:
    """Snapshot of a Morpho Vault V2."""

    address: str
    name: str
    symbol: str
    asset_address: str
    asset_symbol: str
    asset_decimals: int
    total_assets: int
    idle_assets: int
    liquidity: int
    share_price: float
    max_apy: float
    performance_fee: float
    performance_fee_recipient: str
    management_fee: float
    management_fee_recipient: str
    owner: str
    curator: str
    allocators: list[str]
    sentinels: list[str]
    liquidity_adapter: VaultV2Adapter | None
    adapters: list[VaultV2Adapter]
    caps: list[VaultV2Cap] = field(default_factory=list)


@dataclass(slots=True)
class VaultV1MarketAllocation:
    """A MetaMorpho V1 allocation entry."""

    market_id: str
    lltv: int
    loan_symbol: str
    loan_decimals: int
    collateral_symbol: str
    market_supply_assets: int
    market_borrow_assets: int
    market_supply_apy: float
    supply_assets: int
    supply_cap: int

    @property
    def cap_room(self) -> int:
        return max(self.supply_cap - self.supply_assets, 0)


@dataclass(slots=True)
class VaultV1Info:
    """Snapshot of a MetaMorpho V1 vault."""

    address: str
    name: str
    symbol: str
    asset_address: str
    asset_symbol: str
    asset_decimals: int
    total_assets: int
    fee_wad: int
    owner: str
    curator: str
    guardian: str
    fee_recipient: str
    allocators: list[str]
    allocations: list[VaultV1MarketAllocation]
    public_allocator: VaultFlowCap | None  # vault-level (no flowCap filtering)
    public_allocator_flow_caps: dict[str, tuple[int, int]]  # marketId -> (maxIn,maxOut)


def fetch_vault(
    address: str, chain_id: int, *, timeout: float = 10.0
) -> VaultV2Info | VaultV1Info:
    """Fetch a Morpho vault by address. Tries V2 first, falls back to V1.

    Raises `MorphoApiError` if the address is not a Morpho vault on this chain.
    """
    address = Web3.to_checksum_address(address)
    v2_body = _post_query(
        _VAULT_V2_QUERY, {"address": address, "chainId": chain_id}, timeout
    )
    v2 = (v2_body.get("data") or {}).get("vaultV2ByAddress") if v2_body else None
    if v2:
        return _parse_vault_v2(v2)
    v1_body = _post_query(
        _VAULT_V1_QUERY, {"address": address, "chainId": chain_id}, timeout
    )
    v1 = (v1_body.get("data") or {}).get("vaultByAddress") if v1_body else None
    if v1:
        return _parse_vault_v1(v1)
    raise MorphoApiError(
        f"Vault {address} not found on chain {chain_id} (neither V1 nor V2)."
    )


def _post_query(query: str, variables: dict, timeout: float) -> dict:
    """POST a GraphQL query and return the parsed body. Tolerates NOT_FOUND."""
    payload = json.dumps({"query": query, "variables": variables}).encode()
    req = Request(  # noqa: S310  # fixed https endpoint, not user input
        MORPHO_API_URL,
        data=payload,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urlopen(req, timeout=timeout) as resp:  # noqa: S310 — fixed URL
            body = json.loads(resp.read())
    except (URLError, TimeoutError) as exc:
        raise MorphoApiError(f"Morpho API unreachable: {exc}") from exc
    except json.JSONDecodeError as exc:
        raise MorphoApiError(f"Morpho API returned invalid JSON: {exc}") from exc
    # NOT_FOUND errors come back with errors[].status == "NOT_FOUND" and data: null.
    # We swallow those (caller decides) and re-raise other GraphQL errors.
    if errors := body.get("errors"):
        statuses = {e.get("status") for e in errors}
        if statuses - {"NOT_FOUND"}:
            raise MorphoApiError(f"Morpho API error: {errors}")
    return body


def _parse_vault_v2(raw: dict) -> VaultV2Info:
    asset = raw.get("asset") or {}
    liq_adapter = raw.get("liquidityAdapter") or {}
    adapters = [
        _parse_v2_adapter(a) for a in (raw.get("adapters") or {}).get("items", []) if a
    ]
    caps = [_parse_v2_cap(c) for c in (raw.get("caps") or {}).get("items", []) if c]
    return VaultV2Info(
        address=raw["address"],
        name=raw.get("name") or "",
        symbol=raw.get("symbol") or "",
        asset_address=asset.get("address") or "",
        asset_symbol=asset.get("symbol") or "",
        asset_decimals=int(asset.get("decimals") or 0),
        total_assets=int(raw.get("totalAssets") or 0),
        idle_assets=int(raw.get("idleAssets") or 0),
        liquidity=int(raw.get("liquidity") or 0),
        share_price=float(raw.get("sharePrice") or 0.0),
        max_apy=float(raw.get("maxApy") or 0.0),
        performance_fee=float(raw.get("performanceFee") or 0.0),
        performance_fee_recipient=raw.get("performanceFeeRecipient") or "",
        management_fee=float(raw.get("managementFee") or 0.0),
        management_fee_recipient=raw.get("managementFeeRecipient") or "",
        owner=(raw.get("owner") or {}).get("address") or "",
        curator=(raw.get("curator") or {}).get("address") or "",
        allocators=[
            a["allocator"]["address"]
            for a in raw.get("allocators") or []
            if (a.get("allocator") or {}).get("address")
        ],
        sentinels=[
            s["sentinel"]["address"]
            for s in raw.get("sentinels") or []
            if (s.get("sentinel") or {}).get("address")
        ],
        liquidity_adapter=_parse_v2_adapter(liq_adapter) if liq_adapter else None,
        adapters=adapters,
        caps=caps,
    )


def _parse_v2_adapter(raw: dict) -> VaultV2Adapter:
    return VaultV2Adapter(
        address=raw.get("address") or "",
        adapter_type=raw.get("type") or raw.get("__typename") or "",
        assets=int(raw.get("assets") or 0),
        inner_vault=(raw.get("metaMorpho") or {}).get("address"),
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
        absolute_cap=int(raw.get("absoluteCap") or 0),
        relative_cap_wad=int(raw.get("relativeCap") or 0),
        allocation=int(raw.get("allocation") or 0),
        market_id=market_id,
        loan_token=loan,
        collateral_token=collat,
        oracle=oracle,
        irm=irm,
        lltv=lltv,
    )


def _decode_market_v1_id_data(
    id_data: str,
) -> tuple[str, str, str, str, int] | None:
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


def _morpho_blue_market_id(
    loan: str, collateral: str, oracle: str, irm: str, lltv: int
) -> str:
    """Compute a Morpho Blue market unique key from MarketParams.

    `marketId = keccak256(abi.encode(loanToken, collateralToken, oracle, irm, lltv))`
    — used by Morpho Blue's `idToMarketParams` mapping.
    """
    encoded = encode(
        ["address", "address", "address", "address", "uint256"],
        [loan, collateral, oracle, irm, lltv],
    )
    return "0x" + keccak(encoded).hex()


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
                market_id=market.get("marketId") or "",
                lltv=int(market.get("lltv") or 0),
                loan_symbol=loan_asset.get("symbol") or "",
                loan_decimals=int(loan_asset.get("decimals") or 0),
                collateral_symbol=collat_asset.get("symbol") or "",
                market_supply_assets=int(m_state.get("supplyAssets") or 0),
                market_borrow_assets=int(m_state.get("borrowAssets") or 0),
                market_supply_apy=float(m_state.get("supplyApy") or 0.0),
                supply_assets=int(entry.get("supplyAssets") or 0),
                supply_cap=int(entry.get("supplyCap") or 0),
            )
        )

    pac = raw.get("publicAllocatorConfig")
    flow_cap_summary: VaultFlowCap | None = None
    flow_caps: dict[str, tuple[int, int]] = {}
    if pac:
        flow_cap_summary = VaultFlowCap(
            fee_wei=int(pac.get("fee") or 0),
            max_in=0,
            max_out=0,
            admin=pac.get("admin"),
        )
        for fc in pac.get("flowCaps") or []:
            mid = ((fc.get("market") or {}).get("marketId") or "").lower()
            if mid:
                flow_caps[mid] = (int(fc.get("maxIn") or 0), int(fc.get("maxOut") or 0))

    return VaultV1Info(
        address=raw["address"],
        name=raw.get("name") or "",
        symbol=raw.get("symbol") or "",
        asset_address=asset.get("address") or "",
        asset_symbol=asset.get("symbol") or "",
        asset_decimals=int(asset.get("decimals") or 0),
        total_assets=int(state.get("totalAssets") or 0),
        fee_wad=int(state.get("fee") or 0),
        owner=state.get("owner") or "",
        curator=state.get("curator") or "",
        guardian=state.get("guardian") or "",
        fee_recipient=state.get("feeRecipient") or "",
        allocators=[
            a["address"] for a in raw.get("allocators") or [] if a.get("address")
        ],
        allocations=allocations,
        public_allocator=flow_cap_summary,
        public_allocator_flow_caps=flow_caps,
    )


def _parse_vault(raw: dict, market_id: str) -> VaultAllocation:
    asset = raw.get("asset") or {}
    state = raw.get("state") or {}
    allocation_for_market: dict = next(
        (
            a
            for a in (state.get("allocation") or [])
            if (a.get("market") or {}).get("marketId", "").lower() == market_id.lower()
        ),
        {},
    )
    pac = raw.get("publicAllocatorConfig")
    flow_cap: VaultFlowCap | None = None
    if pac:
        flow_for_market = next(
            (
                fc
                for fc in (pac.get("flowCaps") or [])
                if (fc.get("market") or {}).get("marketId", "").lower()
                == market_id.lower()
            ),
            None,
        )
        if flow_for_market:
            flow_cap = VaultFlowCap(
                fee_wei=int(pac.get("fee") or 0),
                max_in=int(flow_for_market.get("maxIn") or 0),
                max_out=int(flow_for_market.get("maxOut") or 0),
                admin=pac.get("admin"),
            )
    allocators = [
        a["address"] for a in (raw.get("allocators") or []) if a.get("address")
    ]
    return VaultAllocation(
        vault_address=raw["address"],
        vault_name=raw.get("name") or "",
        vault_symbol=raw.get("symbol") or "",
        asset_symbol=asset.get("symbol") or "",
        asset_decimals=int(asset.get("decimals") or 0),
        total_assets=int(state.get("totalAssets") or 0),
        supply_assets=int(allocation_for_market.get("supplyAssets") or 0),
        supply_cap=int(allocation_for_market.get("supplyCap") or 0),
        allocators=allocators,
        flow_cap=flow_cap,
    )

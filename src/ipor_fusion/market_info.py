"""The ``fusion market`` JSON documents: `build_morpho_blue_market_json` for a
Morpho Blue market and `build_meta_morpho_json` for a MetaMorpho vault.

Integers beyond a float's precision (amounts, caps, wad values) are decimal
strings."""

# No ``from __future__ import annotations``: a TypedDict records its
# ``NotRequired`` keys in ``__optional_keys__`` only from evaluated annotations.

from typing import Literal, NotRequired, TypedDict

from eth_typing import ChecksumAddress
from web3.types import Timestamp

from ipor_fusion.morpho_api import (
    MorphoApiMarket,
    VaultAllocation,
    VaultV1Info,
    VaultV2Adapter,
    VaultV2Cap,
    VaultV2Info,
)
from ipor_fusion.readers.morpho import (
    MorphoMarket,
    MorphoMarketParams,
    MorphoMarketRates,
)
from ipor_fusion.types import ChainId, Decimals, MorphoBlueMarketId


class MorphoMarketParamsJson(TypedDict):
    loan_token: ChecksumAddress
    collateral_token: ChecksumAddress
    oracle: ChecksumAddress
    irm: ChecksumAddress
    lltv: str


class MorphoMarketStateJson(TypedDict):
    total_supply_assets: str
    total_supply_shares: str
    total_borrow_assets: str
    total_borrow_shares: str
    liquidity_assets: str
    fee_wad: str
    last_update: Timestamp


class MorphoMarketRatesJson(TypedDict):
    rate_per_second_wad: str
    utilization: float
    borrow_apy: float
    supply_apy: float


class MorphoAssetJson(TypedDict):
    address: ChecksumAddress
    symbol: str
    decimals: Decimals


class MorphoVaultAssetJson(TypedDict):
    symbol: str
    decimals: Decimals


class MorphoFlowCapConfigJson(TypedDict):
    """A supplying vault's PublicAllocator flow caps for this market."""

    fee_wei: str
    max_in: str
    max_out: str
    admin: ChecksumAddress | None


class MorphoSupplyingVaultJson(TypedDict):
    address: ChecksumAddress
    name: str
    symbol: str
    asset: MorphoVaultAssetJson
    total_assets: str
    supply_assets: str
    supply_cap: str
    allocators: list[ChecksumAddress]
    public_allocator_config: MorphoFlowCapConfigJson | None


class MorphoBlueMarketJson(TypedDict):
    """The ``fusion market morpho-blue --json`` document; the Morpho API keys
    are present only when that lookup ran (``api_error`` when it failed)."""

    market_id: MorphoBlueMarketId
    chain_id: ChainId
    public_allocator: ChecksumAddress | None
    market_params: MorphoMarketParamsJson
    state: MorphoMarketStateJson
    rates: MorphoMarketRatesJson
    api_error: NotRequired[str]
    loan_asset: NotRequired[MorphoAssetJson]
    collateral_asset: NotRequired[MorphoAssetJson]
    vaults: NotRequired[list[MorphoSupplyingVaultJson]]


class MetaMorphoAdapterJson(TypedDict):
    address: ChecksumAddress
    type: str
    assets: str
    inner_vault: ChecksumAddress | None


class MetaMorphoCapJson(TypedDict):
    """A Vault V2 cap; the market fields are null unless ``type`` is
    ``MarketV1``."""

    cap_id: str
    type: str
    id_data: str
    absolute_cap: str
    relative_cap_wad: str
    allocation: str
    room: str
    market_id: MorphoBlueMarketId | None
    loan_token: ChecksumAddress | None
    collateral_token: ChecksumAddress | None
    oracle: ChecksumAddress | None
    irm: ChecksumAddress | None
    lltv: str | None


class MetaMorphoV2Json(TypedDict):
    """The ``fusion market meta-morpho --json`` document of a Morpho Vault V2."""

    version: Literal["v2"]
    chain_id: ChainId
    address: ChecksumAddress
    name: str
    symbol: str
    asset: MorphoAssetJson
    total_assets: str
    idle_assets: str
    liquidity: str
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
    liquidity_adapter: MetaMorphoAdapterJson | None
    adapters: list[MetaMorphoAdapterJson]
    caps: list[MetaMorphoCapJson]


class MetaMorphoFlowCapJson(TypedDict):
    max_in: str
    max_out: str


class MetaMorphoPublicAllocatorJson(TypedDict):
    fee_wei: str
    admin: ChecksumAddress | None
    flow_caps: dict[MorphoBlueMarketId, MetaMorphoFlowCapJson]


class MetaMorphoAllocationJson(TypedDict):
    market_id: MorphoBlueMarketId
    lltv: str
    loan_symbol: str
    loan_decimals: Decimals
    collateral_symbol: str
    supply_assets: str
    supply_cap: str
    cap_room: str
    market_supply_apy: float


class MetaMorphoV1Json(TypedDict):
    """The ``fusion market meta-morpho --json`` document of a MetaMorpho V1
    vault."""

    version: Literal["v1"]
    chain_id: ChainId
    address: ChecksumAddress
    name: str
    symbol: str
    asset: MorphoAssetJson
    total_assets: str
    fee_wad: str
    owner: ChecksumAddress
    curator: ChecksumAddress | None
    guardian: ChecksumAddress | None
    fee_recipient: ChecksumAddress | None
    allocators: list[ChecksumAddress]
    public_allocator: MetaMorphoPublicAllocatorJson | None
    allocations: list[MetaMorphoAllocationJson]


def build_morpho_blue_market_json(
    *,
    market_id: MorphoBlueMarketId,
    chain_id: ChainId,
    params: MorphoMarketParams,
    state: MorphoMarket,
    rates: MorphoMarketRates,
    api_market: MorphoApiMarket | None = None,
    api_error: str | None = None,
    public_allocator: ChecksumAddress | None = None,
) -> MorphoBlueMarketJson:
    """The ``fusion market morpho-blue --json`` document: a Morpho Blue
    market's parameters, state and rates (from `MorphoReader`), plus the
    MetaMorpho vaults supplying it when ``api_market`` (`fetch_market`) is
    given, or ``api_error`` when that lookup failed. ``market_id`` is the
    canonical id (`parse_morpho_market_id`) and is written as is."""
    out: MorphoBlueMarketJson = {
        "market_id": market_id,
        "chain_id": chain_id,
        "public_allocator": public_allocator,
        "market_params": {
            "loan_token": params.loan_token,
            "collateral_token": params.collateral_token,
            "oracle": params.oracle,
            "irm": params.irm,
            "lltv": str(params.lltv),
        },
        "state": {
            "total_supply_assets": str(state.total_supply_assets),
            "total_supply_shares": str(state.total_supply_shares),
            "total_borrow_assets": str(state.total_borrow_assets),
            "total_borrow_shares": str(state.total_borrow_shares),
            "liquidity_assets": str(
                state.total_supply_assets - state.total_borrow_assets
            ),
            "fee_wad": str(state.fee),
            "last_update": state.last_update,
        },
        "rates": {
            "rate_per_second_wad": str(rates.rate_per_second_wad),
            "utilization": rates.utilization,
            "borrow_apy": rates.borrow_apy,
            "supply_apy": rates.supply_apy,
        },
    }
    if api_error:
        out["api_error"] = api_error
    if api_market is not None:
        out["loan_asset"] = {
            "address": api_market.loan_token,
            "symbol": api_market.loan_symbol,
            "decimals": api_market.loan_decimals,
        }
        out["collateral_asset"] = {
            "address": api_market.collateral_token,
            "symbol": api_market.collateral_symbol,
            "decimals": api_market.collateral_decimals,
        }
        out["vaults"] = [_vault_to_json(v) for v in api_market.vaults]
    return out


def _vault_to_json(vault: VaultAllocation) -> MorphoSupplyingVaultJson:
    flow_cap = vault.flow_cap
    return {
        "address": vault.vault_address,
        "name": vault.vault_name,
        "symbol": vault.vault_symbol,
        "asset": {"symbol": vault.asset_symbol, "decimals": vault.asset_decimals},
        "total_assets": str(vault.total_assets),
        "supply_assets": str(vault.supply_assets),
        "supply_cap": str(vault.supply_cap),
        "allocators": list(vault.allocators),
        "public_allocator_config": None
        if flow_cap is None
        else {
            "fee_wei": str(flow_cap.fee_wei),
            "max_in": str(flow_cap.max_in),
            "max_out": str(flow_cap.max_out),
            "admin": flow_cap.admin,
        },
    }


def build_meta_morpho_json(
    info: VaultV1Info | VaultV2Info, chain_id: ChainId
) -> MetaMorphoV1Json | MetaMorphoV2Json:
    """The ``fusion market meta-morpho --json`` document for a MetaMorpho V1
    or Morpho Vault V2 (`fetch_vault`), told apart by ``version``."""
    if isinstance(info, VaultV2Info):
        return _meta_morpho_v2_json(info, chain_id)
    return {
        "version": "v1",
        "chain_id": chain_id,
        "address": info.address,
        "name": info.name,
        "symbol": info.symbol,
        "asset": {
            "address": info.asset_address,
            "symbol": info.asset_symbol,
            "decimals": info.asset_decimals,
        },
        "total_assets": str(info.total_assets),
        "fee_wad": str(info.fee_wad),
        "owner": info.owner,
        "curator": info.curator,
        "guardian": info.guardian,
        "fee_recipient": info.fee_recipient,
        "allocators": list(info.allocators),
        "public_allocator": (
            {
                "fee_wei": str(info.public_allocator.fee_wei),
                "admin": info.public_allocator.admin,
                "flow_caps": {
                    mid: {"max_in": str(mi), "max_out": str(mo)}
                    for mid, (mi, mo) in info.public_allocator_flow_caps.items()
                },
            }
            if info.public_allocator
            else None
        ),
        "allocations": [
            {
                "market_id": a.market_id,
                "lltv": str(a.lltv),
                "loan_symbol": a.loan_symbol,
                "loan_decimals": a.loan_decimals,
                "collateral_symbol": a.collateral_symbol,
                "supply_assets": str(a.supply_assets),
                "supply_cap": str(a.supply_cap),
                "cap_room": str(a.cap_room),
                "market_supply_apy": a.market_supply_apy,
            }
            for a in info.allocations
        ],
    }


def _meta_morpho_v2_json(info: VaultV2Info, chain_id: ChainId) -> MetaMorphoV2Json:
    return {
        "version": "v2",
        "chain_id": chain_id,
        "address": info.address,
        "name": info.name,
        "symbol": info.symbol,
        "asset": {
            "address": info.asset_address,
            "symbol": info.asset_symbol,
            "decimals": info.asset_decimals,
        },
        "total_assets": str(info.total_assets),
        "idle_assets": str(info.idle_assets),
        "liquidity": str(info.liquidity),
        "share_price": info.share_price,
        "max_apy": info.max_apy,
        "performance_fee": info.performance_fee,
        "performance_fee_recipient": info.performance_fee_recipient,
        "management_fee": info.management_fee,
        "management_fee_recipient": info.management_fee_recipient,
        "owner": info.owner,
        "curator": info.curator,
        "allocators": list(info.allocators),
        "sentinels": list(info.sentinels),
        "liquidity_adapter": (
            _v2_adapter_json(info.liquidity_adapter) if info.liquidity_adapter else None
        ),
        "adapters": [_v2_adapter_json(a) for a in info.adapters],
        "caps": [_v2_cap_json(c) for c in info.caps],
    }


def _v2_adapter_json(adapter: VaultV2Adapter) -> MetaMorphoAdapterJson:
    return {
        "address": adapter.address,
        "type": adapter.adapter_type,
        "assets": str(adapter.assets),
        "inner_vault": adapter.inner_vault,
    }


def _v2_cap_json(cap: VaultV2Cap) -> MetaMorphoCapJson:
    return {
        "cap_id": cap.cap_id,
        "type": cap.cap_type,
        "id_data": cap.id_data,
        "absolute_cap": str(cap.absolute_cap),
        "relative_cap_wad": str(cap.relative_cap_wad),
        "allocation": str(cap.allocation),
        "room": str(cap.room),
        "market_id": cap.market_id,
        "loan_token": cap.loan_token,
        "collateral_token": cap.collateral_token,
        "oracle": cap.oracle,
        "irm": cap.irm,
        "lltv": str(cap.lltv) if cap.lltv is not None else None,
    }

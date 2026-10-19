"""The ``fusion market`` JSON documents: `build_morpho_blue_market_json` for a
Morpho Blue market and `build_meta_morpho_json` for a MetaMorpho vault."""

from __future__ import annotations

from typing import Any

from ipor_fusion.morpho_api import (
    MorphoApiMarket,
    VaultAllocation,
    VaultV1Info,
    VaultV2Cap,
    VaultV2Info,
)
from ipor_fusion.readers.morpho import (
    MorphoMarket,
    MorphoMarketParams,
    MorphoMarketRates,
)
from ipor_fusion.types import ChainId


def build_morpho_blue_market_json(
    market_id: str,
    chain_id: ChainId,
    params: MorphoMarketParams,
    state: MorphoMarket,
    rates: MorphoMarketRates,
    api_market: MorphoApiMarket | None,
    api_error: str | None,
    public_allocator: str | None,
) -> dict[str, Any]:
    """The ``fusion market morpho-blue --json`` document: a Morpho Blue
    market's parameters, state and rates (from `MorphoReader`), plus the
    MetaMorpho vaults supplying it when ``api_market`` (`fetch_market`) is
    given, or ``api_error`` when that lookup failed. ``market_id`` is the
    64-hex-char id without ``0x``."""
    out: dict = {
        "market_id": "0x" + market_id,
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


def _vault_to_json(vault: VaultAllocation) -> dict:
    entry: dict = {
        "address": vault.vault_address,
        "name": vault.vault_name,
        "symbol": vault.vault_symbol,
        "asset": {"symbol": vault.asset_symbol, "decimals": vault.asset_decimals},
        "total_assets": str(vault.total_assets),
        "supply_assets": str(vault.supply_assets),
        "supply_cap": str(vault.supply_cap),
        "allocators": list(vault.allocators),
    }
    if vault.flow_cap is not None:
        entry["public_allocator_config"] = {
            "fee_wei": str(vault.flow_cap.fee_wei),
            "max_in": str(vault.flow_cap.max_in),
            "max_out": str(vault.flow_cap.max_out),
            "admin": vault.flow_cap.admin,
        }
    else:
        entry["public_allocator_config"] = None
    return entry


def build_meta_morpho_json(
    info: VaultV1Info | VaultV2Info, chain_id: ChainId
) -> dict[str, Any]:
    """The ``fusion market meta-morpho --json`` document for a MetaMorpho V1
    or Morpho Vault V2 (`fetch_vault`)."""
    if isinstance(info, VaultV2Info):
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
                {
                    "address": info.liquidity_adapter.address,
                    "type": info.liquidity_adapter.adapter_type,
                    "assets": str(info.liquidity_adapter.assets),
                    "inner_vault": info.liquidity_adapter.inner_vault,
                }
                if info.liquidity_adapter
                else None
            ),
            "adapters": [
                {
                    "address": a.address,
                    "type": a.adapter_type,
                    "assets": str(a.assets),
                    "inner_vault": a.inner_vault,
                }
                for a in info.adapters
            ],
            "caps": [_v2_cap_json(c) for c in info.caps],
        }
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


def _v2_cap_json(cap: VaultV2Cap) -> dict:
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

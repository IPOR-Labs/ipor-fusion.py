"""MCP server exposing IPOR Fusion CLI as tools for Claude Code.

Calls the ipor_fusion SDK directly — no CLI subprocess.
Configuration is loaded from the shared CLI config (~/.config/ipor-fusion/).
"""

from __future__ import annotations

import json

from mcp.server.fastmcp import FastMCP
from web3 import Web3

from ipor_fusion.cli.config_store import (
    FusionConfig,
    VaultEntry,
    load_config,
    save_config,
)
from ipor_fusion.cli.vault_cmd import (
    CHAIN_NAMES,
    _build_json_output,
)
from ipor_fusion.cli.vault_fetcher import (
    _fetch_deployment_info,
    _fetch_vault_data,
)
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.plasma_vault import PlasmaVault

mcp = FastMCP("ipor-fusion")


# ---------------------------------------------------------------------------
# Resolution helpers
# ---------------------------------------------------------------------------


def _resolve_provider(cfg: FusionConfig, chain_id: int) -> str:
    if provider_url := cfg.providers.get(str(chain_id)):
        return provider_url
    raise ValueError(
        f"No provider for chain {chain_id}. "
        f"Use config_set_provider to configure one."
    )


def _resolve_chain_id(cfg: FusionConfig, vault_address: str, chain_id: int) -> int:
    if chain_id:
        return chain_id
    entry = next(
        (v for v in cfg.vaults if v.address.lower() == vault_address.lower()),
        None,
    )
    if entry:
        return entry.chain_id
    if len(cfg.providers) == 1:
        return int(next(iter(cfg.providers)))
    raise ValueError(
        "Cannot auto-detect chain ID. Use chain_id parameter or save the vault first."
    )


def _build_ctx(
    cfg: FusionConfig, chain_id: int, block_number: int = 0
) -> tuple[Web3Context, int | None]:
    provider_url = _resolve_provider(cfg, chain_id)
    ctx = Web3Context.from_url(provider_url)
    effective_block = block_number if block_number else None
    if effective_block is not None:
        ctx.default_block = effective_block
    return ctx, effective_block


# ---------------------------------------------------------------------------
# Vault tools
# ---------------------------------------------------------------------------


@mcp.tool()
def vault_info(
    vault_address: str,
    chain_id: int = 0,
    block_number: int = 0,
) -> str:
    """Get full on-chain state of a Plasma Vault (JSON).

    Returned JSON fields:
    - vault, chain, chain_id, block, block_timestamp, block_timestamp_utc
    - links (ipor_app URL, etherscan URL)
    - deployment (deployer, deploy_block, deploy_timestamp, vault_age)
    - asset (address, symbol, decimals, price_usd)
    - share_decimals
    - total_assets (raw, formatted, usd)
    - total_supply (raw, formatted)
    - supply_cap (raw, formatted)
    - managers (access, price_oracle, rewards, withdraw)
    - withdraw_manager_details — null when no withdraw manager:
      withdraw_window_seconds, request_fee_wad/percent, withdraw_fee_wad/percent,
      shares_to_release (raw/formatted), last_release_funds_timestamp/utc,
      total_pending_shares (raw/formatted),
      pending_requests[] (account, shares, assets with usd, end_withdraw_window, remaining_seconds, can_withdraw)
    - fuses (address, contract name)
    - instant_withdrawal_fuses (address, contract name)
    - balance_fuses per market (market name, balance raw/formatted, fuse address/contract)
    - substrates per market (address, symbol, contract, substrate_type)
    - erc20_balances (address, symbol, decimals, balance, price_usd, usd_value)
    - reconciliation (balance_fuses_total, underlying_on_vault, erc20_direct_total, sum, on_chain_total_assets, delta)
    - lending_health — null when no lending positions:
      markets[] (protocol, market_id, market_name, current_ltv, max_ltv, health_factor,
      total_collateral_usd, total_debt_usd, ltv_usage_percent, is_warning, is_critical),
      worst_ltv_usage_percent
    - health_check (ok, warnings)

    Args:
        vault_address: Vault address (required).
        chain_id: Chain ID (auto-detected if 0).
        block_number: Block number (latest if 0).
    """
    cfg = load_config()
    chain_id = _resolve_chain_id(cfg, vault_address, chain_id)
    ctx, effective_block = _build_ctx(cfg, chain_id, block_number)
    checksum = Web3.to_checksum_address(vault_address)

    code = ctx.web3.eth.get_code(checksum)
    if code in {b"", b"\x00"}:
        raise ValueError(f"No contract found at {checksum} on chain {chain_id}.")

    plasma_vault = PlasmaVault(ctx, checksum)

    try:
        data = _fetch_vault_data(ctx, plasma_vault, effective_block, chain_id=chain_id)
    except Exception as exc:
        if "Tried to read" in str(exc) and "only got 0 bytes" in str(exc):
            raise ValueError(
                f"Address {checksum} on chain {chain_id} does not appear to be "
                f"a Plasma Vault."
            ) from exc
        raise

    api_key = cfg.etherscan_api_key
    chain_label = CHAIN_NAMES.get(chain_id, str(chain_id))
    data.deployment_block, data.deployment_timestamp = _fetch_deployment_info(
        ctx, chain_id, vault_address, api_key
    )

    result = _build_json_output(
        ctx, plasma_vault, data, vault_address, chain_id, chain_label, api_key
    )
    return json.dumps(result, indent=2)


@mcp.tool()
def vault_list() -> str:
    """List all saved Plasma Vaults with their chain, label, and address."""
    cfg = load_config()
    entries = [
        {
            "address": v.address,
            "label": v.label,
            "chain": CHAIN_NAMES.get(v.chain_id, str(v.chain_id)),
            "chain_id": v.chain_id,
        }
        for v in cfg.vaults
    ]
    return json.dumps(entries, indent=2)


@mcp.tool()
def vault_add(
    address: str,
    label: str = "",
    chain_id: int = 0,
) -> str:
    """Save a Plasma Vault to the local config.

    Args:
        address: Vault address (required).
        label: Human-readable label (fetched from on-chain name() if empty).
        chain_id: Chain ID (auto-detected when only one provider configured).
    """
    cfg = load_config()

    if not chain_id:
        if len(cfg.providers) == 1:
            chain_id = int(next(iter(cfg.providers)))
        else:
            raise ValueError(
                "Cannot auto-detect chain ID — multiple providers configured. "
                "Provide chain_id."
            )

    if not label:
        provider_url = _resolve_provider(cfg, chain_id)
        ctx = Web3Context.from_url(provider_url)
        checksum = Web3.to_checksum_address(address)
        try:
            label = PlasmaVault(ctx, checksum).name()
        except Exception:  # pylint: disable=broad-except
            label = checksum

    for vault_entry in cfg.vaults:
        if vault_entry.address.lower() == address.lower():
            vault_entry.label = label
            vault_entry.chain_id = chain_id
            save_config(cfg)
            return f"Vault {label} ({address}) updated."

    cfg.vaults.append(VaultEntry(address=address, label=label, chain_id=chain_id))
    save_config(cfg)
    return f"Vault {label} ({address}) added."


@mcp.tool()
def vault_remove(address: str) -> str:
    """Remove a Plasma Vault from the local config.

    Args:
        address: Vault address to remove.
    """
    cfg = load_config()
    before = len(cfg.vaults)
    cfg.vaults = [v for v in cfg.vaults if v.address.lower() != address.lower()]
    if len(cfg.vaults) == before:
        return "Vault not found."
    save_config(cfg)
    return "Vault removed."


# ---------------------------------------------------------------------------
# Config tools
# ---------------------------------------------------------------------------


@mcp.tool()
def config_show() -> str:
    """Show current fusion CLI configuration.

    Displays configured RPC providers, saved vaults,
    and Etherscan API key status.
    """
    cfg = load_config()
    result = {
        "providers": dict(cfg.providers),
        "vaults": [
            {
                "chain_id": v.chain_id,
                "chain": CHAIN_NAMES.get(v.chain_id, str(v.chain_id)),
                "label": v.label,
                "address": v.address,
            }
            for v in cfg.vaults
        ],
        "etherscan_api_key": "***" if cfg.etherscan_api_key else None,
    }
    return json.dumps(result, indent=2)


@mcp.tool()
def config_set_provider(url: str, chain_id: int = 0) -> str:
    """Set RPC provider URL for a chain.

    Args:
        url: RPC provider URL.
        chain_id: Chain ID (auto-detected via eth_chainId if 0).
    """
    if not chain_id:
        web3 = Web3(Web3.HTTPProvider(url))
        chain_id = web3.eth.chain_id

    cfg = load_config()
    cfg.providers[str(chain_id)] = url
    save_config(cfg)
    return f"Provider for chain {chain_id} set."


@mcp.tool()
def config_set_etherscan_key(api_key: str) -> str:
    """Set Etherscan API key (works for all chains via Etherscan V2).

    Args:
        api_key: Etherscan API key.
    """
    cfg = load_config()
    cfg.etherscan_api_key = api_key
    save_config(cfg)
    return "Etherscan API key set."


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

"""MCP server exposing IPOR Fusion CLI as tools for Claude Code."""

import subprocess

from mcp.server.fastmcp import FastMCP

mcp = FastMCP("ipor-fusion")


def _run_fusion(*args: str) -> str:
    result = subprocess.run(
        ["fusion", *args],
        capture_output=True,
        text=True,
        timeout=120,
        check=False,
    )
    if result.returncode != 0:
        return f"Error: {result.stderr.strip() or result.stdout.strip()}"
    return result.stdout.strip()


@mcp.tool()
def vault_info(
    vault_address: str = "",
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
    - fuses (address, contract name)
    - instant_withdrawal_fuses (address, contract name)
    - balance_fuses per market (market name, balance raw/formatted, fuse address/contract)
    - substrates per market (address, symbol, contract, substrate_type)
    - erc20_balances (address, symbol, decimals, balance, price_usd, usd_value)
    - reconciliation (balance_fuses_total, underlying_on_vault, erc20_direct_total, sum, on_chain_total_assets, delta)
    - health_check (ok, warnings)

    Args:
        vault_address: Vault address (uses default if empty).
        chain_id: Chain ID (auto-detected if 0).
        block_number: Block number (latest if 0).
    """
    args = ["vault", "info", "--json"]
    if vault_address:
        args.extend(["--vault", vault_address])
    if chain_id:
        args.extend(["--chain-id", str(chain_id)])
    if block_number:
        args.extend(["--block-number", str(block_number)])
    return _run_fusion(*args)


@mcp.tool()
def vault_market_detail(
    vault_address: str = "",
    chain_id: int = 0,
    market_id: int = 0,
    block_number: int = 0,
) -> str:
    """Single-market deep-dive for a Plasma Vault (JSON).

    Lighter than vault_info — returns data for one market only.

    Returned JSON fields:
    - vault, chain_id, block, block_number
    - market (name), market_id
    - asset (address, symbol, decimals)
    - balance (raw, formatted, usd)
    - fuse (address, contract name)
    - substrates (address, symbol, contract, substrate_type)

    Args:
        vault_address: Vault address (uses default if empty).
        chain_id: Chain ID (auto-detected if 0).
        market_id: Market ID to inspect (required, use vault_info to find IDs).
        block_number: Block number (latest if 0).
    """
    args = ["vault", "market-detail", "--json", "--market-id", str(market_id)]
    if vault_address:
        args.extend(["--vault", vault_address])
    if chain_id:
        args.extend(["--chain-id", str(chain_id)])
    if block_number:
        args.extend(["--block-number", str(block_number)])
    return _run_fusion(*args)


@mcp.tool()
def vault_list() -> str:
    """List all saved Plasma Vaults with their chain, label, and address."""
    return _run_fusion("vault", "list", "--json")


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
    args = ["vault", "add", address]
    if label:
        args.extend(["--label", label])
    if chain_id:
        args.extend(["--chain-id", str(chain_id)])
    return _run_fusion(*args)


@mcp.tool()
def vault_remove(address: str) -> str:
    """Remove a Plasma Vault from the local config.

    Args:
        address: Vault address to remove.
    """
    return _run_fusion("vault", "remove", address)


@mcp.tool()
def config_show() -> str:
    """Show current fusion CLI configuration.

    Displays configured RPC providers, default vault, saved vaults,
    and Etherscan API key status.
    """
    return _run_fusion("config", "show")


@mcp.tool()
def config_set_provider(url: str, chain_id: int = 0) -> str:
    """Set RPC provider URL for a chain.

    Args:
        url: RPC provider URL.
        chain_id: Chain ID (auto-detected via eth_chainId if 0).
    """
    args = ["config", "set-provider", url]
    if chain_id:
        args.extend(["--chain-id", str(chain_id)])
    return _run_fusion(*args)


@mcp.tool()
def config_set_etherscan_key(api_key: str) -> str:
    """Set Etherscan API key (works for all chains via Etherscan V2).

    Args:
        api_key: Etherscan API key.
    """
    return _run_fusion("config", "set-etherscan-key", api_key)


@mcp.tool()
def config_set_default_vault(address: str) -> str:
    """Set the default vault address used when --vault is omitted.

    Args:
        address: Vault address to set as default.
    """
    return _run_fusion("config", "set-default-vault", address)


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

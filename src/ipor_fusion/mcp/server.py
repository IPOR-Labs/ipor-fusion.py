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
    """Get full on-chain state of a Plasma Vault.

    Returns vault asset info, total assets/supply, managers, fuses,
    balance fuses with cached balances, ERC20 holdings, substrates
    per market, balance reconciliation, and health check.

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
def vault_list() -> str:
    """List all saved Plasma Vaults with their chain, label, and address."""
    return _run_fusion("vault", "list", "--json")


@mcp.tool()
def config_show() -> str:
    """Show current fusion CLI configuration.

    Displays configured RPC providers, default vault, saved vaults,
    and Etherscan API key status.
    """
    return _run_fusion("config", "show")


def main() -> None:
    mcp.run(transport="stdio")


if __name__ == "__main__":
    main()

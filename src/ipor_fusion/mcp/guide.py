"""Guide resources and prompts for a FastMCP server.

The documents come from `ipor_fusion.guide`; this module only adapts them to
the MCP surface (resources under `fusion://`, prompts that clients expose as
slash commands). `register_guide` takes any `FastMCP` instance so every server
built on this SDK serves the same text.
"""

from __future__ import annotations

import inspect
from collections.abc import Callable
from typing import Annotated

from mcp.server.fastmcp import FastMCP
from pydantic import Field

from ipor_fusion.guide import RESOURCES, GuideResource

_SDK_URL = "https://github.com/IPOR-Labs/ipor-fusion.py"
_REGISTRY_URL = "https://github.com/IPOR-Labs/ipor-abi"

ChainIdArg = Annotated[str, Field(description="Chain id, e.g. 8453 for Base")]
VaultArg = Annotated[str, Field(description="Plasma Vault address (0x...)")]


def quickstart() -> str:
    """How to work with IPOR Fusion through this server: where to start, what is
    read-only, and which rules keep a transaction from reverting."""
    return f"""You are working with IPOR Fusion through this server. Before anything else:

1. Read the resource `fusion://glossary` unless you already know what a Plasma \
Vault, a fuse, a balance fuse, a market and a substrate are.
2. Read `fusion://invariants` before you write or review any code that deploys, \
configures or operates a vault. Each rule there names the revert you get when \
you break it.

What this server does: read-only inspection. Start by listing vaults \
(`vault_list` or `vaults_list`, whichever this server has), then \
`vault_info(chain_id, vault_address)` for the full state of one vault, \
`vault_oracle_mapping` for how it prices each asset, and `market_morpho_blue` / \
`market_meta_morpho` for Morpho markets. These tools need no key and change \
nothing on-chain.

What it does not do: deploy, configure, deposit or execute. Those go through \
the `ipor-fusion` Python SDK with a `Web3Context` that carries a private key \
(`pip install ipor-fusion`, {_SDK_URL}). Say so explicitly when a user asks \
this server to change something. To write that code, read `fusion://quickstart`: \
the executed walk from clone to the first execute, with the factory proxy for \
every chain. Keep its steps and their order; change only addresses and names.

Addresses are per chain. Resolve factory and fuse addresses by contract name in \
the ipor-abi registry ({_REGISTRY_URL}); never reuse an address from one chain \
on another."""


def analyze_vault(chain_id: ChainIdArg, vault_address: VaultArg) -> str:
    """Summarize one Plasma Vault — assets, markets, fuses, fees, managers, roles —
    and flag what an operator should look at."""
    return f"""Analyze the IPOR Fusion Plasma Vault {vault_address} on chain {chain_id}.

1. Call `vault_info` with chain_id={chain_id} and vault_address={vault_address}. \
If it reports that the address is not a Plasma Vault, stop and say so.
2. Summarize, in this order: underlying asset and total assets; share price and \
supply cap if reported; every market with its balance, its share of total \
assets, its fuses and its substrates; the fee configuration; the withdraw \
manager and rewards manager if configured; the role holders if the tool \
reports them.
3. Flag anything an operator should look at: a market with fuses but no balance \
fuse, a balance fuse reporting zero for a market with granted substrates, a \
lending position with a health factor or LTV close to liquidation, unvested \
rewards, a gap between the sum of market balances and total assets.
4. Use the APY and percentage fields verbatim; the unit is already applied.

Do not guess values the tool did not return."""


def trace_oracle_pricing(
    chain_id: ChainIdArg,
    vault_address: VaultArg,
    asset: Annotated[
        str, Field(description="Asset address to trace; empty for every asset")
    ] = "",
) -> str:
    """Explain how a vault prices one asset, or every asset, through its price
    oracle middleware."""
    scope = f"the asset {asset}" if asset else "every configured asset"
    asset_arg = f" and asset={asset}" if asset else ""
    return f"""Explain how the IPOR Fusion Plasma Vault {vault_address} on chain \
{chain_id} prices {scope}.

1. Call `vault_oracle_mapping` with chain_id={chain_id} and \
vault_address={vault_address}{asset_arg}.
2. For each asset, walk the chain from the vault's price oracle middleware to \
the leaf feed: which oracle contract, which feed, which quote currency, how \
many decimals, and the timestamp of the latest answer if reported.
3. Point out anything that affects safety: a fallback or hand-entered price, a \
stale timestamp, a feed in a different quote currency than the vault expects, \
an asset with no mapping at all.

Keep the answer to the mapping the tool returned; do not invent feeds."""


def explain_fuse(
    chain_id: ChainIdArg,
    fuse: Annotated[
        str, Field(description="Fuse contract name (e.g. SupplyFuseAaveV3) or address")
    ],
) -> str:
    """Explain what a fuse does, which market and substrates it needs, and which
    SDK class encodes its actions."""
    return f"""Explain the IPOR Fusion fuse `{fuse}` on chain {chain_id}.

1. Identify it. If `{fuse}` is a contract name, resolve it to an address in the \
ipor-abi registry ({_REGISTRY_URL}), or with an address-lookup tool if this \
server has one; if it is an address, find its name the same way. Say which \
market id it serves and whether it is an action fuse (moves funds) or a balance \
fuse (values the position).
2. Describe what `execute` does at the protocol level when it runs this fuse, \
which substrates the market needs granted for it to work, and which balance \
fuse must be registered on the same market.
3. Name the SDK class that encodes its actions if one exists. The registry name \
and the class name swap their parts: `SupplyFuseAaveV3` is `AaveV3SupplyFuse`.
4. State the rules from `fusion://invariants` that apply: fuses are immutable, \
the address is chain-specific, balance fuse before the first execute."""


def deploy_vault(
    chain_id: ChainIdArg,
    asset: Annotated[
        str, Field(description="Underlying asset, symbol or address, e.g. USDC")
    ],
    market: Annotated[
        str,
        Field(description="Market for the first strategy step, e.g. AAVE_V3 or MORPHO"),
    ] = "AAVE_V3",
) -> str:
    """Write the code that deploys, configures, funds and operates a new Plasma
    Vault on a chain, adapted from the executed quickstart walk."""
    return f"""Write the code that deploys and operates a new IPOR Fusion Plasma Vault on \
chain {chain_id} for the asset {asset}, with a first strategy step on the \
{market} market.

1. Read `fusion://invariants` and `fusion://quickstart`. The quickstart is \
executed code: keep every step and its order, change only addresses and names.
2. Resolve for chain {chain_id}, by contract name in the ipor-abi registry \
({_REGISTRY_URL}) or with an address-lookup tool if this server has one: \
`IporFusionFactoryProxy`, the action fuse and the balance fuse for {market}, \
and the token address of {asset}. Say which registry names you used; balance \
fuse names differ per chain. If a name does not resolve for chain {chain_id}, \
stop and say so instead of guessing.
3. Ask which access posture the user wants and default to the private one: \
whitelist the depositor with `WHITELIST_ROLE`, or `convert_to_public_vault()` \
only for a vault that takes outside money, because that switch is one-way.
4. Tell the user what the signer needs before running it: the private key in \
the `Web3Context`, gas on chain {chain_id}, and the deposit amount of {asset} \
in the depositor's wallet. Recommend a fork run first \
(`anvil --fork-url <RPC_URL> --chain-id {chain_id}`).

Do not invent addresses, and do not present a step as optional."""


PROMPTS: tuple[Callable[..., str], ...] = (
    quickstart,
    deploy_vault,
    analyze_vault,
    trace_oracle_pricing,
    explain_fuse,
)


def _reader(doc: GuideResource) -> Callable[[], str]:
    def read() -> str:
        return doc.text

    read.__name__ = doc.name
    read.__doc__ = doc.description
    return read


def register_guide(mcp: FastMCP) -> None:
    """Register the shipped guide documents as `fusion://` resources and the
    guide prompts on `mcp`. Call once per server instance."""
    for doc in RESOURCES:
        mcp.resource(
            doc.uri,
            name=doc.name,
            description=doc.description,
            mime_type=doc.mime_type,
        )(_reader(doc))
    for prompt in PROMPTS:
        # The docstring is wrapped for the source; clients show it on one line.
        description = " ".join((inspect.getdoc(prompt) or "").split())
        mcp.prompt(name=prompt.__name__, description=description)(prompt)


__all__ = ["PROMPTS", "register_guide"]

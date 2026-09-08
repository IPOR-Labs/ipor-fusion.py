<p align="center">
    <img height="80px" width="80px" src="https://ipor.io/images/ipor-fusion.svg" alt="IPOR Fusion Python SDK"/>
    <h1 align="center">IPOR Fusion Python SDK</h1>
</p>

`ipor_fusion` is the official Python SDK for **IPOR Fusion Plasma Vaults** — typed abstractions for DeFi protocol interactions on EVM chains through a fuse adapter pattern.

Maintained by <a href="https://ipor.io">IPOR Labs AG</a>.

[Documentation](https://docs.ipor.io/build-on-fusion) · [SDK docs](https://docs.ipor.io/build-on-fusion/alpha/sdk) · [llms.txt for AI agents](https://ipor.io/llms.txt) · [Hosted MCP server](https://mcp.ipor.io/mcp) · [Example bot](https://github.com/IPOR-Labs/ipor-fusion-alpha-example) · [Contracts](https://github.com/IPOR-Labs/ipor-fusion)

<table>
  <tr>
    <td><strong>Workflow</strong></td>
    <td>
        <a href="https://github.com/IPOR-Labs/ipor-fusion.py/actions/workflows/ci.yml">
            <img src="https://github.com/IPOR-Labs/ipor-fusion.py/actions/workflows/ci.yml/badge.svg" alt="CI">
        </a>
        <a href="https://github.com/IPOR-Labs/ipor-fusion.py/actions/workflows/cd.yml">
            <img src="https://github.com/IPOR-Labs/ipor-fusion.py/actions/workflows/cd.yml/badge.svg" alt="CD">
        </a>
        <a href="https://github.com/IPOR-Labs/ipor-fusion.py/actions/workflows/release.yml">
            <img src="https://github.com/IPOR-Labs/ipor-fusion.py/actions/workflows/release.yml/badge.svg"
alt="Release">
        </a>
    </td>
  </tr>
  <tr>
    <td><strong>Social</strong></td>
    <td>
        <a href="https://discord.com/invite/bSKzq6UMJ3">
            <img alt="Chat on Discord" src="https://img.shields.io/discord/832532271734587423?logo=discord&logoColor=white">
        </a>
        <a href="https://x.com/ipor_io">
            <img alt="X (formerly Twitter) URL" src="https://img.shields.io/twitter/url?url=https%3A%2F%2Fx.com%2Fipor_io&style=flat&logo=x&label=%40ipor_io&color=green">
        </a>
        <a href="https://t.me/IPOR_official_broadcast">
            <img alt="IPOR Official Broadcast" src="https://img.shields.io/badge/-t?logo=telegram&logoColor=white&logoSize=%3D&label=ipor">
        </a>
    </td>
  </tr>
  <tr>
    <td><strong>Code</strong></td>
    <td>
        <a href="https://pypi.org/project/ipor-fusion/">
            <img alt="PyPI version" src="https://img.shields.io/pypi/v/ipor-fusion?color=blue">
        </a>
        <a href="https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/LICENSE">
            <img alt="GitHub License" src="https://img.shields.io/github/license/IPOR-Labs/ipor-fusion?color=blue">
        </a>
        <a href="https://pypi.org/project/ipor-fusion/">
            <img alt="Python Version" src="https://img.shields.io/pypi/pyversions/ipor-fusion">
        </a>
        <a href="https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/pyproject.toml">
            <img alt="Linter: Ruff" src="https://img.shields.io/endpoint?url=https://raw.githubusercontent.com/astral-sh/ruff/main/assets/badge/v2.json">
        </a>
        <a href="https://deepwiki.com/IPOR-Labs/ipor-fusion.py">
            <img alt="DeepWiki" src="https://img.shields.io/badge/DeepWiki-IPOR--Labs%2Fipor--fusion.py-blue.svg?logo=data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAACwAAAAyCAYAAAAnWDnqAAAAAXNSR0IArs4c6QAAA05JREFUaEPtmUtyEzEQhtWTQyQLHNak2AB7ZnyXZMEjXMGeK/AIi+QuHrMnbChYY7MIh8g01fJoopFb0uhhEqqcbWTp06/uv1saEDv4O3n3dV60RfP947Mm9/SQc0ICFQgzfc4CYZoTPAswgSJCCUJUnAAoRHOAUOcATwbmVLWdGoH//PB8mnKqScAhsD0kYP3j/Yt5LPQe2KvcXmGvRHcDnpxfL2zOYJ1mFwrryWTz0advv1Ut4CJgf5uhDuDj5eUcAUoahrdY/56ebRWeraTjMt/00Sh3UDtjgHtQNHwcRGOC98BJEAEymycmYcWwOprTgcB6VZ5JK5TAJ+fXGLBm3FDAmn6oPPjR4rKCAoJCal2eAiQp2x0vxTPB3ALO2CRkwmDy5WohzBDwSEFKRwPbknEggCPB/imwrycgxX2NzoMCHhPkDwqYMr9tRcP5qNrMZHkVnOjRMWwLCcr8ohBVb1OMjxLwGCvjTikrsBOiA6fNyCrm8V1rP93iVPpwaE+gO0SsWmPiXB+jikdf6SizrT5qKasx5j8ABbHpFTx+vFXp9EnYQmLx02h1QTTrl6eDqxLnGjporxl3NL3agEvXdT0WmEost648sQOYAeJS9Q7bfUVoMGnjo4AZdUMQku50McDcMWcBPvr0SzbTAFDfvJqwLzgxwATnCgnp4wDl6Aa+Ax283gghmj+vj7feE2KBBRMW3FzOpLOADl0Isb5587h/U4gGvkt5v60Z1VLG8BhYjbzRwyQZemwAd6cCR5/XFWLYZRIMpX39AR0tjaGGiGzLVyhse5C9RKC6ai42ppWPKiBagOvaYk8lO7DajerabOZP46Lby5wKjw1HCRx7p9sVMOWGzb/vA1hwiWc6jm3MvQDTogQkiqIhJV0nBQBTU+3okKCFDy9WwferkHjtxib7t3xIUQtHxnIwtx4mpg26/HfwVNVDb4oI9RHmx5WGelRVlrtiw43zboCLaxv46AZeB3IlTkwouebTr1y2NjSpHz68WNFjHvupy3q8TFn3Hos2IAk4Ju5dCo8B3wP7VPr/FGaKiG+T+v+TQqIrOqMTL1VdWV1DdmcbO8KXBz6esmYWYKPwDL5b5FA1a0hwapHiom0r/cKaoqr+27/XcrS5UwSMbQAAAABJRU5ErkJggg==">
        </a>
    </td>
  </tr>
</table>

## Quickstart

### Install

```bash
pip install ipor-fusion
```

### Connect and execute

```python
from ipor_fusion import Web3Context, PlasmaVault, AaveV3SupplyFuse
from web3 import Web3

# 1. Create a Web3 context with your provider and private key
ctx = Web3Context.from_url(
    url="https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY",
    private_key="0x...",
)

# 2. Wrap the PlasmaVault contract
vault = PlasmaVault(ctx, Web3.to_checksum_address("0xVAULT_ADDRESS"))

# 3. Build a fuse action (e.g. supply USDC to Aave V3)
fuse = AaveV3SupplyFuse(Web3.to_checksum_address("0xFUSE_ADDRESS"))
action = fuse.supply(
    asset=Web3.to_checksum_address("0xUSDC_ADDRESS"),
    amount=1_000_000,  # 1 USDC (6 decimals)
)

# 4. Execute on-chain (execute() returns a Call; .send() signs and submits it)
receipt = vault.execute([action]).send()
```

Fuse, factory and manager addresses per chain are published in
[ipor-abi](https://github.com/IPOR-Labs/ipor-abi) (`mainnet/mainnet-<chain>-fusion/addresses.json`).
A fuse must also be registered on the vault; `vault.get_fuses().call()` lists the registered ones.

Amounts are raw on-chain integers (`Amount`, `Shares` in `ipor_fusion.types`); the SDK never scales by decimals.

### Read, send, or simulate

Every wrapper method returns a `Call` instead of executing. The same `Call` powers all three modes:

```python
from ipor_fusion import VaultSimulator

total = vault.total_assets().call()          # eth_call -> Amount
receipt = vault.execute([action]).send()     # signed tx -> TxReceipt
payload = vault.execute([action]).calldata   # raw bytes for an external signer

# Simulate first via eth_simulateV1 (no local node); alpha is the account allowed to call execute()
sim = VaultSimulator(ctx.web3, vault=vault.address, alpha=Web3.to_checksum_address("0xALPHA"))
sim.observe("before", vault.total_assets())
sim.execute([action])
sim.observe("after", vault.total_assets())
result = sim.run()
if result.all_success:
    print(result.get("after") - result.get("before"))
```

## CLI Quickstart

The SDK ships with a `fusion` CLI for inspecting and managing Plasma Vaults from the terminal.

You need an RPC provider URL — get a free key at [Alchemy](https://www.alchemy.com/) or [Infura](https://www.infura.io/).

```bash
# Install with CLI extras (pipx keeps dependencies isolated)
pipx install 'ipor-fusion[cli]'

# Configure an RPC provider (auto-detects chain ID)
fusion config set-provider https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY

# Inspect a vault (auto-saves to config on first use)
fusion vault info 0xB8a451107A9f87FDe481D4D686247D6e43Ed715e --chain-id ethereum

# List saved vaults
fusion vault list

# Inspect a Morpho Blue market or MetaMorpho vault
fusion market morpho-blue 0xMARKET_ID --chain ethereum
fusion market meta-morpho 0xVAULT_ADDRESS --chain ethereum
```

## MCP Server

Two ways to reach IPOR Fusion from an MCP-compatible AI assistant (Claude Code, Cursor, Windsurf, etc.).

### Hosted server (no install)

`https://mcp.ipor.io/mcp` is public, unauthenticated and read-only — inspect any Fusion vault without your own RPC key.

```bash
claude mcp add --transport http ipor-fusion https://mcp.ipor.io/mcp
```

```json
{
  "mcpServers": {
    "ipor-fusion": {
      "type": "http",
      "url": "https://mcp.ipor.io/mcp"
    }
  }
}
```

Tools: `vaults_list`, `vault_info`, `vault_oracle_mapping`, `fusion_addresses_list`, `fusion_address_names`, `fusion_address_lookup`, `market_morpho_blue`, `market_meta_morpho`.

### Local server (from the SDK)

The SDK ships a `fusion-mcp` server that exposes the CLI over MCP, against your own RPC providers and local config.

```bash
# Install with MCP extras (pipx keeps dependencies isolated)
pipx install 'ipor-fusion[mcp]'
```

Add to your MCP client configuration (e.g. `.mcp.json`):

```json
{
  "mcpServers": {
    "ipor-fusion": {
      "command": "fusion-mcp",
      "type": "stdio"
    }
  }
}
```

Available tools:

| Tool | Description |
|------|-------------|
| `server_info` | Server name, running version and changelog entries |
| `config_show` | Show current configuration (providers, vaults, API key status) |
| `config_set_provider` | Set RPC provider URL for a chain (auto-detects chain ID) |
| `config_set_etherscan_key` | Set Etherscan API key (enables contract name resolution) |
| `vault_info` | Full on-chain vault state — assets, fuses, balances, fees, lending health, reconciliation |
| `vault_role_accounts` | Accounts holding each AccessManager role on a vault |
| `vault_oracle_mapping` | Price-oracle sources per asset for a vault |
| `vault_list` | List all saved vaults |
| `vault_add` | Save a vault to the local config (auto-fetches on-chain name) |
| `vault_remove` | Remove a vault from the local config |
| `market_morpho_blue` | Morpho Blue market parameters and state |
| `market_meta_morpho` | MetaMorpho V1 or Morpho Vault V2 allocations and caps (Morpho API) |

Configure providers and vaults via `fusion config` or the MCP config tools first.

Besides the tools, `fusion-mcp` serves the guide from `ipor_fusion.guide` as the
resources `fusion://glossary`, `fusion://architecture`, `fusion://invariants` and
`fusion://quickstart` (the executed deploy-and-operate walk), and the prompts
`quickstart`, `deploy_vault`, `analyze_vault`, `trace_oracle_pricing` and
`explain_fuse` (slash commands in clients that support MCP prompts).

## Agent skills

[`skills/ipor-deploy-vault/SKILL.md`](skills/ipor-deploy-vault/SKILL.md) teaches a
coding agent the full clone → roles → market → access posture → deposit → execute
walk, with the invariants and the revert selectors, before it writes vault code.
Its body is `fusion://invariants` and `fusion://quickstart` from `ipor_fusion.guide`
verbatim (a test keeps them identical), so the skill and the MCP resources are one
text with two delivery paths. It follows the
[Agent Skills](https://agentskills.io/specification) format and versions with the
SDK. One install per machine:

```bash
# Claude Code — plugin with the skill and the hosted MCP server
/plugin marketplace add IPOR-Labs/ipor-fusion.py
/plugin install ipor-fusion@ipor-fusion

# Codex CLI, Gemini CLI, Cursor — the skill; add the MCP server as shown above
npx skills add IPOR-Labs/ipor-fusion.py -g -a codex      # or gemini-cli, cursor
```

## Common errors

Reverts on the deploy-and-configure path, keyed by selector so a failed transaction is greppable. This table and the full invariants ship in the wheel as [`ipor_fusion.guide`](src/ipor_fusion/guide/invariants.md), which `fusion-mcp` serves as `fusion://invariants`.

| Selector | Revert | What happened | Fix |
|---|---|---|---|
| `0x8745fbfd` | `DaoFeePackagesArrayEmpty()` | `clone()` was sent to `IporFusionFactoryImpl` | Send it to `IporFusionFactoryProxy` for that chain |
| `0x9996b315` | `AddressEmptyCode(address)` | `execute()` touched a market with no balance fuse | `add_balance_fuse(market_id, balance_fuse)` before the first `execute` on that market |
| `0x068ca9d8` | `AccessManagedUnauthorized(address)` | The caller lacks the role the function requires: `FUSE_MANAGER` for `add_fuses`, `grant_market_substrates`, `add_balance_fuse`; `ALPHA` for `execute`; `WHITELIST` for `deposit` and `mint` on a private vault | `AccessManager.grant_role(role, account, 0)` from the role's admin (`OWNER` grants `ATOMIST`, `ATOMIST` grants the rest); for a reverting `deposit`, whitelist the depositor or convert the vault to public |
| `ValueError: Private key required for sending transactions` | SDK, before any transaction | `.send()` on a `Web3Context` without a key | `Web3Context(w3, chain_id, signer=..., private_key=...)` or `Web3Context.from_url(url, private_key=...)` |

`clone()` grants the owner only `Roles.OWNER_ROLE` (1). OWNER grants `Roles.ATOMIST_ROLE` (100), which administers `Roles.FUSE_MANAGER_ROLE` (300, configuration), `Roles.ALPHA_ROLE` (200, `execute`) and `Roles.WHITELIST_ROLE` (800, `deposit` on a private vault).

Configuration order on a fresh vault: `add_fuses` → `grant_market_substrates` → `add_balance_fuse` → `execute`; all three configuration steps are mandatory.

The full clone → configure → deposit → execute sequence is exercised in [`tests/test_simulate_vault_from_scratch_base.py`](tests/test_simulate_vault_from_scratch_base.py).

## Vault construction examples

Runnable, canonical examples for building and configuring a vault from scratch live in
[`examples`](examples). They preview and simulate through `eth_simulateV1` — nothing is
ever signed or broadcast.

- [Simple Aave V3 supply vault](examples/simple_aave_v3_supply_base.py) — start here for vault
  creation, role bootstrap, and configuring a single supported market.
- [Advanced Euler V2 credit-market vault](examples/advanced_euler_v2_credit_market_base.py) —
  continue here for composing multiple functional fuses, typed Euler substrates and sub-accounts,
  and an ordered collateral → borrow → repay → unwind lifecycle.

```bash
export BASE_PROVIDER_URL="https://base-mainnet.g.alchemy.com/v2/YOUR_KEY"
uv run python examples/simple_aave_v3_supply_base.py
uv run python examples/advanced_euler_v2_credit_market_base.py
```

No RPC key? `BASE_PROVIDER_URL=https://mainnet.base.org` (Base's public RPC) runs both examples out of
the box — it supports `eth_simulateV1` and serves the pinned block. It is rate-limited, so use a
dedicated archive node for repeated or CI runs.

## Architecture

The SDK uses a **fuse adapter pattern**:

- **Fuses** encode protocol-specific calls into `FuseAction` objects (pure calldata, no state)
- **PlasmaVault** batches and executes `FuseAction` sequences on-chain via `execute()`; a batch is atomic
- **Web3Context** manages provider connections, signing, and transaction dispatch
- **Call** is the lazy result of every wrapper method: `.call()`, `.send()`, `.calldata`, or feed it to `VaultSimulator`

```
Fuse.method()  -->  FuseAction  -->  PlasmaVault.execute([actions])  -->  Call  -->  .send() / simulate
```

### Core modules (`ipor_fusion.core`)

| Module | Purpose |
|--------|---------|
| `Web3Context` | Provider connection, signing, tx dispatch, gas estimation |
| `Call` | Pre-encoded contract call; `.call()`, `.send()`, `.calldata`, `.build_transaction()` |
| `PlasmaVault` | ERC-4626 vault — execute, deposit, withdraw, fuse and market configuration |
| `VaultSimulator` | Batch `execute` + reads through `eth_simulateV1`, multi-block, no local node |
| `FusionFactory` | Deploy a new vault (`clone`, `clone_supervised`) |
| `AccessManager` | Role-based access control |
| `RewardsManager` | Claim and vest rewards |
| `WithdrawManager` | Time-windowed withdrawal requests |
| `FeeManager` | Deposit, performance, and management fee configuration |
| `FeeAccount` | Fee escrow account, resolves its `FeeManager` |
| `PriceOracleMiddleware` | Asset price feeds |
| `PriceOracleMiddlewareManager` | Per-vault price-source overrides |
| `ExternalStateExecutor` | NAV propose/confirm for off-vault capital (market 50) |
| `ERC20` | Token reads and approvals |

### Supported protocols (`ipor_fusion.fuses`)

| Protocol | Fuses |
|----------|-------|
| Aave V3 | `AaveV3SupplyFuse`, `AaveV3BorrowFuse` |
| Morpho | `MorphoSupplyFuse`, `MorphoCollateralFuse`, `MorphoBorrowFuse`, `MorphoFlashLoanFuse`, `MorphoClaimFuse` |
| Euler V2 | `EulerV2SupplyFuse`, `EulerV2CollateralFuse`, `EulerV2ControllerFuse`, `EulerV2BorrowFuse`, `EulerV2BatchFuse`, `EulerV2SwapDeployFuse`, `EulerV2SwapReconfigureFuse`, `EulerV2SwapRegistryFuse` |
| Uniswap V3 | `UniswapV3SwapFuse`, `UniswapV3NewPositionFuse`, `UniswapV3ModifyPositionFuse`, `UniswapV3CollectFuse` |
| Ramses V2 | `RamsesV2NewPositionFuse`, `RamsesV2ModifyPositionFuse`, `RamsesV2CollectFuse`, `RamsesClaimFuse` |
| Compound V3 | `CompoundV3SupplyFuse` |
| Gearbox V3 | `GearboxSupplyFuse`, `GearboxStakeFuse` |
| ERC-4626 | `ERC4626SupplyFuse` |
| Fluid Instadapp | `FluidInstadappSupplyFuse`, `FluidInstadappStakingFuse` |
| Merkl | `MerklClaimWrapperFuse` |
| Universal | `UniversalTokenSwapperFuse` |
| Off-vault capital | `AsyncActionFuse` (market 40), `ExternalStateOperationFuse` (market 50) |

### Readers (`ipor_fusion.readers`)

Read-only aggregators over positions and health, no fuses involved:

| Reader | Purpose |
|--------|---------|
| `MorphoReader`, `AaveV3Reader`, `CompoundV3Reader` | Market params, rates and vault positions per lending protocol |
| `UniswapV3Reader`, `RamsesV2Reader` | LP position details |
| `fetch_vault_lending_health` | Health factor per lending market a vault is in |
| `build_oracle_mapping` | How the vault prices every configured asset |

### Supported networks

- **Ethereum** mainnet
- **Arbitrum** One
- **Base**

## Development

```bash
uv sync --all-extras                                       # Install dependencies
uv run pytest tests/test_fuse_encoding.py -n auto -v       # Unit tests (fast, offline)
uv run pytest -v -s                                        # All tests (needs `.*_PROVIDER_URL` variables in `.env`)
uv run ruff format ./                                      # Format
uv run ruff check ./                                       # Lint
uv run pyright                                             # Type check
```

Integration tests need provider URLs in `.env` (eth_simulateV1 at a pinned fork block — no local node):

```bash
cp .env.example .env
# Edit .env with ARBITRUM_PROVIDER_URL, ETHEREUM_PROVIDER_URL, BASE_PROVIDER_URL
```

Contributor and coding-agent instructions (commands, conventions, invariants, domain rules) live in [AGENTS.md](AGENTS.md).

## Examples

For full usage patterns, see the example repository: [ipor-fusion-alpha-example](https://github.com/IPOR-Labs/ipor-fusion-alpha-example)

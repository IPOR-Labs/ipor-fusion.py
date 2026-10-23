<p align="center">
    <img height="80px" width="80px" src="https://ipor.io/images/ipor-fusion.svg" alt="IPOR Fusion Python SDK"/>
    <h1 align="center">IPOR Fusion Python SDK</h1>
</p>

`ipor_fusion` is the official Python SDK for **IPOR Fusion Plasma Vaults** — typed abstractions for DeFi protocol interactions on EVM chains through a fuse adapter pattern.

Maintained by <a href="https://ipor.io">IPOR Labs AG</a>.

> **Preview, not production:** the crosschain market (54: Stargate and CCIP executors,
> dispatchers, lanes, fuses) and the HyperCore market (55: fuses, readers, events) are
> under active development. Their contracts are not production releases, the mainnet
> deployments are a proof of concept and IPOR Labs canaries, and their interfaces may
> change between minor versions. Route no production capital through them. Everything
> else in the package is production. `server_info` and `fusion changelog` list the same
> status (`PREVIEW_FEATURES`).

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

> **Upgrading from 3.x?** 4.0 breaks 3.x SDK code at run time: wrappers are
> built from an address alone and every execution takes the context
> (`.call(ctx)`, `.send(ctx)`), chain-reading methods are `read_*(ctx, ...)`, the
> vault read is one pinned snapshot, and several modules and names moved. A 3.x call
> such as `PlasmaVault(ctx, address)` fails only when it runs, so a broad
> `except Exception` can hide it. Follow the ordered checklist in
> [MIGRATION_4.md, *Start here*](https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/MIGRATION_4.md#start-here).

## Quickstart

### Install

```bash
pip install ipor-fusion
```

### Connect and execute

```python
from ipor_fusion import (
    AaveV3SupplyFuse,
    Amount,
    PlasmaVault,
    Web3Context,
    parse_fuse_address,
    parse_plasma_vault_address,
)
from web3 import Web3

# 1. Create a Web3 context with your provider and private key
ctx = Web3Context.from_url(
    url="https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY",
    private_key="0x...",
)

# 2. Wrap the PlasmaVault contract (an address only; contexts are passed at execution)
vault = PlasmaVault(parse_plasma_vault_address("0xVAULT_ADDRESS"))

# 3. Build a fuse action (e.g. supply USDC to Aave V3)
fuse = AaveV3SupplyFuse(parse_fuse_address("0xFUSE_ADDRESS"))
action = fuse.supply(
    asset=Web3.to_checksum_address("0xUSDC_ADDRESS"),
    amount=Amount(1_000_000),  # 1 USDC (6 decimals)
)

# 4. Execute on-chain (execute() returns a Call; .send(ctx) signs and submits it)
receipt = vault.execute([action]).send(ctx)
```

Fuse, factory and manager addresses per chain are published in
[ipor-abi](https://github.com/IPOR-Labs/ipor-abi) (`mainnet/mainnet-<chain>-fusion/addresses.json`);
`fetch_address_registry(chain_id).address("SupplyFuseAaveV3")` reads them by name (`ipor_fusion.registry`).
A fuse must also be registered on the vault; `vault.get_fuses().call(ctx)` lists the registered ones.

Amounts are raw on-chain integers (`Amount`, `Shares` in `ipor_fusion.types`); the SDK scales by decimals
only in explicit boundary converters.
`parse_amount("1.5", Decimals(6))` and `format_amount(Amount(1_500_000), Decimals(6))` convert exactly at the
boundary (no floats, no rounding; `parse_shares` / `format_shares` for shares at the share token's decimals).

### Read, send, or simulate

Every wrapper method returns a `Call` instead of executing. Wrappers and `Call`s hold no
`Web3Context`: the context is a required argument wherever something runs, and methods that
must read the chain to answer are named `read_*` and take it first
(`vault.read_balance_fuses(ctx)`). The same `Call` powers all three modes:

```python
from ipor_fusion import VaultSimulator

total = vault.total_assets().call(ctx)          # eth_call -> Amount
receipt = vault.execute([action]).send(ctx)     # signed tx -> TxReceipt
payload = vault.execute([action]).calldata   # raw bytes for an external signer

# Simulate first via eth_simulateV1 (no local node); alpha is the account allowed to call execute()
sim = VaultSimulator(ctx.web3, vault=vault.address, alpha=Web3.to_checksum_address("0xALPHA"))
before = sim.observe(vault.total_assets())      # SimulationObservation[Amount]
sim.execute([action])
after = sim.observe(vault.total_assets())
result = sim.run()
if result.all_success:
    print(after.value - before.value)
```

Read a batch back from calldata, e.g. to review a multisig transaction:

```python
from ipor_fusion import AaveV3SupplyFuse, decode_execute_calldata, decode_fuse_action, fuse_classes_for

# The class behind each fuse address you know (from the registry or your config);
# fuse_classes_for(action) lists the candidates when you do not.
known = {fuse.address: AaveV3SupplyFuse}
for action in decode_execute_calldata(payload):  # execute / claimRewards calldata
    cls = known.get(action.fuse)
    if cls is None:
        print(action.fuse, "one of", [c.__name__ for c in fuse_classes_for(action)])
        continue
    decoded = decode_fuse_action(action, cls)
    print(decoded.label, decoded.args)  # AaveV3SupplyFuse.enter ((asset, amount, e_mode),)
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

# Validate a vault: ok / warning / critical verdict, exit status 1 on critical
fusion vault validate 0xB8a451107A9f87FDe481D4D686247D6e43Ed715e --chain-id ethereum

# List saved vaults
fusion vault list

# Check an execute batch before signing it (role, fuses, balance fuses, simulation)
fusion vault preflight 0xVAULT_ADDRESS --calldata 0xEXECUTE_CALLDATA --sender 0xALPHA

# Inspect a Morpho Blue market or MetaMorpho vault
fusion market morpho-blue 0xMARKET_ID --chain ethereum
fusion market meta-morpho 0xVAULT_ADDRESS --chain ethereum

# Find IPOR Fusion contracts in the ipor-abi registry
fusion address lookup SupplyFuseAaveV3 --chain base
fusion address list --chain base
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

Tools: `vaults_list`, `vault_info`, `vault_validate`, `vault_oracle_mapping`, `fusion_addresses_list`, `fusion_address_names`, `fusion_address_lookup`, `market_morpho_blue`, `market_meta_morpho`.

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
| `vault_validate` | Health verdict (ok / warning / critical) with the reconciliation, lending-health and ERC20 numbers behind it |
| `vault_preflight` | Check execute calldata as its sender would send it: permission, fuses, balance fuses, simulation |
| `vault_role_accounts` | Accounts holding each AccessManager role on a vault |
| `vault_oracle_mapping` | Price-oracle sources per asset for a vault |
| `vault_list` | List all saved vaults |
| `vault_add` | Save a vault to the local config (auto-fetches on-chain name) |
| `vault_remove` | Remove a vault from the local config |
| `market_morpho_blue` | Morpho Blue market parameters and state |
| `market_meta_morpho` | MetaMorpho V1 or Morpho Vault V2 allocations and caps (Morpho API) |
| `address_lookup` | IPOR Fusion contracts by name substring or address (ipor-abi registry) |
| `address_names` | Contract names in the registry, with the chains that have them |
| `address_list` | One chain's full `{name: address}` registry |

Configure providers and vaults via `fusion config` or the MCP config tools first.

Besides the tools, `fusion-mcp` serves the guide from `ipor_fusion.guide` as the
resources `fusion://glossary`, `fusion://architecture`, `fusion://invariants` and
`fusion://quickstart` (the executed deploy-and-operate walk), and the prompts
`quickstart`, `deploy_vault`, `analyze_vault`, `trace_oracle_pricing` and
`explain_fuse` (slash commands in clients that support MCP prompts).

## Agent skills

[`skills/ipor-deploy-vault/SKILL.md`](https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/skills/ipor-deploy-vault/SKILL.md) teaches a
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

Reverts on the deploy-and-configure path, keyed by selector so a failed transaction is greppable. This table and the full invariants ship in the wheel as [`ipor_fusion.guide`](https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/src/ipor_fusion/guide/invariants.md), which `fusion-mcp` serves as `fusion://invariants`.

| Selector | Revert | What happened | Fix |
|---|---|---|---|
| `0x8745fbfd` | `DaoFeePackagesArrayEmpty()` | `clone()` was sent to `IporFusionFactoryImpl` | Send it to `IporFusionFactoryProxy` for that chain |
| `0x9996b315` | `AddressEmptyCode(address)` | `execute()` touched a market with no balance fuse | `add_balance_fuse(market_id, balance_fuse)` before the first `execute` on that market |
| `0x4bf4de4e` | `HandlerNotFound()` | A protocol called back into the vault during `execute()` (a flash loan) and no handler is registered for its address and selector | `update_callback_handler(handler, sender, selector)` before the first `execute` that triggers the callback |
| `0x068ca9d8` | `AccessManagedUnauthorized(address)` | The caller lacks the role the function requires: `FUSE_MANAGER` for `add_fuses`, `grant_market_substrates`, `add_balance_fuse`, `update_callback_handler`; `ALPHA` for `execute`; `WHITELIST` for `deposit` and `mint` on a private vault | Check first with `AccessManager.can_call(caller, target, selector).call(ctx)` (a closed target or a missing role answers `immediate=False, delay=0`; a role with an execution delay answers its `delay`). `AccessManager.grant_role(role, account, Period(0))` from the role's admin (`OWNER` grants `ATOMIST`, `ATOMIST` grants the rest); for a reverting `deposit`, whitelist the depositor or convert the vault to public |
| `ValueError: Private key required for sending transactions` | SDK, before any transaction | `.send(ctx)` on a `Web3Context` without a key | `Web3Context(w3, chain_id, signer=..., private_key=...)` or `Web3Context.from_url(url, private_key=...)` |

`clone()` grants the owner only `Roles.OWNER_ROLE` (1). OWNER grants `Roles.ATOMIST_ROLE` (100), which administers `Roles.FUSE_MANAGER_ROLE` (300, configuration), `Roles.ALPHA_ROLE` (200, `execute`) and `Roles.WHITELIST_ROLE` (800, `deposit` on a private vault).

Configuration order on a fresh vault: `add_fuses` → `grant_market_substrates` → `add_balance_fuse` → `execute`; all three configuration steps are mandatory.

A flash-loan fuse also needs `update_callback_handler(handler, sender, selector)` before its first `execute`: Morpho Blue calls back `onMorphoFlashLoan(uint256,bytes)`, the Euler EVC `onEulerFlashLoan(bytes)`, and an unregistered callback reverts `HandlerNotFound()`.

The full clone → configure → deposit → execute sequence is exercised in [`tests/test_simulate_vault_from_scratch_base.py`](https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/tests/test_simulate_vault_from_scratch_base.py).

Custom errors of the IPOR Fusion contracts and the libraries they import
(OpenZeppelin included) decode by name, so a simulated or sent call that reverts
reads `AccessManagedUnauthorized(0x...)`, `UnsupportedFuse()` or
`NavSettledStale(8453)` in `SimulationResult.revert_reason` and `TransactionError`
instead of a bare selector. Importing `ipor_fusion.crosschain` also registers
`CROSSCHAIN_ERROR_SIGNATURES`.
`register_custom_errors([...])` adds your own, and `SimulationResult.raise_for_failure()`
raises `SimulationError` naming the first failed call.

## Vault construction examples

Runnable, canonical examples for building and configuring a vault from scratch live in
[`examples`](https://github.com/IPOR-Labs/ipor-fusion.py/tree/main/examples). They preview and simulate through `eth_simulateV1` — nothing is
ever signed or broadcast.

- [Simple Aave V3 supply vault](https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/examples/simple_aave_v3_supply_base.py) — start here for vault
  creation, role bootstrap, and configuring a single supported market.
- [Advanced Euler V2 credit-market vault](https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/examples/advanced_euler_v2_credit_market_base.py) —
  continue here for composing multiple functional fuses, typed Euler substrates and sub-accounts,
  and an ordered collateral → borrow → repay → unwind lifecycle.
- [External-state margin leg](https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/examples/external_state_margin_leg_base.py) — a vault whose capital
  sits off-chain: typed market-50 substrates, a dual-custodian propose/confirm marking the NAV,
  the scheduled-withdrawal prep an alpha performs, and a mismatched confirmation being rejected.
- [Crosschain CCIP vault, Arbitrum hub and HyperEVM spoke](https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/examples/crosschain_ccip_usdc_arbitrum_hyperevm.py) —
  the released crosschain contracts as deployed: configure the CCIP factory pair as its owner,
  create both vaults and the executor, wire the crosschain market and NAV pre-hook, deploy the
  dispatcher, then supply → attest → DEPOSIT → REDEEM → recall → claim USDC (preview feature).
```bash
export BASE_PROVIDER_URL="https://base-mainnet.g.alchemy.com/v2/YOUR_KEY"
uv run python examples/simple_aave_v3_supply_base.py
uv run python examples/advanced_euler_v2_credit_market_base.py
uv run python examples/external_state_margin_leg_base.py
```

The crosschain example needs Arbitrum and HyperEVM archive RPCs and the executor and dispatcher
creation codes built with Foundry from the contracts release it pins; run without them, it prints
the `forge inspect` commands that build them:

```bash
export ARBITRUM_PROVIDER_URL="https://arb-mainnet.g.alchemy.com/v2/YOUR_KEY"
export HYPEREVM_PROVIDER_URL="https://YOUR_HYPEREVM_ARCHIVE_NODE"
export CCIP_EXECUTOR_CREATION_CODE_FILE=executor.hex CCIP_DISPATCHER_CREATION_CODE_FILE=dispatcher.hex
uv run python examples/crosschain_ccip_usdc_arbitrum_hyperevm.py
```

No RPC key? `BASE_PROVIDER_URL=https://mainnet.base.org` (Base's public RPC) runs the examples out of
the box — it supports `eth_simulateV1` and serves their pinned blocks. It is rate-limited, so use a
dedicated archive node for repeated or CI runs.

## Public API and stability

The supported API is `ipor_fusion.__all__`: import from `ipor_fusion`. Removing or
renaming a name in it, or changing a signature incompatibly, waits for a major
release; adding one does not.

- **Public subpackages** declare their own `__all__`, each a subset of the top
  level: `ipor_fusion.core`, `config`, `fuses` and `readers`. `ipor_fusion.guide`
  (the packaged docs) keeps its names in its own namespace.
- **Public modules** stay importable for the top-level names they define:
  `about`, `cache`, `chains`, `dependency_graph`, `errors`, `explorer`, `field_docs`,
  `market_ids`, `market_info`, `morpho_api`, `substrates`, `types`, `validation`,
  `vault_data` and `vault_info` (all under `ipor_fusion.`). A name that moved out of
  one of them keeps its old path there (`vault_data.FeeData`,
  `vault_data.WithdrawManagerData`, `validation.Erc20Holding`,
  `validation.Erc20Holdings`, `morpho_api.MorphoApiError`).
- **Internal:** `ipor_fusion.cli`, `ipor_fusion.mcp`, every other module (e.g.
  `vault_reads`) and every submodule of a public subpackage (e.g.
  `ipor_fusion.core.contract`, `ipor_fusion.fuses.base`; import their names from
  the subpackage or `ipor_fusion`), underscore names, and names in no `__all__`.
  They change without notice; the CLI and the MCP server are stable as commands
  and tools, not as Python modules.
- **Preview features** (`PREVIEW_FEATURES` in `ipor_fusion.about`) export only
  from their own subpackages, `ipor_fusion.crosschain` (with `crosschain.fuses`,
  `crosschain.ccip` and `crosschain.stargate`) and `ipor_fusion.hypercore`, never
  from the top level, and keep no backward compatibility: a minor release may
  change or remove them (see [Preview features](#preview-features-ipor_fusioncrosschain-ipor_fusionhypercore)). Their exception classes
  stay in `ipor_fusion.errors`, and the `vault info` document's HyperCore blocks
  (`HyperCoreJson`, ...) stay with the document.
- **Third-party types** are not re-exported: import `ChecksumAddress` and
  `HexAddress` from `eth_typing`, and `BlockIdentifier`, `Timestamp` and `TxParams`
  from `web3.types`. The SDK's own types (`ChainId`, `Amount`, `PlasmaVaultAddress`,
  ...) come from `ipor_fusion` or `ipor_fusion.types`; a preview feature's ids
  (`CommandId`, `HyperCoreAsset`, ...) come from its subpackage.

`tests/test_public_api.py` snapshots every `__all__` and the names each public
module keeps by path, so a change to the surface is a deliberate, reviewed edit.
`tests/test_public_type_reachability.py` fails when a public signature or field
uses a project type that no `__all__` exports.

## Architecture

The SDK uses a **fuse adapter pattern**:

- **Fuses** encode protocol-specific calls into `FuseAction` objects (pure calldata, no state)
- **PlasmaVault** batches and executes `FuseAction` sequences on-chain via `execute()`; a batch is atomic
- **Web3Context** manages provider connections, signing, and transaction dispatch
- **Call** is the lazy result of every wrapper method: `.call(ctx)`, `.send(ctx)`, `.calldata`, or feed it to `VaultSimulator`

```
Fuse.method()  -->  FuseAction  -->  PlasmaVault.execute([actions])  -->  Call  -->  .send(ctx) / simulate
```

### Core modules (`ipor_fusion.core`)

| Module | Purpose |
|--------|---------|
| `Web3Context` | Provider connection, signing, tx dispatch, gas estimation |
| `Call` | Pre-encoded contract call; `.call(ctx)`, `.send(ctx)`, `.calldata`, `.build_transaction(ctx)` |
| `PlasmaVault` | ERC-4626 vault — execute, deposit, withdraw, previews, fuse, market, fee and limit configuration |
| `VaultSimulator` | Batch `execute` + reads through `eth_simulateV1`, multi-block, no local node |
| `FusionFactory` | Deploy a new vault (`clone`, `clone_supervised`) |
| `AccessManager` | Role-based access control; `read_call_plan(ctx, call, caller=)` says whether a restricted call goes now, must be scheduled (execution delay), waits, is ready or is forbidden, and gives the call to send now; `schedule`, `execute` and `cancel` build the timelock calls |
| `RewardsManager` | Claim and vest rewards |
| `WithdrawManager` | Time-windowed withdrawal requests |
| `FeeManager` | Deposit, performance, and management fee configuration and harvesting |
| `ContextManager` | Run calls on approved context-aware targets on the caller's behalf |
| `FeeAccount` | Fee escrow account, resolves its `FeeManager` |
| `PriceOracleMiddleware` | Asset price feeds |
| `PriceOracleMiddlewareManager` | Per-vault price-source overrides |
| `ExternalStateExecutor` | NAV propose/confirm for off-vault capital (market 50); the executor's thresholds (`staleness_max`, `big_change_bps`, `dust_threshold`, `min_update_interval`), the live aggregate (`get_balance_fuse_snapshot`) and the pre-hook's staleness clock (`get_oldest_update_timestamp`) |
| `read_external_state`, `ExternalStateVaultState` | The market's vault-scoped state from the vault's ERC-7201 storage at one block: executor, the balance fuse's cached total, the custodian marker it last checked (a timestamp on the earlier executor build, the confirm count on the 2026-10-21 one) and the pause flag the pre-hook enforces (no getter exposes them) |
| `decode_external_state_event`, `EXTERNAL_STATE_TOPICS` | The ExternalState events the vault emits (big change, cache updates, unpause, rescue, executor creation and deployment, operation enter/exit), decoded by topic with their raw log identity; a malformed listed log raises |
| `erc20_balance_slot`, `VaultSimulator.with_erc20_balance` | Fund any address with any ERC-20 in a simulation by a storage override (slot found by probing), no holder to impersonate |
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

### Preview features (`ipor_fusion.crosschain`, `ipor_fusion.hypercore`)

Not production (see the note at the top) and outside the stable top level:
import these from their subpackage. They track the current official contracts
only, with no backward compatibility: from 4.1 on, a minor release may change or
remove any of these names and drops support for retired deployments (such as
the pilot executors and factories). Pin an exact version if you depend on them.

| Name | Purpose |
|------|---------|
| `StargateCrosschainExecutor`, `CcipCrosschainExecutor` | Crosschain executor reads and attestation (`propose_balance`, `approve_balance`) |
| `StargateCrosschainDispatcher`, `CcipCrosschainDispatcher` | Remote dispatcher state (`observation`, tracked idle, command lane) |
| `StargateCrosschainFactory`, `CcipCrosschainFactory` | Executor creation and route/asset configuration reads |
| `CrosschainLane`, `open_lane` | One executor/dispatcher pair driven transport-agnostically: supply, recall, command, claim, buckets, attestation (`attestation`, `needs_attestation`, `staleness_max`); `enforces_min_received` identifies real supply floors (CCIP requires zero); `open_lane` detects the transport and finds the fuses |
| `discover_deployment`, `open_lanes` | Read a vault's crosschain market from the hub alone (executors with transport, factory, fuses, attestation keys and spokes served; remote vaults per spoke) and open every lane; `LANES` maps a transport kind to its lane class |
| `CrosschainSimulator` | Multi-chain `eth_simulateV1` relay: replays LayerZero and CCIP messages between pinned chains by impersonating the endpoint or router on delivery, no bridge needed |
| `ccip_token_lane` | Whether a CCIP message lane exists from a chain and whether a token's pool serves it, read on Chainlink's Router, OnRamp, TokenAdminRegistry and pool |
| `decode_hypercore_event`, `hypercore_events` | The HyperCore fuse, pending-library, settlement and reporter events, decoded by topic with their raw log identity under the same input contract as `decode_external_state_event`; a malformed listed log raises `HyperCoreEventDecodeError` |
| Crosschain fuses (market 54): `StargateCrosschainSupplyFuse`, `StargateCrosschainCommandFuse`, `CcipCrosschainSupplyFuse`, `CcipCrosschainCommandFuse`, `CrosschainClaimFuse`; transport-agnostic bases `CrosschainSupplyFuse` / `CrosschainCommandFuse` with `StargateSendParams` / `CcipSendParams`, plus `Command`, `CrosschainSubstrateLib`, `OptionsBuilder` | `ipor_fusion.crosschain` |
| HyperCore fuses (market 55): `HyperCoreDepositFuse`, `HyperCoreMarginFuse`, `HyperCoreSendFuse`, `HyperCoreOrderFuse`, `HyperCoreCancelFuse`, `HyperCoreBuilderFeeFuse`, `HyperCoreSubstrates` | `ipor_fusion.hypercore`, with `HyperCoreReader`, `read_hypercore_vault_state` and the event decoders |

The crosschain API supports only the contracts of the 2026-10-21 release; earlier
deployments are unreadable (`MIGRATION_4.md`). `open_lane` and `discover_deployment`
take the market ID explicitly (54, or a keccak-derived one from
`crosschain_market_id(name)` for a vault built with its own): `discover_deployment`
reads the market's executor grants; `open_lane` takes an executor address and uses the
market ID to find the vault's matching fuses unless they are passed.

### Readers (`ipor_fusion.readers`)

Read-only aggregators over positions and health, no fuses involved:

| Reader | Purpose |
|--------|---------|
| `MorphoReader`, `AaveV3Reader`, `CompoundV3Reader` | Market params, rates and vault positions per lending protocol |
| `UniswapV3Reader`, `RamsesV2Reader` | LP position details |
| `fetch_vault_lending_health` | Health factor per lending market a vault is in |
| `build_oracle_mapping` | How the vault prices every configured asset |

### Supported networks

The on-chain vault tooling (`fusion vault info`, `role-accounts`, `oracle-mapping`) is
validated on every live vault of these chains (`SUPPORTED_CHAIN_IDS`):

- **Ethereum** mainnet
- **Arbitrum** One
- **Base**
- **HyperEVM**
- **Unichain**
- **Plasma**
- **Avalanche** C-Chain
- **Katana**
- **Monad**
- **Ink** and **Robinhood**, which have no live vault yet: validated on a vault freshly
  cloned through their FusionFactory on a fork

Balance fuses and the withdraw manager are read from vault storage. The remaining event
scans (role holders, pending withdraw requests) page `eth_getLogs` within the provider's
limits (`get_logs_adaptive`). Where a provider caps the range, such as 10,000 blocks on
Unichain and Plasma, an old contract needs thousands of requests: past
`Web3Context.log_scan_max_requests` (1,000 by default; `None` lifts it) or
`log_scan_timeout_s` the scan raises `LogScanError`, and `vault info` reports those two
fields as unavailable (`null`) rather than partial. A `LogSource` on the context, such as
a service that caches an explorer's log API, answers those scans instead
(`Web3Context(log_source=…)`); when it raises `LogSourceUnavailable` the RPC scan runs.

Immutable contract facts (token symbols and decimals, contract names, deployment blocks)
are cached per chain and address in the context's `ContractCache`. The default,
`FileContractCache`, is `$XDG_CACHE_HOME/ipor-fusion/contract_cache.json`
(`~/.cache/...`), written atomically and ignored when corrupt or unwritable. A service
passes `Web3Context(..., cache=NullCache())` to keep nothing, `FileContractCache(path)`
to move it, or its own `get` / `put` implementation; `get_contract_name(..., cache=)`
takes the same object.

## Development

```bash
uv sync --all-extras --locked                              # Install dependencies (as CI)
uv run pytest tests/test_fuse_encoding.py -n auto -v       # Unit tests (fast, offline)
uv run pytest -v -s                                        # All tests; live ones skip without their provider
uv run ruff format ./                                      # Format
uv run ruff check ./                                       # Lint
uv run pyright                                             # Type check
```

Each integration test runs when its provider URL is in `.env` (eth_simulateV1 at a
pinned fork block, no local node) and skips otherwise; HyperEVM tests also need
`HYPEREVM_PROVIDER_URL`, and the source-backed rehearsals a contracts checkout (see
AGENTS.md):

```bash
cp .env.example .env
# Edit .env with ARBITRUM_PROVIDER_URL, ETHEREUM_PROVIDER_URL, BASE_PROVIDER_URL
```

Contributor and coding-agent instructions (commands, conventions, invariants, domain rules) live in [AGENTS.md](https://github.com/IPOR-Labs/ipor-fusion.py/blob/main/AGENTS.md).

## Examples

For full usage patterns, see the example repository: [ipor-fusion-alpha-example](https://github.com/IPOR-Labs/ipor-fusion-alpha-example)

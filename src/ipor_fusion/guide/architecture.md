# IPOR Fusion architecture

## One vault, many protocols

```
depositor ── deposit / redeem (ERC-4626) ──► Plasma Vault ◄── execute([fuse actions]) ── Alpha
                                               │
                     ┌─────────────────────────┼─────────────────────────┐
                     ▼                         ▼                         ▼
              market 1 (Aave V3)        market 14 (Morpho)        market n (...)
              action fuses              action fuses              action fuses
              balance fuse              balance fuse              balance fuse
              substrates                substrates                substrates
                     │                         │                         │
                     └────────── Price Oracle Middleware values every position ──────────┘
```

A Plasma Vault is an ERC-4626 vault whose strategy is expressed as calls into
fuses. Each fuse targets one external protocol and one market id. The vault
`delegatecall`s the fuse, so the protocol sees the vault as the caller and the
vault's own storage records the result. Per market, the vault holds:

- the action fuses it may use (`add_fuses`),
- the substrates those fuses may touch (`grant_market_substrates`),
- exactly one balance fuse that values the position (`add_balance_fuse`).

`execute` runs a list of actions, then re-reads every touched market through
its balance fuse, applies the performance fee and checks that the vault's total
assets still reconcile. Positions are valued in the underlying asset through
the price oracle middleware, so the share price moves only when a balance fuse
reports a change.

## Governance

Every vault has its own access manager. The clone assigns `OWNER_ROLE` to the
`owner` argument and nothing else. The role-admin chain is
`OWNER → ATOMIST → {ALPHA, FUSE_MANAGER, WHITELIST, UPDATE_MARKETS_BALANCES}`,
so the owner grants itself `ATOMIST_ROLE` first and the operating roles after.
`ALPHA_ROLE` is the only role that can call `execute`; it is meant for the
strategy bot. `FUSE_MANAGER_ROLE` configures markets. A fresh vault is
private: `deposit` and `mint` require `WHITELIST_ROLE` until an Atomist calls
`convert_to_public_vault()`, which is one-way.

Optional managers plug into the vault: a Withdraw Manager for scheduled
withdrawals, a Rewards Claim Manager for vesting claimed rewards, a Fee
Manager for management and performance fees.

## Deployment

Vaults are not deployed from bytecode. `FusionFactory.clone(...)` on the
per-chain `IporFusionFactoryProxy` creates the vault together with its access
manager, fee manager, rewards manager, withdraw manager, context manager and
price manager in one transaction. Clone addresses are CREATE2-deterministic: `.call()` on the same
arguments previews them for free and `.send()` deploys. `clone_supervised` has
the same shape but is gated by a maintenance role; use `clone` unless you were
told otherwise.

Fuse and factory addresses differ per chain and are not embedded in the SDK.
Resolve them by contract name in the public `ipor-abi` registry. Names are
not uniform across chains: the Aave V3 balance fuse is `BalanceFuseAaveV3` on
Base and `AaveV3WithPriceOracleMiddlewareBalanceFuse` on Arbitrum. The SDK
class names swap the order of the registry names (`SupplyFuseAaveV3` in the
registry is `AaveV3SupplyFuse` in Python).

## Read path and write path

Inspection tools (vault info, oracle mapping, role holders, market data) are
read-only and need no key. Anything that changes state — cloning, granting
roles, configuring markets, depositing, executing — goes through the Python SDK
with a `Web3Context` that carries a private key. `.call()` previews any call
without a key; `.send()` signs locally and needs one.

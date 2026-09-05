# Vault examples

Canonical, runnable examples for working with IPOR Fusion vaults -- building and
configuring one from scratch, and one-shot operations on a vault (supply, withdraw,
claim, rebalance, inspect). They exist so a developer -- or a coding agent -- can
learn a real workflow from a single readable file, without reverse-engineering the
test suite.

They preview and simulate through `eth_simulateV1`; they never sign or broadcast a
transaction.

## Which example to start with

- [`simple_aave_v3_supply_base.py`](simple_aave_v3_supply_base.py) -- start
  here. Creates a vault on Base, bootstraps its roles, wires a single market
  (Aave V3 supply fuse + balance fuse), grants only the USDC substrate, verifies
  oracle coverage, deposits, and runs one alpha-driven supply.

More examples (e.g. an advanced Euler V2 credit-market vault covering typed
substrates, collateral and borrowing) will land here following the same
conventions below.

## Prerequisites

- The `ipor-fusion` package installed (or this repo's dev environment).
- A Base RPC URL from an **archive node that implements `eth_simulateV1`**
  (Alchemy and other geth/reth-based providers do).

## Environment variables

- `BASE_PROVIDER_URL` (required) -- your Base archive RPC endpoint. Never commit it or
  share it; it embeds your provider key.

## Running

The shell snippets here assume a POSIX shell (bash or zsh); translate `export`
for others (`set -x VAR val` in fish, `$env:VAR = "val"` in PowerShell).

```bash
export BASE_PROVIDER_URL="https://base-mainnet.g.alchemy.com/v2/YOUR_KEY"
uv run python examples/simple_aave_v3_supply_base.py
```

The default run previews the deployment, prints the unsigned creation calldata
and an ordered transaction plan, executes the whole flow through a single
`eth_simulateV1` batch, and asserts the final state. It signs and broadcasts
nothing.

### Inspecting the calldata

Each example exposes pure builder functions (for instance
`unsigned_clone_calldata()` and `build_supply_action()`) that need no provider,
so you can import an example and inspect the exact bytes it would send. The
default run also logs the unsigned `clone(...)` creation calldata.

### Going to production

These examples only simulate; they never broadcast. To submit a flow on-chain, take
the `.calldata` each builder produces and sign it with your own signer -- a multisig,
hardware wallet, or key-management service. (`Call.send()` performs a direct send from a
configured signer, but a production deployment should not sign with a hot key.) Always
review production configuration independently before using it with real funds.

## Conventions

Every example follows a fixed set of conventions -- one-shot and simulation-only,
self-contained in a single file, with provenance-verified addresses, a deterministic
pinned block, fail-loud assertions, import-safety, and CI coverage. If you are
**writing or editing** an example, read [CONTRIBUTING.md](CONTRIBUTING.md) for the full
rules and the golden-standard structure.

## Scope

Each example is one-shot -- run it once, it performs its task and exits -- and narrow:
one coherent workflow, not a bundle of production concerns (fees, governance,
multi-market strategies). Long-running bots (loops, schedulers, watchers that poll and
act) live in
[`ipor-fusion-alpha-example`](https://github.com/IPOR-Labs/ipor-fusion-alpha-example),
not here. Always review production configuration independently before using it with
real funds. (Writing an example? The full scope rules are in
[CONTRIBUTING.md](CONTRIBUTING.md).)

## See also

- [Python SDK README](../README.md) -- install, quickstart, architecture.
- [ipor-fusion-alpha-example](https://github.com/IPOR-Labs/ipor-fusion-alpha-example) -- a runnable
  alpha-bot template for *long-running* vault operation (a loop/scheduler that watches and acts).
  One-shot actions -- including operations like withdraw or claim -- live here; a continuously
  running bot lives there.
- [IPOR deployments and ABIs](https://github.com/IPOR-Labs/ipor-abi) -- the
  source of provenance for every address used here.
- [IPOR Fusion documentation](https://docs.ipor.io/ipor-fusion/fusion-introduction)
  -- concepts referenced by the examples: access management, substrates, and the
  price oracle middleware.

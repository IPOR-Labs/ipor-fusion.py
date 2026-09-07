# IPOR Fusion glossary

IPOR Fusion is a protocol for on-chain asset-management vaults on EVM chains.
It is a separate product from IPOR's interest-rate derivatives: "Fusion vault",
"Plasma Vault" and "IPOR vault" all mean the vault product described here.

- **Plasma Vault** — an ERC-4626 vault. Depositors receive shares; the vault
  holds the underlying asset plus positions in external protocols. Created by
  `clone()` on the Fusion Factory as a minimal proxy whose code never changes.
- **Underlying asset** — the ERC-20 the vault is denominated in. Deposits,
  withdrawals, total assets and share price are all expressed in it.
- **Fuse** — an immutable, non-upgradable contract the vault `delegatecall`s to
  interact with one external protocol (supply to Aave V3, lend on Morpho, swap
  on Uniswap, ...). A fuse holds no funds; it runs in the vault's storage.
- **Action fuse** (supply, borrow, swap, claim, ...) — a fuse that moves funds.
  `execute` takes a list of fuse actions and runs them in order.
- **Balance fuse** — the fuse that values the vault's position in one market,
  in the underlying asset, through the price oracle middleware. One balance
  fuse per market. Without it the vault cannot account for the market and
  `execute` on that market reverts.
- **Market** — a numbered integration slot (`IporFusionMarkets`, for example
  `AAVE_V3 = 1`, `MORPHO = 14`). Fuses, substrates and the balance fuse are all
  keyed by market id.
- **Substrate** — a `bytes32` grant on a market that whitelists what fuses on
  that market may touch: an asset address, a Morpho market id, a pool, a typed
  struct. The layout is market-specific. A fuse action outside the granted
  substrates reverts.
- **Fusion Factory** — the contract that clones new vaults. It is deployed as a
  proxy (`IporFusionFactoryProxy`) over an implementation
  (`IporFusionFactoryImpl`). Only the proxy carries configuration; `clone()` on
  the implementation reverts.
- **Access Manager** — one access manager per vault, created by the clone.
  Every privileged vault function is gated by a numeric role id.
- **Roles** — `OWNER_ROLE` (1) administers the vault's roles; `ATOMIST_ROLE`
  (100) configures the vault and administers the operating roles;
  `ALPHA_ROLE` (200) calls `execute`; `FUSE_MANAGER_ROLE` (300) adds fuses,
  substrates and balance fuses; `WHITELIST_ROLE` (800) may deposit while the
  vault is private; `GUARDIAN_ROLE` (2) cancels scheduled operations and
  closes targets. `PUBLIC_ROLE` means every address.
- **Alpha** — the operator, a person or a bot, that holds `ALPHA_ROLE` and runs
  the strategy by calling `execute`.
- **Atomist** — the vault administrator that holds `ATOMIST_ROLE`.
- **Price Oracle Middleware** — the vault's price source. Maps each asset to an
  oracle feed so balance fuses and the share price agree on one valuation.
- **Withdraw Manager** — optional contract that schedules withdrawals
  (request, delay, release) for vaults whose positions cannot be exited
  instantly.
- **Rewards Claim Manager** — optional contract that holds claimed protocol
  rewards outside the vault's total assets and vests them in over time.
- **Fee Manager** — mints management and performance fees as vault shares to
  the configured recipients.
- **ipor-abi** — the public registry of contract ABIs and per-chain deployment
  addresses (factories, fuses, oracles): https://github.com/IPOR-Labs/ipor-abi

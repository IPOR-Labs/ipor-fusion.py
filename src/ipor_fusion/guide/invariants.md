# IPOR Fusion invariants

Rules that hold for every Fusion vault. Breaking one is a revert; the revert
name and selector are listed with each rule so a failed transaction is
greppable.

1. **Clone from `IporFusionFactoryProxy`, never from `IporFusionFactoryImpl`.**
   Only the proxy carries the fee configuration. `clone()` on the
   implementation reverts `DaoFeePackagesArrayEmpty()` (`0x8745fbfd`). The
   two are published side by side in the registry under those exact names;
   resolve the proxy by name, do not pattern-match a suffix.
2. **A fresh clone grants the owner only `OWNER_ROLE` (1).** Every
   configuration and execute call from that account reverts
   `AccessManagedUnauthorized(address)` (`0x068ca9d8`) until the roles are
   granted through the vault's access manager: `ATOMIST_ROLE` (100) first,
   because it administers the others, then `FUSE_MANAGER_ROLE` (300) for
   configuration and `ALPHA_ROLE` (200) for `execute`.
3. **Register all three before the first `execute` on a market:**
   `add_fuses([...])`, `grant_market_substrates(market_id, [...])` and
   `add_balance_fuse(market_id, balance_fuse)`. Any order between them works;
   what is not optional is that all three precede `execute`. `execute` on a market with no
   balance fuse reverts `AddressEmptyCode(address)` (`0x9996b315`) with the
   zero address; an action outside the granted substrates reverts inside the
   fuse. A fuse whose protocol calls back into the vault mid-`execute`
   (Morpho Blue flash loans, Uniswap V3 mints) also needs
   `update_callback_handler(handler, protocol, "onMorphoFlashLoan(uint256,bytes)")`
   before its first `execute`; without it the callback reverts inside the protocol.
4. **A fresh clone is private.** `deposit` and `mint` revert
   `AccessManagedUnauthorized(address)` (`0x068ca9d8`) until either the
   depositor holds `WHITELIST_ROLE` (800), which keeps the vault private, or an
   Atomist calls `convert_to_public_vault()`, which opens deposits to every
   address and cannot be reverted. A vault run by its owner's own bot
   whitelists the depositor; only a vault that takes outside money goes public.
5. **Fuses are immutable and addresses are per chain.** A fuse cannot be
   upgraded; new strategies are composed from already-registered fuses, and a
   new fuse is needed only for an action no registered fuse covers. Factory,
   fuse and token addresses differ on every chain and so may the registry
   names for the same role: Base publishes both `BalanceFuseAaveV3` and
   `AaveV3WithPriceOracleMiddlewareBalanceFuse`, Arbitrum only the latter, so a
   name that resolves on one chain may not exist on the next. Resolve
   `(chain, name)` in `ipor-abi`; never reuse an address across chains.
6. **`.send()` signs locally and needs a private key in the `Web3Context`;
   `.call()` previews without one.** Sending through a context built with
   `signer=` alone raises `ValueError("Private key required for sending
   transactions")`.

## Common errors

| Selector | Revert | What happened | Fix |
|---|---|---|---|
| `0x8745fbfd` | `DaoFeePackagesArrayEmpty()` | `clone()` was sent to `IporFusionFactoryImpl` | Send it to `IporFusionFactoryProxy` for that chain |
| `0x9996b315` | `AddressEmptyCode(address)` | `execute()` touched a market with no balance fuse | `add_balance_fuse(market_id, balance_fuse)` before the first `execute` on that market |
| `0x068ca9d8` | `AccessManagedUnauthorized(address)` | The caller lacks the role the function requires: `FUSE_MANAGER` for `add_fuses`, `grant_market_substrates`, `add_balance_fuse`; `ALPHA` for `execute`; `WHITELIST` for `deposit` and `mint` on a private vault | `AccessManager.grant_role(role, account, 0)` from the role's admin (`OWNER` grants `ATOMIST`, `ATOMIST` grants the rest); for a reverting `deposit`, whitelist the depositor or convert the vault to public |
| `ValueError: Private key required for sending transactions` | SDK, before any transaction | `.send()` on a `Web3Context` without a key | `Web3Context(w3, chain_id, signer=..., private_key=...)` or `Web3Context.from_url(url, private_key=...)` |

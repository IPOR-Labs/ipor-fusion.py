---
name: ipor-deploy-vault
description: Deploy, configure, fund and operate an IPOR Fusion Plasma Vault with the ipor-fusion Python SDK. Use when the user wants to create or deploy a Fusion vault, add fuses or a balance fuse, grant vault roles, whitelist depositors or open the vault to the public, deposit, or execute a strategy through a fuse — or hits DaoFeePackagesArrayEmpty, AddressEmptyCode or AccessManagedUnauthorized. Read it before writing any vault code.
license: BSD-3-Clause
metadata:
  homepage: https://docs.ipor.io/build-on-fusion
  repository: https://github.com/IPOR-Labs/ipor-fusion.py
---

# Deploy and operate an IPOR Fusion vault

IPOR Fusion is a protocol for on-chain asset-management vaults on EVM chains.
A **Plasma Vault** is an ERC-4626 vault whose strategy is expressed as calls
through **fuses**, immutable adapters to external protocols (Aave V3, Morpho,
Uniswap, ...). Fusion is a separate product from IPOR's interest-rate
derivatives; when a user says "IPOR vault", "Fusion vault" or "automated
strategy vault", they mean this.

The path from nothing to a vault with a live position is eleven transactions,
all signed by the account that will own the vault. There is no shortcut in the
SDK: every one of them is mandatory. Read the invariants first, then copy the
walk.

## Install

```bash
pip install ipor-fusion                                     # SDK (Python 3.10+)
curl -L https://foundry.paradigm.xyz | bash && foundryup    # anvil, for fork tests
anvil --fork-url <BASE_RPC_URL> --chain-id 8453             # then point the SDK at :8545
```

The hosted read-only server at `https://mcp.ipor.io/mcp` and the bundled
`fusion-mcp` server inspect vaults; neither deploys, configures, deposits or
executes. Those go through the SDK below.

## Invariants

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
3. **Configure a market in this order, all three before the first `execute`:**
   `add_fuses([...])`, then `grant_market_substrates(market_id, [...])`, then
   `add_balance_fuse(market_id, balance_fuse)`. `execute` on a market with no
   balance fuse reverts `AddressEmptyCode(address)` (`0x9996b315`) with the
   zero address; an action outside the granted substrates reverts inside the
   fuse.
4. **A fresh clone is private.** `deposit` and `mint` revert
   `AccessManagedUnauthorized(address)` (`0x068ca9d8`) until either the
   depositor holds `WHITELIST_ROLE` (800), which keeps the vault private, or an
   Atomist calls `convert_to_public_vault()`, which opens deposits to every
   address and cannot be reverted. A vault run by its owner's own bot
   whitelists the depositor; only a vault that takes outside money goes public.
5. **Fuses are immutable and addresses are per chain.** A fuse cannot be
   upgraded; a new strategy means adding a new fuse. Factory, fuse and token
   addresses differ on every chain and so may the registry names for the same
   role (`BalanceFuseAaveV3` on Base, `AaveV3WithPriceOracleMiddlewareBalanceFuse`
   on Arbitrum). Resolve `(chain, name)` in `ipor-abi`; never reuse an
   address across chains.
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

## The walk: clone, roles, market, access posture, deposit, execute

Base, USDC into Aave V3. Executed end to end on a Base fork with this SDK: all
eleven transactions land, the vault's idle USDC goes from 0 to the deposit
through `deposit()`, and its aToken balance rises after `execute`. Swap the
four addresses for another chain or market; keep the order.

```python
from eth_account import Account
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    IporFusionMarkets,
    PlasmaVault,
    Roles,
    Web3Context,
)
from ipor_fusion.core import FusionFactory
from ipor_fusion.fuses import AaveV3SupplyFuse

# A Base RPC, or an anvil fork of Base: anvil --fork-url <BASE_RPC_URL> --chain-id 8453
w3 = Web3(Web3.HTTPProvider("http://localhost:8545"))
OWNER_PRIVATE_KEY = "0x..."  # .send() signs locally; .call() previews need no key
owner = Account.from_key(OWNER_PRIVATE_KEY).address  # owns, configures and operates the vault
ctx = Web3Context(w3, chain_id=8453, signer=owner, private_key=OWNER_PRIVATE_KEY)

FACTORY_PROXY = "0x1455717668fA96534f675856347A973fA907e922"  # IporFusionFactoryProxy, Base
USDC = "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
SUPPLY_FUSE = "0x26fD6EF391E98C78CfCA27e00c3d15be4D941625"  # SupplyFuseAaveV3, Base
BALANCE_FUSE = "0xf53f3EaFfDf67539256365cA7299540A98b60BA9"  # BalanceFuseAaveV3, Base

# 1. Deploy. Clone addresses are CREATE2-deterministic: .call() previews them for
#    free, .send() with the same arguments creates the vault and its managers.
factory = FusionFactory(ctx, FACTORY_PROXY)
clone = factory.clone(
    asset_name="My Vault",
    asset_symbol="MV",
    underlying_token=USDC,
    redemption_delay_seconds=0,
    owner=owner,
)
instance = clone.call(ctx)
clone.send(ctx)
vault_address = instance.plasma_vault
access_manager_address = instance.access_manager

# 2. Roles. The clone grants `owner` only OWNER_ROLE. ATOMIST first: it administers the rest.
am = AccessManager(ctx, access_manager_address)
am.grant_role(Roles.ATOMIST_ROLE, owner, 0).send(ctx)
am.grant_role(Roles.FUSE_MANAGER_ROLE, owner, 0).send(ctx)
am.grant_role(Roles.ALPHA_ROLE, owner, 0).send(ctx)

# 3. Market, in this order: fuses -> substrates -> balance fuse.
vault = PlasmaVault(ctx, vault_address)
vault.add_fuses([SUPPLY_FUSE]).send(ctx)
usdc_substrate = bytes(12) + bytes.fromhex(USDC[2:])  # address right-aligned in bytes32
vault.grant_market_substrates(IporFusionMarkets.AAVE_V3, [usdc_substrate]).send(ctx)
vault.add_balance_fuse(IporFusionMarkets.AAVE_V3, BALANCE_FUSE).send(ctx)

# 4. Access posture. A fresh clone is private and deposit() reverts until one of these.
am.grant_role(Roles.WHITELIST_ROLE, owner, 0).send(ctx)  # (a) own bot: whitelist the depositor
# vault.convert_to_public_vault().send(ctx)              # (b) outside money: ATOMIST-only, one-way

# 5. Fund and run the first strategy step. The depositor must already hold the USDC.
amount = 1_000 * 10**6
ERC20(ctx, USDC).approve(vault_address, amount).send(ctx)
vault.deposit(amount, owner).send(ctx)
vault.execute([AaveV3SupplyFuse(SUPPLY_FUSE).supply(asset=USDC, amount=amount)]).send(ctx)
```

Notes on the walk:

- `clone` versus `clone_supervised`: same arguments; the supervised variant
  is gated by a maintenance role. Use `clone`.
- The balance fuse above is the `BalanceFuseAaveV3` entry of the registry
  for Base and is verified working on a fresh clone. Vaults already in
  production may register a different one for the same market; to match
  them, read `PlasmaVault(ctx, address).get_balance_fuses()` on such a vault.
- Substrate layouts are market-specific. For Aave V3 the substrate is the
  asset address right-aligned in 32 bytes; other markets use typed layouts,
  decoded by `ipor_fusion.substrates` and documented in the fuse libraries
  of the contracts repository.
- Give the bot its own key: grant `ALPHA_ROLE` to the bot's address instead
  of `owner`, and keep the owner key offline.

## Factory proxies per chain

`clone()` goes to the proxy column. The implementation column is listed only
so you recognise and avoid it. Verify against `ipor-abi` before use; the
registry is the source of truth and is regenerated on release.

| Chain | Chain id | `IporFusionFactoryProxy` (use) | `IporFusionFactoryImpl` (reverts) | Verified |
|---|---|---|---|---|
| Ethereum | 1 | `0xcd05909C4A1F8E501e4ED554cEF4Ed5E48D9b852` | `0xf19C1E9f6616F6056AF1e322A86fDaAaAf0263f5` | implementation slot |
| Unichain | 130 | `0xC9b4E839e86284C558791C13Bda97999fc49aF63` | `0xf5Bc40C192D51ED93ADD6557884766772FA72589` | implementation slot |
| TAC | 239 | `0x26b5561E2E0e7d7977431E92740903Ef3cB7194b` | `0x7DDd0bBB59eFB4b7791cE5E1Cf1587F083aE4Ef5` | implementation slot |
| Botanix | 3637 | `0x7c9857f22D0cc6523C8e88BFDCbf411AB8300fF5` | `0xddb0EFB2a7a5C02e01b5A1f85C0a1110d68EeC26` | registry only |
| Base | 8453 | `0x1455717668fA96534f675856347A973fA907e922` | `0x610152A79BE7F2Aa3aA70520c9331c18fe8D33b7` | clone executed on a fork |
| Plasma | 9745 | `0x70d41759FBF90fB63c88197217cB08F93a1c65CB` | `0x3A42bD6fEa94421025Ae2ed01CE6f137ac785abD` | implementation slot |
| Arbitrum | 42161 | `0x134fCAce7a2C7Ef3dF2479B62f03ddabAEa922d5` | `0x87f94ac9aF79261F0BC73582114f805F55Cd0b25` | clone executed on a fork |
| Avalanche | 43114 | `0xa00b6379833D77fA9C9497b32dae00dF39Ac751e` | `0x399596cFccFEbBb60f8652bece8586a6Bb4199ab` | implementation slot |
| Ink | 57073 | `0xEC53f69Bd1D991a2F99e96DE66E81D0E42A61D8D` | `0xe7372BC46b79e0c87AfE5286c41c20687018E425` | implementation slot |
| Katana | 747474 | `0xc29b8D591d6a3f109Ca7ba384F2e00162866D37B` | `0x4a4De6e86AD0d0546F55B595cA75f55803D940bA` | registry only |

"Implementation slot" means the proxy's EIP-1967 implementation slot was read
and matches the registry's `IporFusionFactoryImpl`; "registry only" means the
pair was taken from `ipor-abi` without an on-chain read.

Fuse addresses are not in the SDK. Resolve them by registry name and chain
in `ipor-abi` (`SupplyFuseAaveV3`, `BalanceFuseAaveV3`, ...), or with the
hosted server's `fusion_address_lookup(name, chain_id=...)`. The SDK class
for a registry name swaps the parts: `SupplyFuseAaveV3` is `AaveV3SupplyFuse`.

## Pointers

- Documentation: https://docs.ipor.io/build-on-fusion
- SDK: https://github.com/IPOR-Labs/ipor-fusion.py (PyPI `ipor-fusion`);
  `tests/test_simulate_vault_from_scratch_base.py` runs this walk in one
  `eth_simulateV1` batch
- Contract ABIs and per-chain addresses: https://github.com/IPOR-Labs/ipor-abi
- Contracts: https://github.com/IPOR-Labs/ipor-fusion
- A strategy bot that operates an existing vault:
  https://github.com/IPOR-Labs/ipor-fusion-alpha-example
- Read-only inspection of any live vault, no key needed:
  `https://mcp.ipor.io/mcp`. The `fusion-mcp` server bundled with the SDK
  (`pip install 'ipor-fusion[mcp]'`) serves this guide as the resources
  `fusion://glossary`, `fusion://architecture` and `fusion://invariants`

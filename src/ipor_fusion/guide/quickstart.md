# IPOR Fusion quickstart: deploy and operate a vault

The path from nothing to a vault with a live position is eleven transactions,
all signed by the account that will own the vault. There is no shortcut in the
SDK: every one of them is mandatory. The invariants (`fusion://invariants`)
say what reverts when a step is skipped. Copy the walk and change only the
addresses.

## Install

```bash
pip install ipor-fusion                                     # SDK (Python 3.11+)
curl -L https://foundry.paradigm.xyz | bash && foundryup    # anvil, for fork tests
anvil --fork-url <BASE_RPC_URL> --chain-id 8453             # then point the SDK at :8545
```

The hosted read-only server at `https://mcp.ipor.io/mcp` and the bundled
`fusion-mcp` server inspect vaults; neither deploys, configures, deposits or
executes. Those go through the SDK below.

## The walk: clone, roles, market, access posture, deposit, execute

Base, USDC into Aave V3. Executed end to end on a Base fork with this SDK: all
eleven transactions land, the vault's idle USDC goes from 0 to the deposit
through `deposit()`, and its aToken balance rises after `execute`. Swap the
four addresses for another chain or market; keep the order.

```python
import os

from eth_account import Account
from web3 import Web3

from ipor_fusion import (
    ERC20,
    AccessManager,
    Amount,
    ChainId,
    FusionFactory,
    IporFusionMarkets,
    Period,
    PlasmaVault,
    Roles,
    Web3Context,
    parse_fuse_address,
)
from ipor_fusion.fuses import AaveV3SupplyFuse

# A Base RPC, or an anvil fork of Base: anvil --fork-url <BASE_RPC_URL> --chain-id 8453
w3 = Web3(Web3.HTTPProvider("http://localhost:8545"))
OWNER_PRIVATE_KEY = os.environ["OWNER_PRIVATE_KEY"]  # .send(ctx) signs locally; .call(ctx) previews need no key
owner = Account.from_key(OWNER_PRIVATE_KEY).address  # owns, configures and operates the vault
ctx = Web3Context(w3, chain_id=ChainId(8453), signer=owner, private_key=OWNER_PRIVATE_KEY)

# Generic addresses are ChecksumAddress; fuse addresses carry their role (FuseAddress).
FACTORY_PROXY = Web3.to_checksum_address("0x1455717668fA96534f675856347A973fA907e922")  # IporFusionFactoryProxy, Base
USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
SUPPLY_FUSE = parse_fuse_address("0x26fD6EF391E98C78CfCA27e00c3d15be4D941625")  # SupplyFuseAaveV3, Base
BALANCE_FUSE = parse_fuse_address("0xf53f3EaFfDf67539256365cA7299540A98b60BA9")  # BalanceFuseAaveV3, Base

# 1. Deploy, then read the addresses actually created from the mined receipt.
#    Do not use a prior .call(ctx) preview: another clone can consume those CREATE
#    addresses before this transaction lands.
factory = FusionFactory(FACTORY_PROXY)
clone = factory.clone(
    asset_name="My Vault",
    asset_symbol="MV",
    underlying_token=USDC,
    redemption_delay_seconds=Period(0),
    owner=owner,
)
receipt = clone.send(ctx)
instance = factory.decode_clone_receipt(receipt)
vault_address = instance.plasma_vault
access_manager_address = instance.access_manager

# 2. Roles. The clone grants `owner` only OWNER_ROLE. ATOMIST first: it administers the rest.
am = AccessManager(access_manager_address)
am.grant_role(Roles.ATOMIST_ROLE, owner, Period(0)).send(ctx)
am.grant_role(Roles.FUSE_MANAGER_ROLE, owner, Period(0)).send(ctx)
am.grant_role(Roles.ALPHA_ROLE, owner, Period(0)).send(ctx)

# 3. Market, in this order: fuses -> substrates -> balance fuse.
vault = PlasmaVault(vault_address)
vault.add_fuses([SUPPLY_FUSE]).send(ctx)
usdc_substrate = bytes(12) + bytes.fromhex(USDC[2:])  # address right-aligned in bytes32
vault.grant_market_substrates(IporFusionMarkets.AAVE_V3, [usdc_substrate]).send(ctx)
vault.add_balance_fuse(IporFusionMarkets.AAVE_V3, BALANCE_FUSE).send(ctx)

# 4. Access posture. A fresh clone is private and deposit() reverts until one of these.
am.grant_role(Roles.WHITELIST_ROLE, owner, Period(0)).send(ctx)  # (a) own bot: whitelist the depositor
# vault.convert_to_public_vault().send(ctx)              # (b) outside money: ATOMIST-only, one-way

# 5. Fund and run the first strategy step. The depositor must already hold the USDC.
amount = Amount(1_000 * 10**6)  # raw units: 1,000 USDC at 6 decimals
ERC20(USDC).approve(vault_address, amount).send(ctx)
vault.deposit(amount, owner).send(ctx)
vault.execute([AaveV3SupplyFuse(SUPPLY_FUSE).supply(asset=USDC, amount=amount)]).send(ctx)
```

Notes on the walk:

- `clone` versus `clone_supervised`: same arguments; the supervised variant
  is gated by a maintenance role. Use `clone`.
- The balance fuse above is the `BalanceFuseAaveV3` entry of the registry
  for Base and is verified working on a fresh clone. Vaults already in
  production may register a different one for the same market; to match
  them, read `PlasmaVault(address).read_balance_fuses(ctx)` on such a vault.
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

| Chain | Chain id | `IporFusionFactoryProxy` (use) | `IporFusionFactoryImpl` (reverts) | Vault tooling | Verified |
|---|---|---|---|---|---|
| Ethereum | 1 | `0xcd05909C4A1F8E501e4ED554cEF4Ed5E48D9b852` | `0x14d120C365Debd920eaDf0a7AC31e659Bc58217b` | validated | implementation slot |
| Flare | 14 | `0xE75F3565D67645775351A9886F2b598f07d1860B` | `0x6C1ff6eBf5e92Dbc5591E7cCF455829De9C46579` | factory only | implementation slot |
| Unichain | 130 | `0xC9b4E839e86284C558791C13Bda97999fc49aF63` | `0x73c7CA802403BB21e9608C937c556C04d5eeBAD6` | validated | implementation slot |
| Monad | 143 | `0x62DFfe8bCaaf7960f858a4f7dDC83cc98F04cEDC` | `0x1017f0032460B3c8FBB92Bfd350F708Ff6229137` | validated | implementation slot |
| TAC | 239 | `0x26b5561E2E0e7d7977431E92740903Ef3cB7194b` | `0x7DDd0bBB59eFB4b7791cE5E1Cf1587F083aE4Ef5` | factory only | implementation slot |
| HyperEVM | 999 | `0x6bBc827F34b4e862d15215ABd4AC0E3886181665` | `0x010430cbdE2Bb8ACC48D7aDfbEb99812E83f1156` | validated | implementation slot |
| Botanix | 3637 | `0x7c9857f22D0cc6523C8e88BFDCbf411AB8300fF5` | `0xddb0EFB2a7a5C02e01b5A1f85C0a1110d68EeC26` | factory only | registry only |
| Robinhood | 4663 | `0xDF7A590b60072476E355A8EfF33e4872440bDc7C` | `0x9d897beA78F8747d8f9C45d5734089ca7225AE90` | validated | implementation slot |
| Base | 8453 | `0x1455717668fA96534f675856347A973fA907e922` | `0x378fEb2E96082FB965E2BB922AEE484d07c14014` | validated | implementation slot |
| Plasma | 9745 | `0x70d41759FBF90fB63c88197217cB08F93a1c65CB` | `0x346DA190c60d6101B77DA613a252c7aE25A7a120` | validated | implementation slot |
| Arbitrum | 42161 | `0x134fCAce7a2C7Ef3dF2479B62f03ddabAEa922d5` | `0x86C8Ea8800EB6bc8C846c3e0230178520a0ED721` | validated | implementation slot |
| Avalanche | 43114 | `0xa00b6379833D77fA9C9497b32dae00dF39Ac751e` | `0x0f358Bb3d14AB911dBfAF4d9061097e7F5Cf123F` | validated | implementation slot |
| Ink | 57073 | `0xEC53f69Bd1D991a2F99e96DE66E81D0E42A61D8D` | `0xEF774B9767cC9FC813A2b595BE870A13D5B367C0` | validated | implementation slot |
| Katana | 747474 | `0xc29b8D591d6a3f109Ca7ba384F2e00162866D37B` | `0x4a4De6e86AD0d0546F55B595cA75f55803D940bA` | validated | implementation slot |

A deployed factory is not the same as supported tooling. "Validated" chains are
`SUPPORTED_CHAIN_IDS` (`ipor_fusion.chains`): `fusion vault info`/`validate`
and `fetch_vault_data` run there. On a "factory only" chain `clone()` works,
but the vault tooling raises `UnsupportedChainError` until the chain is
validated. "Implementation slot" means the proxy's EIP-1967 implementation
slot was read on chain and matches the registry's `IporFusionFactoryImpl`;
"registry only" means the pair comes from `ipor-abi` without an on-chain read.
The factory implementations are upgraded in place, so the implementation
column changes with releases while the proxy stays.

Fuse addresses are not in the SDK. Resolve them by registry name and chain
in `ipor-abi` (`SupplyFuseAaveV3`, `BalanceFuseAaveV3`, ...), or with the
hosted server's `fusion_address_lookup(name, chain_id=...)`. The SDK class
for a registry name swaps the parts: `SupplyFuseAaveV3` is `AaveV3SupplyFuse`.

# CHANGELOG

<!-- version list -->

## v3.6.5 (2026-09-03)

### Bug Fixes

- **sdk**: Resolve Morpho Blue per chain and reject unknown market IDs
  ([`eb1e01f`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/eb1e01feec616b1051028c87db910754c1066479))


## v3.6.4 (2026-09-03)

### Bug Fixes

- **sdk**: Substrate decoders for swapper v2, flash-loan tokens and Uniswap V4
  ([`0c98151`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/0c98151e20faa05a9967ff6b25396a66969e364a))


## v3.6.3 (2026-09-02)

### Bug Fixes

- **sdk**: Treat zero-address WithdrawManagerChanged as no withdraw manager
  ([`5d97493`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/5d974932872578ce5c57bbb16a97b83dd5896b6d))

### Testing

- **sdk**: Describe the zero-address withdraw manager regression in SDK terms
  ([`25bdb59`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/25bdb59bb4df90665978beb12680e78554c821c0))


## v3.6.2 (2026-09-01)

### Bug Fixes

- **core**: Use EIP-1559 base-fee headroom for maxFeePerGas
  ([`ebbfe49`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/ebbfe4981ae471f3231a4a9609210d76e67a39bd))

- **sdk**: Decode AAVE_V4 substrates per the deployed AaveV4SubstrateLib
  ([`9438f6b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/9438f6bd69447a1c9af8b1af492cfce17a4bfcd2))

- **sdk**: Reject invalid changelog_since versions
  ([`6fb5ec2`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/6fb5ec2ef234b2fdc83e53416f3fd8fe0ca7964c))

### Continuous Integration

- Bump pypi publish action to v1.14.2, lift the hatchling cap
  ([`b3c6047`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/b3c60473e7a36f5e560a1a3e253cb96fd9bc02ff))

### Features

- **core**: Add external-state NAV propose/confirm + mark_nav
  ([`f124430`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/f1244309659d1040066d77ea14e1f54cdd0ceb4b))

- **core**: Add gas-estimate / build-transaction on Web3Context
  ([`22d4c11`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/22d4c11facfcd10e0157744c769fa12aab21de9d))

- **core**: Resolve external-state executor + NAV proposal hash
  ([`4504030`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/450403093dc8be9994d9e73eef07cf3522aeecb1))

- **sdk**: Add AsyncActionFuse (market 40 enter/exit)
  ([`613ee09`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/613ee0955011538f69bee6cf3769446edcd2223b))

- **sdk**: Add AsyncActionSubstrates encoders (market 40)
  ([`9db48cc`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/9db48ccf7084adcbb841e0b57bfde11cf48a2b64))

- **sdk**: Add EXTERNAL_STATE name for market 50 (RWA alias)
  ([`72a3229`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/72a32294d647a0079610cb0a2d2d8495f725bd1d))

- **sdk**: Add ExternalStateOperationFuse (market 50 enter/exit)
  ([`62dfa39`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/62dfa39f5058ad9027b6284ba247264129cbdc0f))

- **sdk**: Add PriceOracleMiddlewareManager write methods
  ([`c6d4692`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/c6d4692964bf0e4931cf6161c5ff72c5750b3c93))

- **sdk**: Add typed substrate encoders for universal token swapper
  ([`00849b7`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/00849b76de07bfdf2966ad69af41883c9c966623))

- **sdk**: Add updateDependencyBalanceGraphs setter to PlasmaVault
  ([`7a2684e`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/7a2684ee76a9981830e51b7e80a8882c7e47c096))

- **sdk**: Support minAmountOut ABI revision in UniversalTokenSwapperFuse
  ([`fc6fc52`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/fc6fc5262707d35842b47cb91dd999ee19b5b3bb))

### Refactoring

- **sdk**: Share substrate-encoding helpers across fuses
  ([`42fd17f`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/42fd17fddd6abd5dc8049b7e0c19064e901ddaca))

- **sdk**: Unify non-negative validation across fuses
  ([`5ed454d`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/5ed454d2d42cca2e1eca1f7d4335482c13050fc0))


## v3.6.1 (2026-08-20)

### Features

- **sdk**: Name the address-pack substrate decoders
  ([`9b95106`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/9b95106d19bfdc7387679c5c45175389d6ca0ffd))


## v3.6.0 (2026-08-20)

### Bug Fixes

- **mcp**: Report package version in MCP handshake
  ([`d54e490`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/d54e490082e31b67cc48aa9e76d948dc8e0bd333))

- **sdk**: Export MerklClaimWrapperFuse at top level
  ([`0a09488`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/0a0948868043120da1b18c49dfe9f6b343627b4f))

### Build System

- Switch semantic-release to the conventional parser
  ([`833f9ea`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/833f9eac675db9bc70a35b29987e838d680f1a62))

### Continuous Integration

- Restore changelog generation, assert it on release
  ([`32d115d`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/32d115d8862c007d81a3ac50a3bbb23f0b542cb7))

### Documentation

- Regenerate changelog from git history
  ([`01a3a45`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/01a3a4545a5441318a67f470c0578d472cf47250))

### Features

- **cli**: Add a changelog command
  ([`26e118a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/26e118aa125282da4f83358c495df5bd1004b0f7))

- **mcp**: Add a server_info tool reporting version and changelog
  ([`643e0dd`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/643e0dd0b2273ca5208daa80e170ec776e92621d))

- **sdk**: Expose package version and changelog via about.py
  ([`b7f950b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/b7f950be0f1cd7e6ebb9c2372218708f590ca493))

- **sdk**: Expose repository URL from package metadata
  ([`c348611`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/c348611b1f9bdd85e646093776db0a37946af1ed))

- **sdk**: Public substrate decode API with AsyncAction decoder
  ([`3add238`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/3add238db1ad51e0679a6f403a5a914a33ee5452))


## v3.5.0 (2026-07-30)

### Bug Fixes

- **sdk**: Surface eth_simulateV1 error objects as revert reasons
  ([`4d44f5a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/4d44f5a1e0434efce88c184a8564974c18968b7f))

### Features

- **sdk**: Add chain registry with vault-tooling support gate
  ([`e6a881a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/e6a881a9c5d923d17f47d3d9b21ac8120415d276))

- **sdk**: Add MerklClaimWrapperFuse with simulateV1 usage tests
  ([`0785e2a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/0785e2aedbf5ba17ba2b1f5a030ee210879521ce))

### Testing

- **sdk**: Re-pin Merkl wrapper simulation to production state
  ([`e34c2ed`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/e34c2ed1261a3c360c612e2b008c344955277a33))


## v3.4.0 (2026-07-29)

### Bug Fixes

- **sdk**: Require type-defining reads in Morpho/ERC4626 gates
  ([`9212fa4`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/9212fa424188d18f295f7ea9ae54ba85056b63a8))

### Chores

- Remove ticket IDs from committed content
  ([`55155da`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/55155da96dce8afa682131076c6f151c2cd4ed3c))

### Documentation

- **sdk**: List the fee wrappers and the fees block in the README
  ([`aac9f54`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/aac9f54b658caacda13f409dbc9d76dc18ff7947))

### Features

- **cli**: Add a Fees section to vault info
  ([`481f47a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/481f47a8b4b8316c1f90798546a505e07fff21d2))

- **mcp**: Type the fees and withdraw-manager blocks
  ([`0a21a64`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/0a21a644208419afad0855a855db0ac2c12e57f8))

### Refactoring

- **sdk**: Promote WAD_DECIMALS to a public constant
  ([`87c5e5b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/87c5e5bff5a0db7fc1bee91780794144073d45f0))

- **sdk**: Rename foreign_getter flag to has_foreign_getter
  ([`c014b24`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/c014b2492ac0d3a81ad29ac68dee23bc3560c54d))

- **sdk**: Type the oracle-mapping underlying-asset block
  ([`9c154c0`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/9c154c0f9ea4d92a3ec5d5ccb97e88741862670d))

### Testing

- **mcp**: Enforce models.py import-graph rule
  ([`fac11fb`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/fac11fbd24113a1c544cd66fbbe2737b17a2ffbc))

- **mcp**: Whitelist models.py runtime imports explicitly
  ([`43721ae`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/43721ae907fccb3efaca6a0c4bcce8dc173342c7))


## v3.3.1 (2026-07-23)

### Bug Fixes

- **sdk**: Classify DualCrossReferencePriceFeed before Chainlink in oracle mapping
  ([`323bab0`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/323bab06b52d6069e9da05da0cdee5845dee5711))

- **sdk**: Propagate unresolved dependency status in oracle mapping
  ([`995c0ac`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/995c0ace2008957bc6f0e085d15e5b9d03db50d3))

- **sdk**: Report middleware-fallback pricing as resolved in oracle mapping
  ([`23d7827`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/23d7827ae722e718317869b16b2e3084ac91c0f6))

### Build System

- **deps**: Pin pyright exactly at 1.1.411
  ([`18f1544`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/18f154445eebf62f0cfaafb4cb077ae25ab8ad4a))

### Chores

- **sdk**: Inline event-collapse semantics in oracle-mapping docstring
  ([`d959f3e`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/d959f3e6c926350b84d3738227d61fefb67e331a))

### Features

- **sdk**: Add feed description and full round metadata to oracle mapping
  ([`c6c0b4b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/c6c0b4bddfe9aab14645206c7721a07e961f24d7))

- **sdk**: Add UTC twins for oracle-mapping round timestamps
  ([`11e27e9`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/11e27e9c64eafc71f74c08cc1a2a78029fe92aa8))

- **sdk**: Grade Chainlink leaf evidence into confirmed and chainlink_style tiers
  ([`72f2fce`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/72f2fce8063814fe2083f27997d827e088c00532))

### Refactoring

- **sdk**: Make oracle-mapping price null when unreadable
  ([`6c497e3`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/6c497e30d2163e783fe946324f8cff977ce439ac))

- **sdk**: Rename answer_decimals to decimals in oracle mapping source_detail
  ([`c405a21`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/c405a21fe87f99a64d5208e2a0082419c6b7672d))


## v3.3.0 (2026-07-17)

### Build System

- Refresh uv.lock in the semantic-release version commit
  ([`3dfd0f4`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/3dfd0f469ac18209143ff95807b4352031b58b13))

### Chores

- Refresh uv.lock after 3.2.0 release version bump
  ([`78fcc1b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/78fcc1b1da3e9630f1809b3f5caa1818d0728e1a))

### Features

- **cli**: Add vault oracle-mapping command
  ([`516086e`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/516086e6eb990f204959bbb3e7f61558419772f0))

- **mcp**: Add vault_oracle_mapping tool
  ([`5af2e89`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/5af2e890142ec4fbf671f3976cafff9fd9d0993d))

- **sdk**: Add oracle-mapping reader (port from ipor-fusion-dev)
  ([`37c86bd`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/37c86bdc306db4c002528febbb59cd8dbb7b63d3))


## v3.2.0 (2026-07-16)

### Bug Fixes

- **cli**: Friendly errors for role scans, validate --role before RPC
  ([`11d9e6e`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/11d9e6e4258053fb0e67d192e7d86931f0e3fee6))

- **cli**: Probe vault info target via resolve_access_manager
  ([`380e7e3`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/380e7e3fa59d96409061218ef5dca2fb3581aa23))

- **core**: Dedup role accounts by (role_id, account)
  ([`5e2cd2f`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/5e2cd2f104205394f9a5e96cbc9ce557b50e3519))

- **core**: Make resolve_access_manager guards typed and block-consistent
  ([`dfd6a72`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/dfd6a721efd809ada12cfb5bb1edfc0c3c71054c))

- **deps**: Declare eth-utils (imported directly)
  ([`8cadcb8`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/8cadcb871c6ad544126680ff8fb4cba74ea1ccf0))

- **mcp**: Add criticals to HealthCheck model
  ([`efcede8`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/efcede8ed72f9f742584a0ae106cdbcc6d73e4f7))

- **mcp**: Adopt typed guard errors in vault_info
  ([`f46fde1`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/f46fde1c68196ea114aa073fbac07aff0040b35f))

- **mcp**: Move vault_role_accounts arg docs into inputSchema
  ([`0e48a35`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/0e48a3516f6f4a1162aa344e6fe8977aa74236ba))

- **mcp**: Sync VaultInfoResponse with vault-info dict builder
  ([`049ca75`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/049ca7515176a0e48bdc26434289970d5212b759))

- **mcp**: Use resolve_access_manager probe in vault_info guards
  ([`e5a229a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/e5a229aa83ea17eebcf275a125edab2ab71b2cb0))

- **sdk**: Resync market ids with IporFusionMarkets.sol
  ([`58cb410`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/58cb4102b2349cc4b4ee8b32d4852124e9369e4c))

### Build System

- Add build.sh and enforce locked uv.lock in CI
  ([`7f3dfb4`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/7f3dfb41294152ce2d06c0830e88e891cb44418c))

- Add uv (PEP 621 metadata + hatchling backend)
  ([`7678be9`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/7678be9cd4f0cfeac945743e4bfcba69991f28b3))

- Drop host .venv before in-container semantic-release build
  ([`d2595a6`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/d2595a624b75ed88e3a6dc09b4b349ed8e0b30f5))

- Install uv inside semantic-release container for build_command
  ([`aae879f`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/aae879f9f64ad57d02482e1f1148a900b32f7b65))

- Pin pyright venv for editor resolution
  ([`9b9dc6a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/9b9dc6a3bd435a8aa8d35e2b9be7456aeb3d3a30))

- Remove Poetry
  ([`5ecc14a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/5ecc14a399d2ed4d52fed0b17923784b6bd719aa))

- Replace black + pylint with ruff (config only)
  ([`54bd7af`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/54bd7afd2d96cc06d6779dd3464f8d846a14d3e4))

- Replace mypy with pyright (sync with dev)
  ([`af19eb3`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/af19eb3cca3f4ac99153452e373b2c4207a61991))

- **deps**: Bump eth-typing to >=6
  ([`fd239f3`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/fd239f30a60959b540d4c9ce22506018d3c64a98))

- **deps**: Bump pytest to >=9.0.3 (fix CVE-2025-71176)
  ([`9b78d29`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/9b78d293967427f95c7320e8e9879b06b0df1065))

- **deps**: Bump pytest-cov to >=7
  ([`30a82b0`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/30a82b0a2734291fdc0a23294a9382e26683261d))

### Chores

- Bump mcp to 1.28.1 (GHSA-vj7q-gjh5-988w)
  ([`2137855`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/2137855a8b71aafc59f996b26c7e550d9acc70e7))

- Ignore the ruff reformat commit in git blame
  ([`d4d4d3f`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/d4d4d3f425208734f0be0a6a296aec90edc0504b))

- Remove dead pylint disable comments
  ([`4f1615b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/4f1615bf72a07f49e6a9924c2bdc099e1fa0a117))

### Code Style

- Apply ruff format + ruff check --fix
  ([`e484052`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/e4840522b0789c044c935adb43e5d542d7ecd607))

- Hoist test_mcp_server imports to top, drop noqa: E402
  ([`f416438`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/f416438180e22a66f45c0c9d0f032acb0f9834fd))

- Re-sort imports after rebase
  ([`70a484f`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/70a484f10b629039cd3ccb94c63174c7c6429335))

- Resolve ruff findings (noqa + zip strict=False)
  ([`0ac69ad`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/0ac69add89248fa04ec8d56c4dfa3aa6fa38ef86))

- Ruff-sort imports in euler v2 tests after rebase
  ([`8ec9fcf`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/8ec9fcf2648cacc5675fe78990264651f5ea40e3))

### Documentation

- Drop stale Docker/Anvil test requirement
  ([`46af574`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/46af5743fc9c140f8724f53f4b84651bc8661101))

- Drop stale temp-notes-file mention in comment
  ([`ff44630`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/ff44630f9fc46192cdfdefef69113e6a383a5a88))

### Features

- Expose per-request RPC timeout on Web3Context.from_url
  ([`702e3c4`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/702e3c420ad2563e3e2b8e3728ceb01be0b92b2a))

- **cli**: Add vault role-accounts command
  ([`a332ee8`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/a332ee85ed44a63336a8b197dff307f8daaec4cc))

- **cli**: Include role accounts in vault info
  ([`657acc7`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/657acc7fdc8042554e3e20648f48db781ee8f3ff))

- **core**: Add RoleAccount.role_name, Roles.resolve, Roles.names_str
  ([`1df0cc7`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/1df0cc7e5b581efc6517581e10d3f0924950079a))

- **core**: Add typed vault guards and resolve_access_manager
  ([`e752a00`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/e752a00e0afcd25ce24ee5b2c813a5cdaa97d1af))

- **mcp**: Add role-account models and vault_role_accounts tool
  ([`32699de`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/32699de994dab774194a5ea9809b213ea915e396))

### Performance Improvements

- **cli**: Overlap role-accounts fetch with other vault info work
  ([`46a19cc`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/46a19ccbc462e3889217777edce5bad6e4971815))

### Refactoring

- **cli**: Extract shared command preamble into _build_ctx
  ([`81d7564`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/81d7564a26418318511489e961e9e46bf6307966))

- **core**: Single source for role-account row shape and sort
  ([`4f14819`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/4f1481990d024cceb461e0df88483145fd66e376))

- **sdk**: Rename NotAPlasmaVaultError to NotPlasmaVaultError
  ([`e701dea`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/e701dea29c624f2ca9e64d56a3b9345c42b8d555))

### Testing

- **mcp**: Restore TestSimpleResponseContracts class membership
  ([`4bc6d67`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/4bc6d67f1c43938430e0e849311e01361f5558c7))


## v3.1.0 (2026-06-25)

### Features

- **fuses**: Add EulerV2 fuse suite with Base fork tests
  ([`098e948`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/098e948c295b4dc91a09aa07dc2e57847e7d2f4f))

- **fuses**: Add EulerV2 swap + batch fuses with Base fork tests
  ([`ab1e7b3`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/ab1e7b37891c8d57faef8bdce55e9f380308567c))


## v3.0.3 (2026-06-22)

### Bug Fixes

- Scope missing-ERC20-dependency check to multi-asset vaults
  ([`62ba3be`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/62ba3be12a3cec37378579aa1a0fc6d62ebd0d6a))

- **cli**: Annotate withdraw_manager_details fees as mutually exclusive
  ([`d849eb3`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/d849eb30b4b8a03c24667ba0e59d4ca1801fdd69))

- **cli**: Make vault_info block a plain int, split out is_latest flag
  ([`64fcb70`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/64fcb70b4f214405edb8a4a7bdb152d39b826b10))

- **cli**: Split balance fuses into venues and zero-balance capabilities
  ([`1c3e7bf`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/1c3e7bff46791a6b017078d85652d57e8bed9dc3))


## v3.0.2 (2026-06-19)

### Bug Fixes

- Python-semantic-release/publish-action hash
  ([`978d4ec`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/978d4ecefe4a81f6ecdac15b85c634884a6cf040))

- **cli**: Count idle underlying in vault info reconciliation (IL-7463)
  ([`da9acda`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/da9acdabb637d77dfb66a57d840e3937a9eea427))

### Features

- Remove develop trigger from CD workflow
  ([`9ed2cc4`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/9ed2cc40ade140d999e4f272bb2a3e4b46430b7b))

- Update GitHub Actions steps
  ([`f59891e`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/f59891e468ae16c48909d2b05bf4186f67379a74))

### Refactoring

- **cli**: Drop redundant underlying-balance assignment in erc20 loop
  ([`4a4fd03`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/4a4fd0326e135c6d3f82d7d14577947ba747ba6d))

### Testing

- **e2e**: Simulate a PlasmaVault from scratch on BASE
  ([`62490f1`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/62490f134d60a00af389fe2242406f6910ba54a2))


## v3.0.1 (2026-05-26)


## v3.0.0 (2026-05-26)

### Bug Fixes

- **core**: Checksum addresses in FusionInstance decoder
  ([`175b20f`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/175b20f3d1a22d58e3bb9d582ac835144743a431))

### Code Style

- Apply black formatting to FusionFactory + clone tests
  ([`175b20f`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/175b20f3d1a22d58e3bb9d582ac835144743a431))

- **tests**: Hoist imports + appease pylint no-member on ChecksumAddress
  ([`175b20f`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/175b20f3d1a22d58e3bb9d582ac835144743a431))

### Features

- **sdk**: VaultSimulator (eth_simulateV1) + lazy Call[T] wrapper API
  ([`42606b6`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/42606b68df53fdaea349a9db312174b3f672594a))

### Refactoring

- **core**: Expose Call.calldata, drop encode_*_calldata statics
  ([`175b20f`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/175b20f3d1a22d58e3bb9d582ac835144743a431))


## v2.2.0 (2026-05-08)

### Bug Fixes

- **cli**: Decode EULER_V2 substrates as address<<96 + flags
  ([`b246920`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/b2469202ddacaf604916313b50a1fda272b4a963))

### Features

- **cli**: Surface deployment lookup errors with structured codes
  ([`9976fb2`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/9976fb2a094380706c1f40eb6c1fd6766df8cade))

- **cli,mcp**: Migrate config_store and MCP tool I/O to Pydantic v2
  ([`578dc1b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/578dc1bfec6663698985d059976197723efbc947))

- **cli,mcp**: Orphan-fuse detection + morpho-blue market explorer
  ([`582dc71`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/582dc7193c278f19a4f29b3333a0a3c4f5d80739))


## v2.1.1 (2026-04-16)

### Bug Fixes

- **cli**: Deduplicate balance fuse totals and add pending withdrawal tracking
  ([`8a25f35`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/8a25f3524ccbfb959ce74ebc747f78fd4c6f8794))

- **sdk**: Chronological event replay in get_balance_fuses
  ([`6254d38`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/6254d385d3cf5ff254879b6fd4fe735d258794a9))

- **sdk**: Net balance fuses by Added/Removed events and deduplicate per market
  ([`e865154`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/e8651546b72efdaf8d30c5421c27151d8b124151))

### Chores

- Fix lint, typing and tests after balance-fuse netting
  ([`7d64b3b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/7d64b3bad3907cc39cb547c3cfbc8fb32004d1b3))

### Code Style

- Apply black formatting
  ([`576982c`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/576982c5fee0f76ae0f88b9fa67ab80ba6a12470))

### Documentation

- Use pipx for CLI/MCP install, make MCP section agent-agnostic
  ([`336b3ab`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/336b3abf96de9c5e1460edcbc8997696b221c669))

### Features

- **cli,mcp**: Per-market position breakdown for Morpho and Aave V3
  ([`8c951fb`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/8c951fb0f4f9b633c436d6ecb12afe8188338d37))


## v2.1.0 (2026-04-14)

### Bug Fixes

- **ci**: Bump pypi-publish to v1.14.0 for Metadata-Version 2.4 support
  ([`8f9dd56`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/8f9dd5698cd0b55b9acfdd0db4d845415e0e79e2))

- **ci**: Disable attestations for pypi-publish v1.14.0
  ([`7f74c54`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/7f74c544dd36b027e94119f8f1d2ecdc8310f74f))

- **ci**: Resolve pylint exit code 8 causing CI failure
  ([`8813f74`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/8813f746dcea85619778fb57127a6e7c76208a22))

- **ci**: Use commit SHA instead of tag object SHA for pypi-publish
  ([`ecad29e`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/ecad29e20d92db15fbe1ebc095c9f8822c524bdf))

- **cli**: Reconciliation double-counting non-underlying ERC20 tokens
  ([`d461a6b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/d461a6ba9b9ad1ce6e73ccd364052ecb1a345a7c))

### Features

- Add CLI and MCP server for Plasma Vault inspection
  ([`093bb4d`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/093bb4d14be0eec12bfb80c9047b998892e067cd))

- Links & deployment block number
  ([`7e9d2fb`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/7e9d2fbf91f944b778fd6539becd47bfbb731ec2))

- **cli**: Add dependency balance graph to vault info
  ([`236e006`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/236e006c31ce79bb6ff976c041b9f6472aab9f82))

- **cli**: Add lending health monitoring and clean up CLI surface
  ([`14c9dfe`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/14c9dfe8b5197e5d81d01cf549920eb10c474b00))

- **cli**: Add share price to vault info output
  ([`8c74d6b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/8c74d6b7814f39a5b95beeb677c6eea34a5e34d4))

- **cli**: Add update reach and update groups to dependency graph output
  ([`a65ec74`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/a65ec7473fe74a11b7f6e049d42efee88636d1ad))

- **cli**: Add vault market-detail command for single-market deep-dive
  ([`329116b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/329116bb7dff95acb1e2469fe73e54d63e2b5b01))

- **cli**: Add verbose/quiet, no-color, shell completion and alias help
  ([`4226b29`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/4226b29e10f6e5e2f876903141c94a56cf718965))

- **cli**: Per-market substrate decoders and vault name in info output
  ([`cba3e27`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/cba3e27509c73366af6d4ab0978ca0603ba66afa))

- **cli**: Show pending withdrawal requests and fix Morpho substrate display
  ([`599b1cf`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/599b1cfd8c9686b174cb3b5760391e23d9ca5e85))

- **cli**: Validate config schema, add versioning, narrow _safe_call exceptions
  ([`63111c7`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/63111c7f52c9692036504ec76521bedafd504852))

- **mcp**: Expose all CLI commands as MCP tools with detailed docstrings
  ([`8619dca`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/8619dcae884641f19ed172bf3dbd0318eaf76298))

### Refactoring

- **cli**: Positional vault address, remove default_vault, auto-save on info
  ([`85782d3`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/85782d38371b264b812fab73f2460b00f2b15dd4))

- **cli**: Split vault_cmd.py into focused modules
  ([`1d05c0a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/1d05c0a2596c38755060af93f99e4626df1d3c33))

- **mcp**: Remove unnecessary __future__ annotations import
  ([`893daa1`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/893daa13fdcad9922b4ba904ef62cfe3e68ed210))

- **mcp**: Replace subprocess CLI calls with direct SDK imports
  ([`4418f93`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/4418f93312fd0cbe7771193457e6ce21d6842f9e))

### Testing

- Add tests for share price, ChainType, and _fetch_getsourcecode
  ([`55d0a14`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/55d0a14527e56b1805de683c1f7ab3d9a51159fd))


## v2.0.0 (2026-03-23)

### Bug Fixes

- Add missing mypy ignore for testcontainers import
  ([`492d0d3`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/492d0d32ccddce7c6d0c9c14e4d4afbcdab5f8e6))

- Install testing extra in CI workflows
  ([`9162941`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/9162941226136b6685071b3eb1365dcb242d1e15))

- Regenerate poetry.lock to match pyproject.toml
  ([`0d446b8`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/0d446b8829b14a23d5024c5cb0d6f38f1da8d123))

- Replace os.getenv with os.environ in tests to fix mypy str | None errors
  ([`3c8c959`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/3c8c959a11392c31b33969a82e8a9643ef3d0b4c))

- Resolve all mypy errors and add type checking to CI
  ([`5909d7a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/5909d7aeb892ea9f3dac44bbecf652e122d62b0a))

- Resolve pylint warnings (unused vars, walrus operators, signature mismatch)
  ([`c6ed85b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/c6ed85b51678de19e6ee8fc90d0c6c1e898ca7e9))

- V2.0 cleanup — keyword-only args, naming, DRY readers, slots
  ([`4e085bc`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/4e085bc91d456a0f7aa37965f9741553b9c7e064))

- Wrap block number literals with BlockNumber type in tests
  ([`c0eb13b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/c0eb13b30e5e011bc2b7d6ec04745a6114e6a476))

### Chores

- Remove duplicate dead code files before v2.0
  ([`b121638`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/b12163872df045dfabc3d4fb11f7f66955bd722f))

### Continuous Integration

- Add skip-version-bump option to release workflow
  ([`54439a9`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/54439a94f02d52050a4e1a169df1d8cd5990361a))

### Documentation

- Add DeepWiki badge to README
  ([`99fc96e`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/99fc96eeb679145564e5834c1292f418ab9744c0))

- Add minimal docstrings to public API surface
  ([`0fd90e2`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/0fd90e2281350d80a0507c514bcc67d351ee4366))

- Add minimal docstrings to public API surface
  ([`4488317`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/44883174a75c4f8011e1459c1e3e7c013465ab74))

### Features

- Add edge-case validation and document test addresses
  ([`53e64c7`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/53e64c78850fb7b07d258f6672b6e771d53884d2))

- Add input validation for amounts and addresses on fuse methods
  ([`c91446c`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/c91446c1741119c9c8846427e2c922dd9e57c3cc))

- Add protocol-specific reader helpers for on-chain state queries
  ([`1cc48de`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/1cc48de8ecdf3156a6b81edda7f815a092704868))

- Add test coverage reporting to CI workflow
  ([`a267e80`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/a267e8066c82488bdd85217e7302f60abbc80a9b))

- Add UniswapV3 event extraction utilities
  ([`afb407a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/afb407adb371c18181bc18df8124d45a68eca29f))

- Decode EVM revert reasons in TransactionError
  ([`7c431e6`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/7c431e6c09c704964a4548dc5df786974f5fadfe))

- Sync __version__ with pyproject.toml via importlib.metadata
  ([`739c574`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/739c574336d5f10e26e8c254ae0497c8000a77b9))

- Sync IporFusionMarkets and Roles with Solidity contracts
  ([`23a3c8a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/23a3c8a405ca069982c0cdf8066e8d95577c10f2))

- V2.0.0 — keyword-only args, public API cleanup, DRY refactoring
  ([`92f6377`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/92f63775bf2b9b8c6ce880ceed41d93e9b6b3756))

- V2.0.0 — keyword-only args, public API cleanup, DRY refactoring
  ([`5bdfc64`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/5bdfc64675b231e6e1f1fecf2a9fba17f25d9c5d))

### Refactoring

- Apply Amount NewType consistently in fuse method signatures
  ([`0096bdf`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/0096bdf1320bb04ea89036f9244ffde60efbde3b))

- Batch changes
  ([`e615937`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/e6159377d826d5a60a6a805b15cfaae269170413))

- Convert AnvilTestContainerStarter to context manager and use pytest fixtures
  ([`2b8a07a`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/2b8a07a227769800d79aa848b22f629a029a0eb6))

- Convert Price to dataclass and fix Period constants to return Period instances
  ([`915396c`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/915396ca737bb9f93e5b62c1ceebf0f3052e225a))

- Extract ContractWrapper base class from core contract classes
  ([`6203202`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/620320272921e848b5e5880a3cd2d8c026cd1443))

- Extract StakeFuse base class to deduplicate stake fuses
  ([`7c596aa`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/7c596aa7062a404a00d23a80a86e1e2a53830395))

- Make Web3Context public attributes private with property accessors
  ([`8bbef5f`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/8bbef5fec498c4d0874088fb09a50b9a3627ecc5))

- Make Web3Context.private_key a private attribute
  ([`dde0435`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/dde04354fdd28625fc3bd4020f523216ee8ef72e))

- Migrate all fuses to use Fuse._action_raw() for encoding
  ([`78793fe`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/78793feae2fda4cc204239acf1406d3e72ff501e))

- Move addresses.py from SDK to tests
  ([`093a12d`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/093a12df59cdc7de476c5861ee112c1014c5b4c1))

- Prepare API for v2.0 release
  ([`4a3b058`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/4a3b0580c68140cd88a2e2eb02ff76a3ce6e8522))

- Remove dead error classes from public API
  ([`23cbb82`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/23cbb82f1f95f0b80d9ab8d22b0861c37f536814))

- Remove dead pylint format config and fix BlockNumber type annotations
  ([`b59b264`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/b59b264f905a1e612a94fc60111368799c6ce04d))

- Remove markets/ abstraction layer in favor of direct fuse API
  ([`4ea0bc9`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/4ea0bc940a75141c8cc9480f077df5f92c27fbbf))

- Remove unused ERC20Token class from erc20.py
  ([`ecab344`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/ecab344a0b24090f199d676f8eafda5eaf749d84))

- Rename Erc4626SupplyFuse to ERC4626SupplyFuse
  ([`83fb6e1`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/83fb6e111266687edb57794cf7a62c609ec32069))

- Replace bare int with domain NewTypes across public API
  ([`11478ea`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/11478eaf09ec9488b36c880b2eda41f204742c61))

- Replace tuple and dict returns with dataclasses
  ([`a169d07`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/a169d07f30ab781ad126338dc2ed369b03d38267))

- Split FluidInstadappSupplyFuse into separate supply and staking fuses
  ([`ad9b679`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/ad9b67978325571f0b75c4b683fa806ceda098ba))

- Split GearboxSupplyFuse into separate supply and staking fuses
  ([`2491bbd`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/2491bbda9d327481697cb08616f8f72edf3f3224))

- Unify typing to Python 3.10+ builtins (list, X | None)
  ([`aa6fa31`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/aa6fa31ac14ae25ad00db516a83feb9dd13b148f))

- Unify typing to Python 3.10+ builtins (list, X | None)
  ([`7e329d5`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/7e329d581a8d0420f4f13ebc93502b452fdd360e))

### Testing

- Add pytest-xdist for parallel test execution
  ([`74f19a4`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/74f19a4247873ec7f92c0b2d3f9092213dfbbce0))

- Add unit tests for core modules to raise coverage from 89% to 97%
  ([`372eb1c`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/372eb1c1415cc27ed38490d86c0b4f7dd1a8ca64))

- Add unit tests for fuse encoding (no Docker needed)
  ([`734c085`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/734c0850ee05ad86652fe4aa95953148289ffd5f))


## v0.24.0 (2026-03-03)


## v0.23.0 (2025-08-19)


## v0.22.0 (2025-08-11)


## v0.21.0 (2025-08-05)


## v0.20.0 (2025-07-24)


## v0.19.0 (2025-07-24)


## v0.18.0 (2025-06-12)


## v0.17.0 (2025-06-10)


## v0.16.0 (2025-06-06)

### Features

- Morpho Flash Loan
  ([`cb061d4`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/cb061d446b2e58b013d7549debaf6d0b0b39a32c))


## v0.15.0 (2025-06-03)

### Features

- Add Aave V3 Borrow
  ([`1229c0c`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/1229c0c9f7ff7b83610b13eff93ac4dcbbe67691))


## v0.14.0 (2025-05-05)

### Features

- Refactor
  ([`6b26f0b`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/6b26f0b9294d9d87ccaace2c47061b4faad80aee))


## v0.13.0 (2025-04-29)


## v0.12.0 (2025-03-17)


## v0.11.0 (2025-02-19)


## v0.10.0 (2025-02-07)


## v0.9.0 (2025-02-07)

### Features

- Aave_v3 improvements
  ([`824dc59`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/824dc5900afb1c49047bdab6c14b0aeeb370ea6d))

- Checksum addresses
  ([`f0beec3`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/f0beec3889b07bac0a172c22a34dfaaf8d247e36))

- Get_instant_withdrawal_fuses
  ([`9bb73e9`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/9bb73e9f6a0a9ca9ac5888e386daebf961283e66))

- Lazy loading
  ([`ab39cee`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/ab39cee15283d5ae6d46533efe5ce339c690331d))

- Modify SDK to handle the new scheduled withdraw
  ([`dc9e4a6`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/dc9e4a643745dfa0697a0682944f3f6168964929))

- Optional withdraw manager
  ([`06081a1`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/06081a1a7cd1b7829611fe5d1c68b8b7c6e74b14))

- SDK - add support for Morpho blue
  ([`97a3b0d`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/97a3b0d001fd793385e5655d6dd782d3ba3e7b2a))

- Update foundry image
  ([`95e1323`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/95e1323ac2cc97c54093a06ab5ab545a77d1d4a3))


## v0.8.0 (2025-02-04)

### Features

- Update base fuses
  ([`23976da`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/23976daedd5ebbcddc2e7c96e0131e956c72917f))


## v0.7.0 (2024-12-30)

### Features

- Multichain support
  ([`89abd54`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/89abd5414d7d0ff126662f285f8d90545983fc8b))


## v0.6.0 (2024-12-20)

### Features

- Release funds
  ([`a8f6cfd`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/a8f6cfd63011c21f8e73219235857e45b7d3a91f))


## v0.5.0 (2024-12-10)

### Features

- Add new uniswap v3 new position fuse
  ([`0f2d6aa`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/0f2d6aa3972fbdbd88d1453e6eafee761acfb36d))

- Check vault
  ([`ca8d62e`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/ca8d62e83fa1c8915837625b6f329377fab84afb))

- Get accounts with roles
  ([`8896ee6`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/8896ee668abad5be71c21ed6af17bf2cda77abd6))

- Get_accounts_with_roles
  ([`b22a729`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/b22a7294e8d154041f5e1f930da462114d9660c3))


## v0.4.0 (2024-11-26)

### Features

- Anvil configuration
  ([`07f4559`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/07f455998665792ba1af5c2a4a1a650b520740a8))


## v0.3.0 (2024-11-21)

### Build System

- IL-4982 Add semantic versioning in release workflow
  ([#33](https://github.com/IPOR-Labs/ipor-fusion.py/pull/33),
  [`dd95b26`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/dd95b26ba8b52c044bb2223d1f79d5d305f9731f))

### Features

- Plasma Vault configuration provider
  ([`709d3b3`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/709d3b3ace7acc279abc2cd1c5d5372705677416))

- Plasma Vault System factory
  ([`94ec149`](https://github.com/IPOR-Labs/ipor-fusion.py/commit/94ec1496504fec72c5df36071c3de67bf4a2933d))


## v0.2.0 (2024-10-29)

- Initial Release

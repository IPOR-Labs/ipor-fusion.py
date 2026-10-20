# AGENTS.md

`ipor-fusion` is the Python SDK for IPOR Fusion Plasma Vaults: typed fuse
encoders, on-chain readers, and a `fusion` CLI plus `fusion-mcp` MCP server
built on the same SDK. Published to PyPI. It is a library and inspection
tooling, not an automation service. `scripts/crosschain_readiness.py` is a
read-only diagnostic run on demand, not a keeper or production monitor; any
scheduled run of it belongs to the monitoring repository.

Related repositories (siblings, referenced by name; clone paths vary):
- [ipor-fusion](https://github.com/IPOR-Labs/ipor-fusion) — Solidity contracts, the source of truth for market ids, roles and substrates.
- [ipor-abi](https://github.com/IPOR-Labs/ipor-abi) — deployed addresses and ABIs per chain (`mainnet/mainnet-<chain>-fusion/addresses.json`); the place to look up fuse, factory and manager addresses.
- [ipor-fusion-alpha-example](https://github.com/IPOR-Labs/ipor-fusion-alpha-example) — end-to-end SDK usage patterns.

The canonical machine-readable overview of IPOR Fusion for AI agents is
https://ipor.io/llms.txt (with https://ipor.io/llms-full.txt inlining the
docs). Fetch it once when a working session first needs protocol context and
reuse that copy for the rest of the session. Fetch it again only when the user
asks for current information, there is evidence it changed, or a discrepancy
must be re-verified. Do not fetch it merely because a new turn starts or for
repository-local work that does not require protocol context.

## Commands

```bash
uv sync --all-extras --locked                                 # install, same as CI
uv run ruff format ./                                         # format (CI runs --check)
uv run ruff check ./                                          # lint: bandit S, C901 max-complexity 10, PLR limits
uv run pyright                                                # types, basic mode, src + tests
uv run pytest                                                 # all tests + coverage gate (fail_under = 95)
uv run pytest -m "cli or mcp" --no-cov                        # fast offline subset
uv run pytest --run-readiness -m readiness --no-cov -rA       # opt-in live Ethereum-hub POC checks
uv run python scripts/crosschain_readiness.py --output /tmp/crosschain-readiness.json # live Arbitrum/HyperEVM report
uv run pytest tests/test_fuse_encoding.py -k aave --no-cov    # single file / test
uv lock --check                                               # uv.lock in sync with pyproject.toml
```

Run format, check, pyright and pytest after every code change; that is exactly
the sequence in `.github/workflows/python-build.yml`. When ruff `C901` trips,
extract helpers, never suppress.

**Offline vs network.** `test_cli_*` and `test_mcp_*` need no network
(auto-tagged `cli` / `mcp` in `conftest.py`; everything else is `sdk`).
Integration tests (`test_simulate_*`, `test_vault_info_onchain_*`) run against
live RPCs via `eth_simulateV1`, no Docker or Anvil (`ANVIL_WALLET` is just a
named address constant). They auto-skip unless `ETHEREUM_PROVIDER_URL`,
`BASE_PROVIDER_URL` and `ARBITRUM_PROVIDER_URL` are set (`.env` is loaded via
python-dotenv) and the provider supports `eth_simulateV1`. CI has all three as
secrets. Never print `.env` or a provider URL: they embed API keys.
`HYPEREVM_PROVIDER_URL` is used by the HyperEVM lifecycle/deployment tests and
the readiness script; general PR CI does not supply it.
`test_simulate_crosschain_hyperevm.py` rehearses Arbitrum → HyperEVM on pinned blocks,
parametrized by `generation`: `v2` and `v3` run the live factory pairs (`FactoryPair`:
address, pins, creation-code hashes, route and dispatcher deployment gas limits) with
executors from the creation code they store; `source` builds a factory pair from
`FUSE_SOURCE_REVISION` inside the simulation. All need `IPOR_FUSION_CONTRACTS_DIR` at
that revision (the fuses are compiled from it); a checkout without `node_modules` also
needs `IPOR_FUSION_FOUNDRY_REMAPPINGS` for OpenZeppelin. `test_crosschain_ccip_events.py`
mirrors the CCIP event registry from a checkout at its own `CONTRACTS_REVISION`.
`test_solidity_compiler.py` uses `IPOR_FUSION_CONTRACTS_DIR` for a local contracts
checkout and optional `IPOR_FUSION_FOUNDRY_REMAPPINGS`. These tests skip when their
external prerequisites are unavailable.
`test_simulate_crosschain_lifecycle.py` drives the mainnet POC deployment from
Ethereum to every spoke on every transport through `CrosschainLane` and
`CrosschainSimulator`; the fixtures (`Chain`, `Deployment`, `Spoke`, pinned
blocks) live in `tests/_crosschain.py`. A planned spoke (HyperEVM) is a
`pending` chain there and an `xfail(strict=True)` param until it is wired.
`test_simulate_crosschain_canary.py` runs the same lifecycle on `CANARY` and
`CANARY_V3`, the live Arbitrum → HyperEVM USDC canaries on the pilot-v2 and v3 CCIP
factory pairs, each pinned right after its last transaction, then checks the final gates and every delivery's receiver
gas against its route limit; a `Chain.simulated_gas_limit` lifts HyperEVM's 3 M
small-block pin to 30 M and `Deployment.ccip_debits_sent_amount` /
`attestation_zero_dust_sd` carry the executor generation's accounting into `Run`.
It needs `ARBITRUM_PROVIDER_URL` and `HYPEREVM_PROVIDER_URL`.
HyperCore (market 55) cannot be simulated directly: the HyperEVM RPC ignores code
overrides at the precompile addresses `0x800`–`0x813`, and the public Hyperliquid RPC
has no `eth_simulateV1`. `tests/_hypercore_shadow.py` therefore compiles the HyperCore
fuses and pre-hooks from `IPOR_FUSION_HYPERCORE_SOURCE_DIR` (a contracts checkout whose
executable code matches the deployed set) with only `HyperCoreLib`'s precompile and
CoreWriter constants redirected to ordinary addresses, proves the rest is identical to
the deployed runtimes, and `tests/_hypercore_model.py` answers those addresses through
`VaultSimulator.with_state_override_provider`. Core never applies anything there: the
tests prove EVM validation under supplied Core state. `test_hypercore_simulation.py`
runs one vault cycle; `test_simulate_crosschain_hypercore.py` runs the composed flow
(v3 canary hub → CCIP → the market-55 vault → a trade → recall → claim) and holds every
CCIP delivery to its route gas limit. Both skip without the source directory and both
RPCs.
`test_crosschain_readiness.py` checks the Ethereum-hub POC's live bridge and IPOR
preconditions. Its `readiness` marker requires `--run-readiness`: ordinary runs
exclude it even with `-m sdk`. Known HyperEVM gaps are non-strict xfails restricted
to assertion failures, so availability improving is an XPASS, not broken CI.
Readiness RPC clients fail on missing/unreachable providers or wrong chain IDs;
they pin a snapshot per chain and do not require `eth_simulateV1`.
`scripts/crosschain_readiness.py` separately reports both Arbitrum/HyperEVM USDC
directions and the v3 factory pair's configuration (creation codes, routes, the
dispatcher deployment gas limit, the canary creator's allowance, balance and HyperEVM
big-block flag) with snapshot block numbers/hashes/timestamps. `hyperevm_delivery`
flags manual execution into HyperEVM per message kind: `manual_execution_required` for
commands and recalls (the hub route's `messageGasLimit`) and `dispatcher_deployment_manual`
for the one-time deployment ticket. A delivery above the 3 M small block is manual
unless every Chainlink executor seen delivering on HyperEVM's CCIP 2.0 OffRamp over
the last ~2 weeks (`chainlink_executors`: `ExecutionStateChanged` senders from every
source, the canary EOA excluded, at least 20 executions) sends into big blocks; a
ticket below a dispatcher creation's gas is manual regardless. The executor set is
derived on every run; `eth_usingBigBlocks` is read at the latest block.
Blocked availability is a successful observation, not acceptance; incomplete probes
fail. There is no GitHub workflow for it in this repository: run it locally with
the Arbitrum and HyperEVM provider URLs from `.env`. A `Spoke` declares which transports reach it;
the lifecycle matrix follows that.

## Conventions

- Commits: conventional (angular) subjects with a scope, as in `git log` —
  `feat(sdk):`, `fix(sdk):`, `refactor(sdk):`, `feat(cli):`, `feat(mcp):`,
  `test(sdk):`, `ci:`, `build:`, `chore:`, `docs:`. Mandatory for every
  commit that reaches `main`: `python-semantic-release` derives the version,
  the release notes and the dev builds from them. A change that ships
  behavior under `chore:` or `refactor:` gets no dev build and no release; a
  breaking change with neither `!` nor a `BREAKING CHANGE:` footer gets a
  minor or patch bump. A revert takes the type of what it undoes
  (`fix(sdk): revert ...`); git's default `Revert "..."` subject is rejected
  and invisible to versioning. With a squash merge
  the PR title becomes the commit subject, so it follows the same format.
  `commit-lint.yml` fails a PR whose commit subjects or title break it. Never
  hand-bump `project.version`; the release workflow stamps it and re-locks
  `uv.lock`.
- This repository is public, and commit messages also ship in `CHANGELOG.md`
  inside the wheel. No issue-tracker keys (`IL-` plus a number), internal hosts,
  dashboard links or private repository names in commits, branch names, PR
  text or code. `scripts/commit-check.sh` enforces both rules: as a
  `commit-msg` hook plus a check of staged lines (`uvx pre-commit install`
  installs both; a clone that ran it before must run it again) and in
  `commit-lint.yml`. Its built-in patterns cover only issue keys; the rest are
  read from the `LEAK_PATTERNS` secret in CI and from
  `~/.config/ipor/leak-patterns` locally, one extended regex per line.
- Releases: a stable release is cut by a manual run of `release.yml`. Every
  push to `main` that carries a `fix`/`perf`/`feat`/breaking commit since the last
  tag also publishes a dev build of the next version to PyPI (`release.yml`
  on push): `<next>.dev<commits since the last tag>`, e.g. `3.17.0.dev4`,
  with no commit, tag, changelog entry or GitHub release. uv skips it unless
  it is pinned exactly (`ipor-fusion==3.17.0.dev4`); pip also takes it when
  no stable release satisfies the specifier (`>=3.17` before 3.17.0). `fusion
  changelog` has no section for a dev version.
- Release notes render only the commit subject; body prose is dropped. Two
  footers survive: `BREAKING CHANGE: <what breaks, what to do instead>` is
  required whenever the subject carries `!` (the `!` bumps the version but
  renders nothing), and `NOTICE: <what the reader must do>` is for changes that
  need action (a migration, a new env var or config key). Omit it otherwise.
- No AI attribution trailers or "generated with" lines in commits or PRs.
- Branch and open a PR; `main` only moves through PRs and the release workflow.
- Dependencies: `>=x,<next-major` ranges in `pyproject.toml`, resolved by
  `uv.lock`; `pyright` is pinned exactly.
- English only, US spelling, in code, comments, docstrings and user-facing
  strings (`field_docs.py` ships as API documentation). Comments only for edge
  cases and non-obvious logic.
- The public API is `ipor_fusion.__all__` (README, "Public API and stability").
  Every public symbol is imported and listed there; a symbol of a public
  subpackage (`core`, `config`, `fuses`, `readers`, `crosschain`,
  `crosschain.fuses`, `crosschain.ccip`, `crosschain.stargate`) also goes in that
  subpackage's `__all__`. `ipor_fusion.guide` keeps its own namespace. The
  public modules (`about`, `cache`, `chains`, `dependency_graph`, `errors`, `explorer`,
  `field_docs`, `market_ids`, `market_info`, `morpho_api`, `substrates`, `types`,
  `validation`, `vault_data`, `vault_info`) stay importable for the names they
  define and the moved names README lists; `cli`, `mcp`, every other module and
  every submodule of a public subpackage (`core.contract`, `fuses.base`, ...) are
  internal. Examples, the guide, the skill and `scripts/` import from
  `ipor_fusion`, a public subpackage or a public module, never an internal
  path. Adding or removing a
  name updates the snapshots in `tests/test_public_api.py` (every `__all__`, and
  the names each public module keeps by path); moving a name out of a public
  module leaves a re-export there. A removal or rename is a breaking change.
  A project type used in a public signature or field is exported too
  (`tests/test_public_type_reachability.py`). The package is `py.typed`, so public APIs stay fully
  annotated.

## Invariants that must change together

| Value | Where it lives |
|---|---|
| ruff version | `uv.lock` dev group and `rev` in `.pre-commit-config.yaml` |
| Python 3.12 runtime, 3.11 floor | `.python-version` + CI `python-version` default; `requires-python`, ruff `target-version`, pyright `pythonVersion` |
| `IporFusionMarkets`, `Roles` | `market_ids.py`, `config/roles.py` mirror `IporFusionMarkets.sol`, `Roles.sol` in `ipor-fusion/contracts/libraries/`; drift-gated by `tests/test_solidity_mirrors.py` — bump its pinned ref in the same change that syncs the mirrors An id sourced from a contracts feature branch ahead of the pinned ref goes in the test's `_AHEAD_OF_UPSTREAM` allowlist and is removed when the ref catches up |
| substrate decoders | `substrates.py` registry mirrors each market's `contracts/fuses/<protocol>/*SubstrateLib.sol` or `*FuseLib.sol` |
| `vault_info` JSON shape | `build_vault_info_json` in `vault_info.py`, models in `mcp/models.py` (`extra="forbid"`), `_full_vault_info_dict` fixture in `test_mcp_models.py` |
| CLI command set | every CLI command has a matching tool in `mcp/server.py` (`changelog` maps to `server_info`) |

## Layout (`src/ipor_fusion/`)

- `chains.py` chain registry; `market_ids.py` `IporFusionMarkets`; `types.py`, `errors.py`
- `about.py` — package version, repository URL, CHANGELOG parsing (`fusion changelog`,
  `server_info`); `field_docs.py` — `DOCS`, the JSON field descriptions shared by CLI
  output and MCP models
- `substrates.py` — public per-market bytes32 substrate decoding (`decode_substrate`,
  `SubstrateInfo`, `format_market_label`, `market_name`; re-exported from `ipor_fusion`),
  used by the CLI, MCP and `readers/lending_health`
- `validation.py` — public vault validation (`validate_vault` fetches and validates;
  `validate_vault_state` is a pure function of a snapshot typed by the `VaultState`
  protocol):
  `VaultValidation` with ordered `Finding`s, an ok/warning/critical verdict, the
  `Reconciliation`, `Erc20Holdings` and lending health; `dependency_graph.py` holds the
  balance-graph checks
- `vault_data.py` — public vault read: `fetch_vault_data` → `VaultData` (with `FeeData`,
  `WithdrawManagerData`), `fetch_deployment_info`, cached `resolve_token_symbol` /
  `resolve_token_decimals`; it composes `vault_reads.py` (the shared Multicall3 batch
  and degrade-to-``None`` helpers), `vault_managers.py` (fee and WithdrawManager
  snapshots), `vault_positions.py` (Morpho/Aave position breakdowns and their token
  prices) and `vault_erc20.py` (the ERC20_VAULT_BALANCE holdings);
  `vault_info.py` — `build_vault_info_json(data)`, the `fusion vault info --json`
  document rendered from the snapshot alone (contract names via an injected
  `resolve_name`),
  and `health_check_lists`; `explorer.py` Etherscan V2 (single endpoint, needs API key);
  `cache.py` the `ContractCache` protocol (`get`/`put` of immutable facts keyed
  `CacheKey(chain_id, address, field)`), `FileContractCache` (one JSON file, atomic
  replace, corrupt/unwritable degrades to memory) and `NullCache`; every SDK use goes
  through `cache_get`/`cache_put` (a failing cache is a miss, never an error) on
  `ctx.cache` (`Web3Context(cache=...)`, default `default_contract_cache()` under
  `$XDG_CACHE_HOME/ipor-fusion/`) or `get_contract_name(cache=...)`. Never cache an
  absence (no code, no answer): it can change at a later block.
  `morpho_api.py` the Morpho public GraphQL API client (`fetch_market`, `fetch_vault`,
  `PUBLIC_ALLOCATOR_ADDRESSES`); `market_info.py` — `build_morpho_blue_market_json`,
  `build_meta_morpho_json`, the `fusion market` JSON documents.
  None of them imports `cli/` (`tests/test_sdk_layering.py`)
- `config/roles.py` — `Roles` IntEnum
- `core/` — `context` (`Web3Context`), `contract` (`Call`, `ContractWrapper`),
  `multicall` (`Multicall3`), `logs` (`get_logs_adaptive`: every event scan pages
  `eth_getLogs` within the provider's caps from the contract's creation block, within a
  time and request budget, and raises `LogScanError` rather than return partial logs;
  `LogSource`: an optional index `Web3Context.get_logs` asks first for a topic0-only
  filter, falling back to the scan on `LogSourceUnavailable`;
  prefer a storage or view read over a scan, as `read_balance_fuses` and
  `read_withdraw_manager_address` do), `plasma_vault`, `access`, `withdraw_manager`, `rewards_manager`, `fee_manager`,
  `simulation` (`VaultSimulator`, eth_simulateV1, `erc20_balance_slot` and ERC-20 balance
  overrides), `oracle`, `fusion_factory`,
  `external_state_executor` (NAV marks for market 50), `erc20`, `fuse_action`
  (`FuseAction`), `fuse` (the `Fuse` base, `StakeFuse`, the shared validators and
  substrate packers), `address` (`_validate_not_zero_address`). `core` imports neither
  `fuses` nor `crosschain` (`tests/test_sdk_layering.py`)
- `fuses/` — per-protocol fuse encoders (aave_v3, async_action, compound_v3,
  erc4626, euler_v2, external_state, fluid_instadapp, gearbox_v3, merkl, morpho, ramses_v2,
  uniswap_v3, universal, `events.py`); `base.py` re-exports the `core/fuse.py` base and validators,
  `FuseAction` and `ZERO_ADDRESS` (from `types`). A fuse is a value: equality and
  hash cover its class and every attribute it stores
- `crosschain/` — crosschain Plasma Vaults (executor on the hub chain, dispatcher at
  the same address on each spoke chain), split like the contracts: shared `messages`
  (`Command`, `MsgType`, envelopes), `contracts` (wrapper bases), `transport`
  (`CrosschainTransport` seam), `lane` (`CrosschainLane`: one executor/dispatcher pair
  driven without knowing its transport; it carries its transport's fuse classes and
  the keeper's attestation helpers), `discovery` (`discover_deployment` reads the
  market grants, `open_lane`/`open_lanes` build lanes; `LANES` is the transport →
  lane registry, the only place that knows every concrete lane), `errors` (the
  contracts' custom error signatures, registered so reverts decode by name),
  `simulation` (`CrosschainSimulator`, a relay over one `VaultSimulator` per chain
  that impersonates the endpoint/router on delivery and funds `SYNTHETIC_TOKEN_SOURCE`
  by a storage override where a chain names no token holder; test and dry-run
  tooling, not something a keeper needs), and one subpackage per transport, `stargate/` and
  `ccip/`, each with its wire codecs, executor/dispatcher/factory wrappers, transport
  and lane; `ccip/chainlink` reads Chainlink's Router, OnRamp, TokenAdminRegistry and
  token pool (`ccip_token_lane`: is there a lane, does the token travel on it);
  `ccip/events` decodes the executor/dispatcher/factory events by topic (one
  registry, mirrored from the v3 source; the v2 pair emits a subset of it);
  `ccip/chainlink` also wraps the 2.0 OffRamp, verifier resolver and committee verifier
  (`CcipOffRamp.execute` is the permissionless delivery) and `ccip/indexer` fetches a
  message's verifier result from Chainlink's public indexers and builds that
  `manual_execution` call.
  The market fuse encoders live in `crosschain/fuses/` (`base`, `stargate`, `ccip`),
  mirroring `contracts/fuses/crosschain/`; they build on `core/fuse.py`, so the
  dependency direction is `core` <- `crosschain` and `fuses/` holds no crosschain code
  (neither package imports the other). Names mirror the Solidity contracts and
  libraries. Adding a transport: a new subpackage, its encoders in `crosschain/fuses/`
  and one `LANES` entry. Layering: the encoders import the wire codecs and wrappers;
  lanes, discovery, transports and the simulator import the encoders.
  `ipor_fusion.crosschain` exports the whole surface (encoders, lanes, discovery,
  transports, simulator). A package `__init__` runs before its modules and the
  encoders import `ccip.contracts` / `stargate.layerzero`, so `.ccip` and `.stargate`
  export only their codecs and wrappers. `tests/test_crosschain_layering.py` pins this:
  the SDK's module-level import graph, with the implicit package-`__init__` edges, is
  acyclic, and the crosschain package has no function-level or `TYPE_CHECKING`
  imports; never work around a cycle with a lazy import, move the module instead.
- `readers/` — read side: lending_health, oracle_mapping, position_manager, aave_v3,
  compound_v3, morpho, ramses_v2, uniswap_v3, unpriceable (middleware-priced tokens of
  granted substrates the oracle cannot price)
- `cli/` — `main.py` root group; `changelog_cmd.py`, `config_cmd.py`, `market_cmd.py`,
  `vault_cmd.py` command orchestration over `vault_data` / `vault_info`; `options.py`
  the shared `--chain-id` / address param types and chain/provider resolution;
  text output in `vault_info_view.py` (`fusion vault info`), `vault_markets_view.py`
  (fuses, balance fuses, substrates, lending health) and `oracle_mapping_view.py`;
  `vault_health.py` renders the validation; `vault_rendering.py` pure formatting;
  `config_store.py` (XDG `~/.config/ipor-fusion/`)
- `mcp/` — `server.py` tools call the SDK directly (no subprocess); `models.py` pydantic output
- Entry points: `entry_cli.py`, `entry_mcp.py` via `[project.scripts]`

## SDK usage model

Two patterns explain most of the code; know both before writing SDK code
or examples.

- `Call[T]` (`core/contract.py`): every `ContractWrapper` method (one per
  Solidity function) returns a `Call` instead of executing. Construction and
  Call building are offline: a wrapper is its address (plus genuine contract
  parameters such as a legacy oracle's decimals) and holds no `Web3Context`;
  a `Call` holds `to`, `data`, output types and decoder, never a context.
  Execution takes the context as a required argument: `.call(ctx)` runs
  `eth_call` (at the context's, possibly pinned, block; `block=` overrides)
  and decodes to `T`; `.send(ctx)` signs and submits, returning a `TxReceipt`;
  `.build_transaction(ctx)` / `.estimate_gas(ctx)` preview without signing
  (default `"latest"`). `.calldata`, `.decode(raw)` and `.map(f)` stay
  offline. A `Call` without `output_types` (a write) raises on `.call(ctx)`.
  The same `Call` feeds `VaultSimulator.observe`, so reads, sends and
  simulations share one definition.
- A wrapper method that must execute to answer (storage, a log scan, a read
  that depends on another read) is named `read_*` and takes `ctx` first:
  `PlasmaVault.read_balance_fuses(ctx)`, `AccessManager.read_all_role_accounts(ctx)`,
  `MorphoReader.read_rates(ctx, id)`. Decide by the I/O a method performs, not
  by the class name. Resolvers that probe the chain to pick a wrapper
  (`price_oracle_middleware(ctx, a)`, `resolve_access_manager`,
  `ExternalStateExecutor.for_vault`) return context-free wrappers. Executors
  and orchestrators own their contexts: `Multicall3(ctx)`, `VaultSimulator`,
  `CrosschainSimulator`, `OracleMappingReader(ctx, oracle)` and
  `CrosschainLane` (`hub_ctx` for the executor, `spoke_ctx` for the
  dispatcher at the same address: run a lane's Calls on the matching one).
  A chain-dependent decode takes the chain explicitly
  (`ExternalStateExecutor.pending_proposals(account, chain_id=)`); a decoder
  never consults the executing context.
- Batch independent reads: `Multicall3(ctx).aggregate(calls)` (raises as
  `.call(ctx)` would) or `.try_aggregate(calls)` (``None`` per failed read) runs
  many `Call`s in one `eth_call` at one block; every batch, re-run and
  no-Multicall fallback runs on its `ctx`. A loop of `.call(ctx)`s, or a
  thread pool of them, costs one round trip each; RPC latency, not decoding,
  is what makes the vault tooling slow.
- Fuses (`fuses/`) are stateless encoders: a method returns an immutable
  `FuseAction(fuse_address, calldata)` and touches no chain. `Fuse` builds it
  via `self._action_raw(solidity_signature, values)`; the shared `_validate_*`
  helpers raise before encoding. `PlasmaVault.execute([actions])` runs the
  batch atomically.
- Pin reads, never mutate a shared context: `ctx.at_block(n)` is a copy whose
  reads (and `get_logs` upper bound) default to block ``n``; `Call.call(ctx, block=n)`
  pins one read.
- Simulate before sending: `VaultSimulator(web3, vault=, alpha=)` batches
  `execute` and `observe` calls into one `eth_simulateV1` round trip, across
  blocks via `next_block`. Integration tests and examples use it instead of a
  local node.
- Amounts are raw on-chain integers; `types.py` `NewType`s (`Amount`, `Shares`,
  `MarketId`, ...) name the unit, and nothing scales by decimals.
- Three address roles are nominal: `PlasmaVaultAddress`, `FuseAddress` and
  `AccessManagerAddress` (NewTypes of `ChecksumAddress`). Their wrappers, getters and
  records return them, so a vault, a fuse and an access manager cannot be passed for
  each other; `parse_*_address` brands raw input. Generic plumbing (`ContractWrapper`,
  `Call`, `Multicall3`, logs, storage, simulation overrides, ERC20 and account
  inputs, event emitters) and recognition boundaries (`resolve_access_manager`'s
  input) stay `ChecksumAddress`; external vaults (ERC4626, Euler, MetaMorpho) are not
  `PlasmaVaultAddress`.
- Ids and addresses are nominal in every signature: `ChainId`, `MarketId` (each
  `IporFusionMarkets` member is one), `RoleId | Roles`, `ChecksumAddress`;
  `HexAddress` only for the lowercase addresses decoded from substrates;
  `MorphoBlueMarketId` is always `0x` + 64 lowercase hex, built by
  `parse_morpho_market_id`. Raw input
  is parsed once at the boundary (click param types, MCP tools, the user config).
  `src/`, `examples/` and `tests/typed_client/` are checked with
  `reportArgumentType` on, so a bare `int`/`str` passed for one fails pyright; the
  other tests keep it off and pass literals.
- Addresses: fuse, factory and manager addresses per chain come from
  `ipor-abi` `addresses.json`; a fuse must be registered on the vault
  (`PlasmaVault.get_fuses()`) and its market granted substrates before an
  action can succeed.
- Adding a protocol: new module in `fuses/`, re-export from `fuses/__init__.py`
  and the top-level `__all__` (and both snapshots in `test_public_api.py`),
  encoding test in `test_fuse_encoding.py`, and a row in the README protocol
  table.
- Keeping `examples/` current: a change to public API, SDK behavior or
  defaults includes reading the examples for what it supersedes — hand-rolled
  code a new helper replaces, workarounds for a gap it closes, prose describing
  the old behavior, checks the SDK performs itself, hardcoded values it can
  discover — and updating them in the same PR. The example tests catch an
  example that breaks, not one that still works the old way. Authoring rules
  live in `examples/CONTRIBUTING.md`.

## Substrate decoders: source of truth

Sync `market_ids.py`, `config/roles.py` and the `substrates.py` registry from
the Solidity libraries above; add missing entries and carry over the substrate
type comments. Every new market id gets a decoder registration in the same
change, sourced from its Solidity substrate lib, never assumed. Every typed
decoder sets a `type_label` (enum name or a protocol tag like `EULER_VAULT`);
a label-less decode renders as a generic placeholder in every consumer.
Plain-address decoding only when the fuses check `isSubstrateAsAssetGranted`;
typed layouts get their own decoder with the enum labels carried over. A
market with no substrate semantics stays unregistered ON PURPOSE and renders
as a loud `no_decoder(NAME)` (e.g. LITE_PSM=48). Never default to
plain-address or guess a layout: a wrong decoder renders plausible-looking
garbage addresses that consumers act on, so it is worse than none (Aave V4,
id 49, is the documented case in `substrates.py`). `decode_substrate` handles
plain addresses (12 zero bytes) and typed substrates (11 zero bytes + type
byte, e.g. Ebisu ZAPPER/REGISTRY).

## CLI and MCP

`fusion config` (set-provider, set-etherscan-key, show), `fusion market`
(morpho-blue, meta-morpho), `fusion vault` (add, remove, list, info, validate,
role-accounts, oracle-mapping). `vault info` runs independent fetch pipelines
(vault/asset, fees, WithdrawManager, markets, roles) concurrently on a
`ThreadPoolExecutor`, each a short chain of `Multicall3` batches; keep new
reads inside a batch rather than adding per-call round trips. A `from_url` context
bounds requests in flight (`max_concurrent_requests`, default 8; an
`InFlightLimit` shares one bound across contexts) whatever the pool nesting, so a
new fan-out does not need its own limit. Contract names,
token symbols and decimals and deployment blocks are cached per chain in
`$XDG_CACHE_HOME/ipor-fusion/contract_cache.json` (`<chain>:<address>:<field>`
keys); tests redirect it with `monkeypatch.setenv("XDG_CACHE_HOME", ...)` or
inject a cache.

Every CLI command has an MCP tool (`fusion changelog` maps to `server_info`;
the rest share names). Read-only: `vault_info`, `vault_validate`,
`vault_list`, `vault_role_accounts`, `vault_oracle_mapping`, `market_morpho_blue`,
`market_meta_morpho`, `config_show`, `server_info`. Mutating: `vault_add`,
`vault_remove`, `config_set_provider`, `config_set_etherscan_key`. Every new
CLI command gets a matching MCP tool. Put every fact in its structured home:
the return shape is the Pydantic return type (it becomes `outputSchema`),
per-argument semantics go in `Annotated[..., Field(description=...)]`, closed
value sets are `Literal` enums. The docstring keeps only what has no
structured home: a one-line summary, behavioral notes, cost warnings. Do not
repeat schema content as prose; it drifts.

`mcp/models.py` forbids extra keys. Adding a top-level field to
`build_vault_info_json` requires adding it to the matching model (with a default
for back-compat) and to the `_full_vault_info_dict` fixture; `test_mcp_models.py`
enforces the mirror. Conditionally shaped blocks (`withdraw_manager_details`,
`substrates`, `dependency_graph`) are `dict[str, Any]`, so their inner keys
are not validated.

## Domain rules (verified against the contracts)

- `fetch_vault_data(ctx, vault, *, block=, lending=, metadata=)` is the snapshot:
  it resolves one block (``block``, else a numeric `ctx.default_block`, else latest),
  runs every read on `ctx.at_block(block)` (log scans end there too) and returns
  everything validation and the ``vault info`` document need, so neither reads the
  chain. The chain is `ctx.chain_id`; ``lending=False`` skips lending health, the
  position breakdowns, their prices and the unpriceable check; ``metadata=False``
  skips token symbols/decimals. Add a new chain-state input to the snapshot, never a
  read inside a renderer or validator.
- PlasmaVault serves governance/view functions via `fallback()` delegatecall to
  PlasmaVaultBase, so Etherscan's ABI for the vault address omits callable
  view functions such as `getDependencyBalanceGraph`. An empty dependency graph
  means "unconfigured on-chain", not "missing function".
- Vaults deployed before the August 2024 audit (no `PLASMA_VAULT_BASE`, e.g.
  early Arbitrum USDC vaults) answer `getMarketSubstrates`, `getTotalSupplyCap`
  and `getPriceOracleMiddleware` with empty data and name the oracle getter
  `getPriceOracle()`. `fetch_vault_data` records them in
  `unimplemented_getters` (a health-check warning each) instead of failing; a
  required getter answering empty raises `UnsupportedVaultVersionError`.
- WithdrawManager: `request_fee` (scheduled exit) and `withdraw_fee` (instant
  exit) are mutually exclusive paths, never summed. `redeemFromRequest` charges
  no fee; the request already paid `request_fee`.
- The `ERC20_VAULT_BALANCE` dependency edge is required only for markets that
  touch non-underlying idle tokens (ERC20_VAULT_BALANCE substrates minus
  underlying). Single-asset optimizers legitimately omit it; idle underlying is
  already in `totalAssets` via ERC4626 base accounting.
- `ZeroBalanceFuse` markets are capabilities (swap, flash-loan,
  instant-withdrawal, admin) with structurally zero balance, not venues. They
  go in `zero_balance_fuses` and are not counted as liquidity markets.
- Reconciliation: `totalAssets = balance_fuses_sum + underlying_on_vault`.
  Balance fuses already price non-underlying ERC20s (e.g. ERC20BalanceFuse
  values BOLD + BOLDUSDC-gauge in WETH terms). Never add all ERC20 values on
  top of balance fuse totals; that double-counts. `erc20_direct_total` in JSON
  output is informational only.

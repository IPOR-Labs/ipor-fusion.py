# Migrating to ipor-fusion 4.0

4.0 changes how every program calls the SDK, not only how type-checked code
spells its arguments. Start with the checklist below; the sections after it hold
the details, and each change is described once, in its section.

## Start here

> **Most 3.x calls fail only when the line runs.** A removed import
> (`ipor_fusion.fuses.crosschain`, `NavMark`, the 3.x cache functions) fails at
> import, but `PlasmaVault(ctx, address)`, `ERC20(ctx, token)` or any other
> `Wrapper(ctx, ...)` raises `TypeError` only when that line executes, and so does
> `call.call()` without a context. A service that wraps its reads in a broad `except Exception` degrades
> silently (empty values, skipped vaults) instead of failing. Before you deploy:
>
> - search for wrappers built with a context (`(ctx,`, `(self.ctx,`, `(self._ctx,`)
>   and for `.call()`, `.send()`, `.build_transaction()` and `.estimate_gas()` with no
>   argument;
> - look for caches or containers of wrappers typed `Any` (or untyped): a type
>   checker cannot see the calls made through them;
> - run the reads against a provider, not only the test suite.

Work through these in order; each is what breaks first at run time after the one
before it is fixed.

1. **Removed and renamed imports fail first, at import.** The preview features
   left the top level: crosschain names import from `ipor_fusion.crosschain`,
   HyperCore names from the new `ipor_fusion.hypercore` (see
   [Preview features leave the top level](#preview-features-leave-the-top-level));
   the crosschain encoders left `ipor_fusion.fuses.crosschain` for
   `ipor_fusion.crosschain.fuses` (no shim); the event decoder modules
   `ipor_fusion.fuses.external_state_events` and `ipor_fusion.fuses.hypercore_events`
   are renamed `external_state_event_decoding` and `hypercore_event_decoding` (import
   the external-state names from `ipor_fusion` or `ipor_fusion.fuses`, the HyperCore
   ones from `ipor_fusion.hypercore`); `IporFusionMarkets.RWA`, `MORPHO_BLUE_ADDRESS`, the 3.x cache
   functions, `read_erc20_holdings`, `ExternalStateExecutor.mark_nav` and `NavMark`
   are gone. See [Moved, removed and reshaped](#moved-removed-and-reshaped).
2. **Wrappers are built from an address; execution takes the context.**
   `PlasmaVault(vault)`, `ERC20(token)`, `AccessManager(manager)`; then
   `.call(ctx)`, `.send(ctx)`, `.build_transaction(ctx)`, `.estimate_gas(ctx)`. See
   [Context-free wrappers](#context-free-wrappers).
3. **Methods that read the chain to answer are `read_*(ctx, ...)`**:
   `vault.get_balance_fuses()` is `vault.read_balance_fuses(ctx)`,
   `access_manager.owner()` is `read_owner(ctx)`. See
   [Renamed methods](#renamed-methods).
4. **Pin a block with `ctx = ctx.at_block(n)`.** Assigning `ctx.default_block`
   raises `AttributeError`. See [Vault reads and validation](#vault-reads-and-validation).
5. **The vault read is one snapshot.** `fetch_vault_data(ctx, vault, *, block=None,
   lending=True, metadata=True)` takes the vault address and the chain from `ctx`;
   `validate_vault_state(state)` and `build_vault_info_json(data, *,
   resolve_name=None)` are pure. See [Vault reads and validation](#vault-reads-and-validation).
6. **Simulations return typed observations.** `sim.observe("label", call)` raises
   `TypeError`: `read = sim.observe(call, label="label")` returns a
   `SimulationObservation`, read as `read.value` after `run()`;
   `SimulationResult.get`, `SimulationResult.observations` and
   `SimulatedCallResult.decoded` are removed (`AttributeError`). `label` is
   keyword-only on `observe`, `add_call`, `add_transaction` and `deploy_contract`.
   A failed `run()` leaves every observation unresolved instead of holding the
   previous run's value. The result records are frozen, logs are `SimulatedLog`
   records, and a failed call always carries an `error`. See
   [Typed batches and simulation observations](#typed-batches-and-simulation-observations)
   and [Behavior changes](#behavior-changes).
7. **Options by keyword and other reshaped signatures.** Several functions take
   their options by keyword or renamed a parameter. See
   [Moved, removed and reshaped](#moved-removed-and-reshaped).
8. **Errors.** Every SDK exception derives from `IporFusionError`; some raises changed
   class (empty call results, crosschain discovery, simulation, Morpho API, malformed
   HyperCore logs). See [Errors](#errors).
9. **Cache and request limit.** `Web3Context.from_url` bounds requests in flight at
   8 (`max_concurrent_requests`; `InFlightLimit` to share a bound, `None` to lift
   it), and the contract cache is injected per context (`NullCache()` keeps
   nothing). See [Contract cache and request limit](#contract-cache-and-request-limit).
10. **Typed callers** (pyright, mypy, Pylance): ids, addresses and amounts are
    nominal types. Wrap literals and raw values, `ChainId(8453)`, `Period(0)`,
    `Amount(n)`, `parse_fuse_address(...)`; values the SDK returns already carry
    their type. See [Nominal types](#nominal-types).
11. **Typed JSON documents and events.** `build_vault_info_json` and the `fusion
    market` builders return TypedDicts (`VaultInfoJson`, ...); code annotating them
    `dict[str, Any]` switches to the TypedDict or `Mapping[str, object]`. Decoded
    events are discriminated unions narrowed by `event.name`. See
    [Typed JSON documents](#typed-json-documents) and [Decoded events](#decoded-events).

Then read [Behavior changes](#behavior-changes): they apply to every caller,
type-checked or not.

Addresses a user types in (CLI arguments, config files, forms): normalize first,
then parse strictly, `parse_plasma_vault_address(Web3.to_checksum_address(raw.strip()))`.
The `parse_*` address parsers reject hex without `0x` and surrounding whitespace;
`Web3.to_checksum_address` accepts hex without `0x` but not whitespace.

## Context-free wrappers

A contract wrapper is its address (plus genuine contract parameters, such as a legacy
oracle's decimals) and nothing chain-bound. Construction and `Call` building are
offline; the `Web3Context` is a required argument where something executes. In 3.x
the context was stored on the wrapper and copied into every `Call`; that is gone,
together with `ContractWrapper.encoder()`, `Call.ctx` and the zero-address
placeholder wrapper.

- Build every wrapper from its address: `PlasmaVault(vault)`, `ERC20(token)`,
  `FusionFactory(factory)`, `AccessManager(manager)`,
  `LegacyPriceOracleMiddleware(oracle, Decimals(8))`, the crosschain executor,
  dispatcher and factory wrappers, the Chainlink CCIP wrappers, the readers
  (`MorphoReader(morpho)`, `AaveV3Reader(pool)`, ...). `HyperCoreReader()` takes no
  argument, and `HyperCorePendingReader(vault, reader)` takes the vault's
  `PlasmaVaultAddress`, not a `PlasmaVault` wrapper.
- Execute a `Call` on a context: `.call(ctx, *, block=None)` (reads default to the
  context's, possibly pinned, block), `.send(ctx)`, `.build_transaction(ctx, *,
  block="latest")`, `.estimate_gas(ctx, *, block="latest")`. `ctx` may be passed by
  keyword. Without it Python raises `TypeError` (and pyright reports the call),
  instead of the 3.x `ValueError("Web3Context required ...")`. `.calldata`,
  `.decode(raw)` and `.map(f)` need no context. `Call(...)` no longer takes `ctx=`.
- A wrapper method that executes to answer (storage, log scans, dependent reads) is
  named `read_*` and takes the context first (table below).
- Executors and orchestrators keep their contexts: `Multicall3(ctx, *, address=,
  batch_size=)` (options by keyword; every batch,
  every re-run of a failed sub-call and the per-call fallback run on its `ctx`, so a
  pinned context keeps them all at its block; it is no longer a `ContractWrapper`,
  `aggregate3(calls)` still returns the raw `Call`), `VaultSimulator`,
  `CrosschainSimulator` and `OracleMappingReader(ctx, oracle)`.
- Resolvers that probe the chain stay eager and return context-free wrappers:
  `price_oracle_middleware(ctx, address)`, `resolve_access_manager(ctx, vault)`,
  `ExternalStateExecutor.for_vault(ctx, vault)`. A resolved oracle carries the ABI
  found on that chain and block; resolve again for another one.
- Crosschain lanes own both contexts: `CcipLane` / `StargateLane` take `hub_ctx=` and
  `spoke_ctx=` instead of `spoke_chain_id=` (it is `spoke_ctx.chain_id`), and expose
  them as `lane.hub_ctx` / `lane.spoke_ctx`. `needs_attestation`,
  `command_in_flight` and `transfer_in_flight` read on `hub_ctx`. The Calls a lane
  returns are unexecuted, and the executor and dispatcher share one address, so pick
  the context yourself: `lane.get_balance().call(lane.hub_ctx)`,
  `lane.observation().call(lane.spoke_ctx)`, or let the lane pick:
  `lane.read_hub_state()` reads every executor bucket and counter
  (`LaneHubState`) on `hub_ctx` through `Multicall3`, at one block (a `latest`, `safe` or
  `finalized` default block resolved to its number once, so a per-call fallback
  stays on it; a number or hash as is);
  `lane.read_observation()` runs the observation on `spoke_ctx`. Keep
  `transfer_in_flight()` / `command_in_flight()` for those decisions: the counters
  do not see a CCIP recall or tell a parked command from a pending one. `CrosschainTransport.executor(address)`
  and `.dispatcher(address)` no longer take a context.
- Chain-bound decoding takes the chain explicitly:
  `ExternalStateExecutor.pending_proposals(balance_account, *, chain_id)`,
  `parse_balance_proposed(log, *, chain_id)` and
  `find_balance_proposed(logs, balance_account=None, *, chain_id)` require it (3.x fell back
  to the wrapper's context). The decoder captures it, so `.call(ctx)`, `Multicall3`
  and `VaultSimulator.observe` stamp the same hash for the same bytes, whatever
  context executes them. Pass `ctx.chain_id` for the chain you read from.
  `pending_proposals` now refuses an executor at the zero address when the Call is
  built, not when it is decoded.

| 3.x | 4.0 |
|---|---|
| `PlasmaVault(ctx, vault)`, `ERC20(ctx, token)`, any `Wrapper(ctx, address, ...)` | `PlasmaVault(vault)`, `ERC20(token)`, `Wrapper(address, ...)` |
| `PlasmaVault.encoder(vault)`, `ERC20.encoder()` | `PlasmaVault(vault)`, `ERC20(token)` (the real address) |
| `LegacyPriceOracleMiddleware(ctx, oracle, decimals)` | `LegacyPriceOracleMiddleware(oracle, decimals)` |
| `HyperCoreReader(ctx)` | `HyperCoreReader()` |
| `Call(to=, data=, ..., ctx=ctx)` | `Call(to=, data=, ...)` |
| `call.call()` / `call.call(block=n)` | `call.call(ctx)` / `call.call(ctx, block=n)` |
| `call.send()` | `call.send(ctx)` |
| `call.build_transaction(block=b)`, `call.estimate_gas(block=b)` | `call.build_transaction(ctx, block=b)`, `call.estimate_gas(ctx, block=b)` |
| `call.ctx` | gone: keep the context yourself |
| `Multicall3(ctx, address)` | `Multicall3(ctx, address=address)` |
| `executor.pending_proposals(balance_account)` | `executor.pending_proposals(balance_account, chain_id=ctx.chain_id)` |
| `executor.parse_balance_proposed(log)` / `find_balance_proposed(logs)` | `...(log, chain_id=...)` / `...(logs, chain_id=...)` |
| `CcipLane(executor=, dispatcher=, spoke_chain_id=, ...)` | `CcipLane(hub_ctx=, spoke_ctx=, executor=, dispatcher=, ...)` (same for `StargateLane`) |
| `transport.executor(ctx, address)` / `transport.dispatcher(ctx, address)` | `transport.executor(address)` / `transport.dispatcher(address)` |
| `sim.observe("label", call)` ... `result.get("label")` | `read = sim.observe(call)` ... `read.value` |
| `csim.observe(chain_id, "label", call)` ... `results[chain_id].get("label")` | `read = csim.observe(chain_id, call)` ... `read.value` |
| `result.observations`, `SimulatedCallResult.decoded` | gone: keep the observation, or `call.decode(bytes(r.return_data))` |
| `a, b = Multicall3(ctx).aggregate(cast(list[Call[Any]], [x, y]))` | `batch = Multicall3(ctx).batch()`; `a = batch.add(x)`; `b = batch.add(y)`; `batch.run()`; `a.value`, `b.value` |

### Typed batches and simulation observations

Reads of different types keep their types. `Multicall3(ctx).batch()` returns a
`Multicall3Batch`: `batch.add(call)` queues a `Call[T]` and returns a
`BatchValue[T]`; `batch.try_add(call)` returns a `BatchValue[T | None]`. One
`batch.run()` executes every queued read in one `eth_call` per `batch_size` reads
(one per read where Multicall3 is not deployed), exactly as `aggregate` does for the
same calls. A failing `add` read raises from `run()` what `Call.call(ctx)` would; a
failing `try_add` read is `None`, as in `try_aggregate`. Each read chooses its own
failure handling, so a mixed batch of required and optional reads is one round trip
and every `value` says in its type whether it can be `None`. Reading `value` before
`run()` raises `RuntimeError`, and a batch runs once. `aggregate` / `try_aggregate`
stay for lists of one type.

```python
batch = Multicall3(ctx).batch()
assets = batch.add(vault.total_assets())    # BatchValue[Amount]
fuses = batch.add(vault.get_fuses())        # BatchValue[list[FuseAddress]]
name = batch.try_add(vault.name())          # BatchValue[str | None]
batch.run()
assets.value, fuses.value, name.value
```

`VaultSimulator.observe(call, *, label=None)` returns a `SimulationObservation[T]`
instead of the simulator, so it no longer chains, and its label is optional (it
names the read in `result.calls` and in failures). After `run()`,
`observation.value` is the read decoded as its `Call` decodes it, from the latest
run; `observation.result` is its `SimulatedCallResult`. Every `run()` unresolves the
observations when it starts and resolves them only once the whole response has
parsed: after a failed run (a transport error, an RPC error, a malformed response,
raised as `SimulationRunError` for the last two) reading one raises `RuntimeError`
until a run succeeds. `label` is keyword-only on `add_call` and `add_transaction`
too, as on `observe` and `deploy_contract`: `sim.add_call(call, from_, "label")`
raises `TypeError`, pass `label="label"`.
`CrosschainSimulator.observe(chain_id, call, *, label=None)` returns the same,
resolved by every relay round. `SimulationResult.get`, `SimulationResult.observations`
and `SimulatedCallResult.decoded` are removed: a list of results of many types has no
honest element type, so the typed value lives on the observation. A write queued with
`add_call` is no longer decoded; decode its return data with the `Call` you queued.

```python
before = sim.observe(vault.total_assets())   # SimulationObservation[Amount]
sim.execute([action])
after = sim.observe(vault.total_assets())
sim.run()
after.value - before.value
```

`Call` is a frozen, hashable dataclass and covariant in its result type:
assigning to a field raises, `output_types` is stored as a tuple (whatever sequence
it was built with, so compare it with a tuple), and a `Call[Amount]` is a
`Call[int]` and a `Call[object]`, so a `Sequence[Call[object]]` takes any calls
without a cast. `add_call` and `execute_call` take a `Call[object]`.

The logs of a simulated call (`SimulatedCallResult.logs`,
`SimulationResult.execute_logs`) are `SimulatedLog` TypedDicts: `address`, `topics`
and `data` as the node's `0x` text, plus its block and transaction identity fields
when it sends them. Other keys a node adds are not kept.

### Renamed methods

| 3.x | 4.0 |
|---|---|
| `PlasmaVault.get_balance_fuses()` | `PlasmaVault.read_balance_fuses(ctx)` |
| `PlasmaVault.withdraw_manager_address()` | `PlasmaVault.read_withdraw_manager_address(ctx)` |
| `PlasmaVault.price_oracle_address()` | `PlasmaVault.read_price_oracle_address(ctx)` |
| `AccessManager.owner()` / `owners()` / `atomists()` | `read_owner(ctx)` / `read_owners(ctx)` / `read_atomists(ctx)` |
| `AccessManager.get_accounts_with_role(role)` | `AccessManager.read_accounts_with_role(ctx, role)` |
| `AccessManager.get_all_role_accounts()` | `AccessManager.read_all_role_accounts(ctx)` |
| `PriceOracleMiddleware.get_assets_price_sources()` | `PriceOracleMiddleware.read_assets_price_sources(ctx)` |
| `WithdrawManager.get_pending_requests(from_block)` | `WithdrawManager.read_pending_requests(ctx, from_block)` |
| `WithdrawManager.get_pending_requests_info(from_block)` | `WithdrawManager.read_pending_requests_info(ctx, from_block)` |
| `MorphoReader.position_breakdown(id, user)` | `MorphoReader.read_position_breakdown(ctx, id, user)` |
| `MorphoReader.require_market(id)` | `MorphoReader.read_existing_market(ctx, id)` |
| `MorphoReader.rates(id)` | `MorphoReader.read_rates(ctx, id)` |
| `MorphoReader.rates_from(market, params)` | `MorphoReader.read_rates_from(ctx, market, params)` (it reads the IRM) |
| `AaveV3Reader.position_breakdown(asset, user)` | `AaveV3Reader.read_position_breakdown(ctx, asset, user)` |
| `AaveV3FuseReader.pool()` | `AaveV3FuseReader.read_pool(ctx)` |
| `PlasmaVault.read(target, data)` / `read_as(target, data, ...)` | `PlasmaVault.universal_read(target, data)` / `universal_read_decoded(target, data, ...)` (they build a Call; `read_*` names only methods that execute) |
| `read_index_of(asset)` | `precompile_index_of(asset)` (it reads nothing) |
| `EulerSwapStaticParams.to_tuple()` (and `EulerSwapDynamicParams`, `EulerSwapInitialState`, `EulerV2BatchItem`) | `.as_tuple()`, a typed tuple instead of a list |
| `ctx.default_block = n` | `ctx = ctx.at_block(n)` |

A service that caches wrappers per chain can now share one wrapper across chains and
snapshots; what it must not share is a `Call` result decoded with a chain-bound
argument (`chain_id=`) for another chain. An external signer still needs the real
target address (`call.to`) and builds the chain-qualified transaction itself, or
gets it from `call.build_transaction(ctx)`.

## Vault reads and validation

The vault read is one pinned snapshot, and the chain is always the context's:

- `fetch_vault_data(ctx, vault, *, block=None, lending=True, metadata=True)` takes
  the vault address (not a `PlasmaVault`) and no `chain_id`. It resolves one block
  (`block`, else `ctx.default_block`, with a `safe` / `finalized` tag or a block hash
  resolved to its number, else the latest) and runs every
  read, log scans included, on `ctx.at_block(block)`; `ctx` is not modified. In 3.x
  `block_number` only selected the block reported in `VaultData`, and the reads ran
  wherever `ctx` pointed. The old `chain_id=0` switch is `lending=False`;
  `metadata=False` skips the display metadata: `token_symbols` and `token_decimals`
  stay empty and the ERC20 holdings' symbols are `"?"`. The underlying asset's
  symbol and decimals and the decimals the valuation needs are still read. With
  `metadata=True` the holdings are named by the same cached, batched read as
  `token_symbols`. `VaultData` gains `vault`, `chain_id`, `market_balances`,
  `erc20`, `token_symbols` and `token_decimals`; `WithdrawManagerData` gains
  `shares_to_release_assets` and `pending_request_assets`.
- `validate_vault(ctx, vault, *, block=None, metadata=False)` fetches (by default
  without display metadata, so its ERC20 holdings are named `"?"`) and validates; it
  no longer takes `resolve_symbol`. `metadata=True` names the ERC20 holdings in the
  result and its findings. `validate_vault_state(state)` is pure: a custom
  `VaultState` also provides `vault`, `chain_id`, `market_balances`, `erc20` and the
  queue's `shares_to_release_assets`.
- `build_vault_info_json(data, *, resolve_name=None)` renders from the snapshot and
  reads nothing; the chain and its label come from `data.chain_id`. Without
  `resolve_name` it names no contract (see
  [Contract cache and request limit](#contract-cache-and-request-limit)).
- `fetch_deployment_info(ctx, vault, *, api_key=None)` and
  `fetch_vault_lending_health(ctx, vault, *, balance_fuse_market_ids,
  market_substrates, aave_pools=None)` take the chain from `ctx`.
- `Web3Context.at_block(block)` returns a pinned copy (same connection, signer, log
  source, cache and scan bounds). `Web3Context.default_block` is read-only:
  `ctx.default_block = n` raises `AttributeError`; the old setter changed the block
  under every thread sharing the context. `Web3Context.get_logs(to_block=None)` ends
  at the context's default block and `Web3Context.get_block(block=None)` returns it,
  both instead of `"latest"`, so a pinned context never sees later logs or blocks.
- `VaultData.price_oracle_addr` is renamed `price_oracle`, like its sibling address
  fields (a custom `VaultState` renames it too).

## Moved, removed and reshaped

**Public API.** 4.0 declares it: `ipor_fusion.__all__`, which now also exports the
names earlier reachable only by module path (`FusionFactory`, `FusionInstance`,
`fetch_market`, `resolve_token_symbol`, the crosschain codecs, ...). Import from
`ipor_fusion`: a module path outside the README's list (*Public API and stability*)
still works but is internal. Every project type a public signature or field uses is
exported too, among them `AaveV3PositionBreakdown`, `AaveV3ReserveTokens`,
`AccountRequest`, `StateOverrideProvider`, `TransferDirection` and the validation
vocabulary `Severity`, `Verdict` and `Check`. Also new: `InFlightLimit` (see
[Contract cache and request limit](#contract-cache-and-request-limit)) and
`log_bytes`, the hex-or-bytes log field helper the crosschain decoders use.

**Crosschain fuse encoders moved.** The crosschain market encoders moved from
`ipor_fusion.fuses.crosschain` into the crosschain package, and
`ipor_fusion.fuses` no longer re-exports them. There is no shim: the old paths
raise `ImportError`. They are not top-level names either (see the next
paragraph).

| 3.x import | 4.0 import |
|---|---|
| `ipor_fusion.fuses.crosschain` | `ipor_fusion.crosschain.fuses` (or `ipor_fusion.crosschain`) |
| `ipor_fusion.fuses.crosschain.base`, `.ccip`, `.stargate` | `ipor_fusion.crosschain.fuses` (its submodules `base`, `ccip` and `stargate` exist but are internal) |
| `from ipor_fusion.fuses import CcipCrosschainSupplyFuse` (and the other crosschain encoders, `SendParams`, `CrosschainSubstrate*`, `crosschain_market_id`) | `from ipor_fusion.crosschain import ...` |

`ipor_fusion.crosschain` now exports the whole crosschain surface: besides the
codecs and wrappers, the encoders, `CrosschainLane`, `CcipLane`, `StargateLane`,
the transports and chains, `discover_deployment`, `open_lane(s)`, `LANES` and
`CrosschainSimulator`, which 3.x exported only from `ipor_fusion`.
`ipor_fusion.crosschain.ccip` and `.stargate` keep exporting only their codecs
and wrappers.

#### Preview features leave the top level

Crosschain and HyperCore are preview features (`PREVIEW_FEATURES`): their names
may change in a minor release, which the top level's major-only removal promise
could not hold. 4.0 exports them only from their subpackages.

- **Crosschain:** the 124 crosschain names import from `ipor_fusion.crosschain`,
  or from `crosschain.ccip` / `crosschain.stargate` / `crosschain.fuses` as before.
  That covers lanes, discovery, transports, encoders, wrappers, codecs and
  `CrosschainSimulator`. It also covers the crosschain ids (`AssetId`, `CommandId`,
  `MessageId`, `LayerZeroEid`, `CcipChainSelector`, ...), which still import from
  `ipor_fusion.types` too.
- **HyperCore:** the 97 HyperCore names import from the new
  `ipor_fusion.hypercore`. That covers the fuses and `HyperCoreSubstrates`, the
  readers and `read_hypercore_*`, the event classes, specs and decoders, and the
  ids (`HyperCoreAsset`, `HyperCoreTokenIndex`, `MarginTableId`, `PayloadHash`,
  ...). It also covers the constants and helpers (`SPOT_DEX`, `USDC_TOKEN_INDEX`,
  `USDC_SYSTEM_ADDRESS`, `system_address`, `precompile_index_of`, `SettlementMode`,
  `TimeInForce`, `TransferDirection`, ...). `HYPEREVM_CHAIN_ID` stays a top-level
  chain constant. `ipor_fusion.fuses`
  and `ipor_fusion.readers` no longer list them; the ids still import from
  `ipor_fusion.types`.
- **Unchanged:** the exception classes (`HyperCoreEventDecodeError`,
  `HyperCoreValuationError`, `CrosschainDiscoveryError`,
  `VerifierResultUnavailable`, ...) stay top-level names of `ipor_fusion.errors`. The `vault info` document's HyperCore TypedDicts
  (`HyperCoreJson`, ...) stay with the document.
- **Error registration:** `import ipor_fusion` no longer loads the crosschain
  package. Import `ipor_fusion.crosschain` to register the crosschain contracts'
  custom errors for decoding by name.

```python
from ipor_fusion.crosschain import CcipLane, discover_deployment, open_lanes
from ipor_fusion.hypercore import HyperCoreDepositFuse, read_hypercore_vault_state
```

**Fuse base.** `Fuse`, `StakeFuse` and the shared validators and substrate packers
moved to `ipor_fusion.core.fuse`. Import `Fuse` and `StakeFuse` from `ipor_fusion`
or `ipor_fusion.fuses`. `ipor_fusion.fuses.base` still re-exports them in 4.0, but
it is a submodule of a public subpackage and therefore internal (see the README,
*Public API and stability*); so are the underscore validators and packers.
`FuseAction` lives in `ipor_fusion.core.fuse_action` and `ZERO_ADDRESS` in
`ipor_fusion.types`; import both from `ipor_fusion` (`FuseAction` also from
`ipor_fusion.fuses`, `ZERO_ADDRESS` from the public module `ipor_fusion.types`).
The internal `ipor_fusion.fuses.base` still re-exports them. The CLI's internal
`ipor_fusion.cli.vault_cmd` was split into `options`, `vault_info_view`,
`vault_markets_view` and `oracle_mapping_view` (private names, not API).

**Event decoder modules renamed.** `ipor_fusion.fuses.external_state_events` and
`ipor_fusion.fuses.hypercore_events` are now `external_state_event_decoding` and
`hypercore_event_decoding`. The receipt decoder `hypercore_events`, re-exported
from `ipor_fusion.fuses`, hid its submodule of the same name (`import
ipor_fusion.fuses.hypercore_events as m` bound the function); the external-state
module was renamed to match. Import the decoders with `from ipor_fusion import
external_state_events` and `from ipor_fusion.hypercore import hypercore_events`;
the modules are internal.

**Removed.**

- `IporFusionMarkets.RWA`: market 50 is `IporFusionMarkets.EXTERNAL_STATE`.
- `MORPHO_BLUE_ADDRESS`: use `morpho_blue_address(chain_id)`. `BLOCK_EXPLORER_URLS`,
  `MORPHO_BLUE_ADDRESSES`, `AAVE_V3_POOL` and `explorer.SUPPORTED_CHAINS` are
  internal. Import `CHAIN_NAMES`, `CHAIN_NAME_TO_ID`, `SUPPORTED_CHAIN_IDS`,
  `GET_LOGS_RANGE_HINTS`, `PUBLIC_ALLOCATOR_ADDRESSES`, `HYPERCORE_PENDING_READERS`,
  `HYPEREVM_CHAIN_ID` and `HYPERCORE_MARKET` from `ipor_fusion` (the HyperCore ones
  also from `ipor_fusion.readers`).
- `read_erc20_holdings`: the holdings are `VaultData.erc20`.
- `ExternalStateExecutor.mark_nav` and `NavMark`: send `propose_balance` and
  `confirm_balance` from separate custodians, then
  `PlasmaVault.update_markets_balances([IporFusionMarkets.EXTERNAL_STATE])`.
- The 3.x cache functions and constants (`load_contract_cache`, ...): see
  [Contract cache and request limit](#contract-cache-and-request-limit).
- `ContractWrapper.encoder()` and `Call.ctx`: see
  [Context-free wrappers](#context-free-wrappers).

**Options by keyword and renamed parameters.** A positional call to one of these
fails at run time:

- `Web3Context(web3, chain_id, *, ...)` and `Web3Context.from_url(url, *, ...)` take
  their options by keyword.
- `VaultSimulator(web3, *, vault=None, alpha=None, block=...)` takes every argument
  after `web3` by keyword, and `vault` and `alpha` are optional: a simulator that only
  observes, deploys or sends `execute_call(call, from_=...)` needs neither, so drop a
  zero-address placeholder. `execute()` without `vault` and `alpha` raises
  `ValueError`, as does `execute_call` without `from_` or `alpha`.
- `fetch_unpriceable_priced_tokens(ctx, *, price_oracle, underlying, balance_fuses,
  market_substrates, morpho_positions)` takes everything after `ctx` by keyword, and
  its `oracle_address` parameter is renamed `price_oracle`.
- `get_contract_name(chain_id, address, *, api_key=None, cache=None)` and
  `get_deployment_tx(chain_id, address, *, api_key=None)` take `api_key` by keyword.
- `build_morpho_blue_market_json(*, market_id, chain_id, params, state, rates,
  api_market=None, api_error=None, public_allocator=None)` takes every argument by
  keyword.
- `encode_oft_compose_msg(*, nonce=, src_eid=, amount_ld=, compose_msg=)` takes
  keyword arguments only.
- `build_oracle_mapping(ctx, vault, *, effective_block, max_depth=6)` takes
  `effective_block` and `max_depth` by keyword.
- The vault parameter is named `vault` throughout: `validate_vault`,
  `fetch_deployment_info`, `fetch_vault_lending_health`, `build_oracle_mapping`,
  `read_external_state`, `ExternalStateExecutor.for_vault`, `read_hypercore_nav`,
  `read_hypercore_vault_state`, `read_hypercore_transfer_snapshot`,
  `read_hypercore_evm_exit_ceiling` and `resolve_access_manager` renamed
  `vault_address` to `vault`; only a call that passes it by keyword changes.
- `FusionInstance.from_dict(data)` rebuilds a stored instance (the
  `dataclasses.asdict` form) with every field parsed; use it instead of
  `FusionInstance(**data)`, which passes raw strings into the role-typed fields.

## Errors

Every exception class the SDK defines now derives from `IporFusionError` and is
importable from `ipor_fusion`; the builtin bases they had are kept. One rule holds
across the whole SDK: a caller's mistake (a bad argument, or an operation the object
was not built for) raises a plain `ValueError` (`TypeError` for an argument of the
wrong type, `KeyError` for an unknown name in a mapping-style lookup such as
`ipor_fusion.guide.guide_text`); a chain, provider or remote API answering something the
SDK cannot use raises an `IporFusionError` subclass. Except for
`CrosschainDiscoveryError` (discovery) and `SimulationError` (`quote_ccip_native_fee`),
every subclass that replaces a `ValueError` raise also derives from `ValueError`, so
`except ValueError` keeps catching it; catch the subclass to tell a chain fact from a
bad argument. Receipts,
logs and transports passed in that do not contain what the call needs (a clone
receipt without its events, logs without the lane's `BalanceProposed`, a CCIP
message to a chain the transport does not configure) stay `ValueError`: the input
is the wrong one.

- `MorphoApiError` (still a `RuntimeError`), `ExternalStateEventDecodeError` (still a
  `ValueError`) and `VerifierResultUnavailable` (still a `LookupError`) now derive
  from `IporFusionError`, so an `except IporFusionError` placed before their own
  handlers catches them first. All three live in `ipor_fusion.errors`, like every
  other SDK exception, and are exported from `ipor_fusion`; the modules that raise
  them (`ipor_fusion.morpho_api`, `ipor_fusion.fuses.external_state_event_decoding`,
  `ipor_fusion.crosschain.ccip.indexer`) still import the same classes.
- `AccessManager.read_owner(ctx)` raises the new `OwnerNotFoundError` (a
  `LookupError`) when no account holds `OWNER_ROLE`, instead of an `IndexError`.
- `EmptyCallResultError` is no longer an eth_abi `InsufficientDataBytes` (nor a
  `DecodingError`). Code that caught either around `.call(ctx)` to tolerate a missing
  contract or getter must catch `EmptyCallResultError`; return data that is present
  but too short still raises eth_abi's error.
- Crosschain discovery (`discover_deployment`, `discover_lane_fuses`,
  `detect_transport`, `open_lane`, `CrosschainDeployment.executor`) raises the new
  `CrosschainDiscoveryError` instead of `ValueError` for on-chain configuration it
  cannot use: a malformed or unknown market substrate, an executor managed by another
  vault or of no known transport, a missing or duplicate lane fuse, or not exactly one
  executor of a transport. An executor reporting `transportKind()` `UNDEFINED` is now
  rejected by `detect_transport` instead of being returned. A transport kind passed in
  that has no lane is still a `ValueError`.
- `Call.call(ctx)` and `Call.decode()` on a write-only `Call` raise `ValueError`, not
  `RuntimeError`. `CrosschainSimulator.relay` raises `ValueError`, not
  `RuntimeError`, for a message to a chain that was not added.
- `VaultSimulator.run` (provider error from `eth_simulateV1`, a result count that
  does not match the queued calls, or a response not in the documented shape, which
  raised `KeyError`, `TypeError` or `ValueError` before: a call result without a
  `0x0`/`0x1` `status`, which 3.x read as a success, or `logs` that is not a list) and
  `CrosschainSimulator.relay` (not settled within `max_rounds`) raise the new
  `SimulationRunError`, still a `RuntimeError`.
- `decode_hypercore_event` raises the new `HyperCoreEventDecodeError` (a
  `ValueError`) for a log whose topic it recognizes but whose other parts are
  malformed; see [Decoded events](#decoded-events).
- The Morpho API client raises `MorphoApiError` for every answer it cannot use and
  every transport failure, never `KeyError`, `TypeError` or `ValueError`:
  - a required market id or address (market and V1 allocation ids, V1 flow-cap ids,
    vault/asset/owner/token/oracle/IRM addresses) that is absent or invalid; an
    optional address (curator, guardian, fee recipients, ...) that is absent or `""`
    reads as `None`, and raises only when present and invalid; an allocator or
    sentinel entry without an address is skipped, and one with an invalid address
    raises;
  - any other answer it cannot read (a missing key such as a `loanAsset` without
    `address`, a value of the wrong type, a body that is not a JSON object);
  - any `OSError` or `http.client.HTTPException` while fetching (it caught only
    `URLError` and `TimeoutError` before).

  When matching a market's own allocation and flow cap, an id that does not parse
  matches nothing, and a V1 flow cap without an id is skipped, as before.
  `fusion market meta-morpho --market` rejects a malformed id.
- The MCP `market_meta_morpho` tool lets `MorphoApiError` propagate instead of
  re-raising it as `ValueError`. MCP clients see the same tool error message; only a
  direct Python call of the tool function sees the new type.
- `read_hypercore_nav` (and `read_hypercore_vault_state`, which calls it) raises the new
  `HyperCoreValuationError` (still a `ValueError`) for a failed `spotBalance`,
  `tokenInfo` or `accountMarginSummary` precompile read, a granted spot token the
  oracle does not price, a price overflow and an unsupported HIP-3 dex bitmap;
  `HyperCoreNav.balance_wad` raises it for a negative NAV.
- `read_hypercore_transfer_snapshot` raises `UnsupportedChainError` (still a
  `ValueError`) on a chain with no known `HyperCorePendingReader` when none is passed.
- `fetch_verifier_result` no longer leaks `KeyError`, `TypeError` or `ValueError` from
  an indexer answer it cannot read (not JSON, a missing or mistyped field, invalid hex
  or address, an empty or non-array CCV list, or a result for a different message id,
  both now checked): that
  indexer is skipped like an unreachable one, and when none serves a readable result
  it raises `VerifierResultUnavailable` naming each indexer's reason. A `message_id`
  that is not 32 bytes is still a `ValueError`.
- `erc20_balance_slot` raises the new `BalanceSlotNotFoundError` (still a
  `ValueError`) when no slot up to `max_slot` answers as the balances mapping; a
  negative `max_slot` is a `ValueError` with its own message.
- The external-state market raises the new `ExternalStateError` (still a
  `ValueError`): `read_external_state` for a storage word longer than 32 bytes, an
  executor word with bits above the address or a pause word other than 0 or 1;
  `ExternalStateExecutor.for_vault` when no executor is deployed and, instead of
  eth_abi's `NonEmptyPaddingBytes`, for a slot that is not an address;
  `parse_balance_proposed` / `find_balance_proposed` for one of the executor's own
  `BalanceProposed` logs whose data is missing or malformed (including data that is
  not hex at all, which raised `TypeError`) or whose hash is not the hash of its own
  fields. A log that is not the event or not from the executor, and
  `find_balance_proposed` over logs that hold no match, are still `ValueError`.
- `ccip_token_lane` raises the new `UnsupportedContractVersionError` (still a
  `ValueError`) for an OnRamp version whose layout is not verified.
- `quote_ccip_native_fee` raises `SimulationError` (label `"quote"`, with
  `revert_reason`) instead of `ValueError` when the call reverts before the CCIP fee
  check. A call that succeeds without sending a message is still a `ValueError`.
- `cache_key(chain_id, address, field)` raises `ValueError` for an address that is
  not 20 bytes of hex, so `get_contract_name` does too.

The rule covers what the SDK itself raises. Errors from web3, the provider or the
HTTP transport while `Call.call(ctx)`, `.send(ctx)` or a `Web3Context` method runs
are not wrapped in an `IporFusionError`; they propagate as web3 raises them (a log
scan that cannot complete raises `LogScanError` instead of returning partial
logs).

## Contract cache and request limit

**Request limit.** `Web3Context.from_url(..., max_concurrent_requests=8)` bounds the
RPC requests in flight across every thread using the context (`None` lifts the
bound); the vault reads fan out on nested thread pools, and the bound is what a
rate-limited provider sees. An `int` bounds one context. To bound several contexts on
one provider (a service that builds a context per request or per scan), create one
`InFlightLimit(n)` per provider and pass it as `max_concurrent_requests=`: every
context built with it shares the same `n` slots. A context built from your own
`Web3` object is not bounded unless you add the limit to it: `limit.install(web3)`
(innermost, as `from_url` does) or
`web3.middleware_onion.inject(limit.middleware, layer=0)`.

**Contract cache.** Contract names, token symbols and decimals and deployment blocks
are cached in a `ContractCache`, injected instead of always written to `~/.cache`:

- `Web3Context(..., cache=...)` and `Web3Context.from_url(..., cache=...)` take one
  (keyword-only); `ctx.cache` returns it, and `ctx.at_block(...)` keeps it. The
  default (`None`) is `default_contract_cache()`, a `FileContractCache` at
  `$XDG_CACHE_HOME/ipor-fusion/contract_cache.json` (`~/.cache/...` when unset),
  resolved when used rather than at import. `NullCache()` keeps nothing;
  `FileContractCache(path)` moves the file; any object with `get(keys)` and
  `put(entries)` over `CacheKey(chain_id, address, field)` → `str` fits the
  protocol (for example a shared store in a service).
- `resolve_token_symbol`, `resolve_token_decimals`, the snapshot's token metadata
  (`fetch_vault_data(metadata=True)`) and `fetch_deployment_info` use `ctx.cache`.
  `get_contract_name(chain_id, address, *, api_key=None, cache=None)` takes the cache
  explicitly (default: the file cache); pass `cache=ctx.cache` to share the
  context's policy. The CLI's text and JSON output name contracts through the
  context's cache.
- `build_vault_info_json(data)` without `resolve_name` names no contract (fuse and
  balance-fuse entries show `contract` as `"?"`, substrate entries omit it) instead of reading names from the on-disk cache; pass
  `resolve_name=partial(get_contract_name, chain_id, api_key=..., cache=ctx.cache)`
  for the CLI's document.
- `ipor_fusion.cache` is a public module now and exports `ContractCache`,
  `FileContractCache`, `NullCache`, `CacheKey`, `CacheField`, `cache_key` and
  `default_contract_cache` (also from `ipor_fusion`). Its 3.x functions and constants
  are gone: `load_contract_cache`, `save_contract_cache`, `update_contract_cache`,
  `load_deployment_cache`, `update_deployment_cache`, `CACHE_DIR`, `CACHE_FILE` and
  `DEPLOYMENT_CACHE_FILE`.

**File format.** One file, keys `<chain_id>:<lowercase address>:<field>` (`name`,
`symbol`, `decimals`, `deployment_block`, `deployment_timestamp`), string values. On
load, a 3.x contract name (`<chain_id>:<address>`) is kept as that chain's `name`;
3.x token entries (`symbol:<address>`, `decimals:<address>`) name no chain and are
discarded, so symbols and decimals are read again once per chain. `deployment_cache.json`
is no longer read: a vault's deployment block is resolved again (it needs an
Etherscan key) and the old file can be deleted.

**Cache behavior.**

- The same address on two chains has two entries: a token's symbol or decimals read
  on one chain is never served for another.
- Absence is not cached: `resolve_token_symbol` still returns `"no contract"` for an
  address without code, but no longer stores it, so a token deployed later (or seen at
  a later block) resolves.
- A write merges with the file as it is on disk and replaces it atomically (a
  temporary file in the same directory, then `os.replace`), so a crash or two CLI/MCP
  processes writing at once never leave a torn file; one of two concurrent writers may
  lose its newest entry, which is read again later.
- A cache never fails a read: a corrupt or unreadable file reads as empty (logged
  warning from `ipor_fusion.cache`) and is rewritten by the next write; an unwritable
  directory (read-only file system) keeps the entries in memory for the process; a
  custom cache that raises is treated as a miss, and so is a stored number that does
  not parse.
- `fetch_deployment_info` returns `rpc-fetch-failed` only when the creation
  transaction or its block cannot be read; a cache failure is no longer reported as
  one (the deployment block is returned).

## Nominal types

4.0 gives chain, market and role ids, the crosschain, HyperCore and Morpho ids,
vault/oracle/token addresses, amounts and transaction hashes a nominal type
(`typing.NewType`) across the public API, and checks the SDK, the examples and a
typed client with pyright's `reportArgumentType`. Other fields keep their types
(e.g. symbols, names and URLs stay `str`). The wrappers do not change values:
`ChainId(1)` is `1` at runtime, so untyped code needs none of this section. Values
the SDK returns already carry their type and go back in unwrapped.

The SDK's own types are importable from `ipor_fusion` (and the public module
`ipor_fusion.types`), as are `USDC_TOKEN_INDEX` and `hip3_action_asset`. Third-party
types are not re-exported: `ChecksumAddress` and `HexAddress` come from `eth_typing`,
and `BlockIdentifier`, `Timestamp` and `TxParams` from `web3.types`. The wrappers
label values; they do not validate byte widths or address spelling.

### Building the types

| Type | From a literal or raw value |
|---|---|
| `ChainId` | `ChainId(1)` |
| `MarketId` | an `IporFusionMarkets` member, or `MarketId(n)` |
| `RoleId` | a `Roles` member (accepted as is), or `RoleId(n)`; `Roles.resolve(name)` returns one |
| `Decimals`, `Period`, `Amount`, `Shares`, ... | `Decimals(8)`, `Period(0)`, `Amount(n)`, `Shares(n)` |
| `ChecksumAddress` | `Web3.to_checksum_address(value)` |
| `HexAddress` | any `ChecksumAddress`, or `parse_hex_address(value)` (20 bytes or `0x`/`0X`-prefixed hex in any case → lowercase `0x` form) |
| `MorphoBlueMarketId` | `parse_morpho_market_id(value)`: 32 bytes or hex in any spelling, always `0x` + 64 lowercase digits |
| `TxHash` | `parse_tx_hash(value)` (32 bytes or `0x`/`0X`-prefixed hex in any case) |
| `PlasmaVaultAddress`, `FuseAddress`, `AccessManagerAddress` | from the SDK (`PlasmaVault.address`, `get_fuses()`, `get_access_manager_address()`, `FusionInstance`, discovery), or `parse_plasma_vault_address` / `parse_fuse_address` / `parse_access_manager_address(value)` for a raw address; they label the role and check nothing on chain |
| crosschain ids | `CommandId(b)`, `MessageId(b)`, `AssetId(b)`, `OperationId(b)`, `TransferId(b)`, `ReturnRequestId(b)`, `PositionSetHash(b)` (bytes32); `ProposalId(n)`, `DeploymentRequestId(n)`, `StargateAssetId(n)`, `LayerZeroEid(n)`, `CcipChainSelector(n)` (int) |
| HyperCore ids | `HyperCoreTokenIndex(n)` (or `USDC_TOKEN_INDEX`), `HyperCoreAsset(n)` (or `hip3_action_asset(dex, index)`), `HyperCoreOrderId(n)`, `HyperCoreClientOrderId(n)`, `MarginTableId(n)`, `HyperCoreActionId(n)`, `PayloadHash(b)` |
| external state | `ProposalHash(b)` |
| `BlockIdentifier` | a block number, `"latest"` / `"pending"` / ..., or a block hash |

The address and hash parsers (`parse_hex_address`, `parse_tx_hash`,
`parse_plasma_vault_address`, `parse_fuse_address`, `parse_access_manager_address`)
validate strictly: a string needs exactly one `0x`/`0X` prefix and the exact number of
hex digits, with no surrounding whitespace; anything else raises `ValueError`.
Normalize user input first (see [Start here](#start-here)).
`parse_morpho_market_id` still accepts hex without `0x`.

`MessageId` covers both a CCIP `messageId` and a LayerZero `guid`. `AssetId` is the
crosschain executor's `bytes32` asset id; a Stargate pool's `uint16` asset id is
`StargateAssetId`. `DeploymentRequestId` is a factory's `uint256` dispatcher request;
a `bytes32` return (recall) request is `ReturnRequestId`.

### Typed signatures, by area

**Units.** `VaultData.total_assets`, `underlying_balance_on_vault` and
`market_balances` values are `Amount`; `total_supply` and `supply_cap` are `Shares`;
`share_decimals` / `asset_decimals` are `Decimals`; `block_timestamp` and
`deployment_timestamp` are `Timestamp`. `FeeData` fee fields are `Fee`,
`management_fee_last_update` a `Timestamp`, `unrealized_management_fee` an `Amount`;
`WithdrawManagerData.withdraw_window` is a `Period`, its fees `Fee`,
`shares_to_release` `Shares`, `last_release_funds_timestamp` a `Timestamp` and the
converted amounts `Amount`. A custom `VaultState` annotates the same.
`VaultValidation.block_timestamp` is a `Timestamp` and `asset_decimals` `Decimals`;
`Erc20Holding.decimals` / `balance` are `Decimals | None` / `Amount | None` and
`Erc20Holdings.value_in_asset` / `market_cached_value` `Amount`;
`VaultData.token_decimals` values and `resolve_token_decimals` are `Decimals | None`.
`PlasmaVault.total_supply()`, `balance_of()` and `get_total_supply_cap()` return
`Shares` (vault shares), and `transfer` / `approve` / `transfer_from` /
`set_total_supply_cap` take `Shares`; `ERC20` keeps `Amount`.
`Reconciliation` amounts (`balance_fuses_total`, `underlying_on_vault`,
`erc20_direct_total`, `sum`, `on_chain_total_assets`, `delta`,
`pending_withdrawals`, `implied_market_total`, `market_storage_divergence`) are
`Amount`. `FusionInstance.asset_decimals` / `underlying_token_decimals` are
`Decimals`, and `CloneArgs.redemption_delay_seconds` is a `Period`.
`VaultSimulator.current_time` is a `Timestamp`.

**Vault data and helpers.** `ensure_supported_chain` takes `ChainId`.
`fetch_unpriceable_priced_tokens` takes the oracle and underlying as
`ChecksumAddress`. `resolve_token_symbol`, `resolve_token_decimals`,
`get_contract_name` and `get_deployment_tx` take `HexAddress` (a checksum address
fits), and the `resolve_name` callback receives one (a callback typed for `str`
still fits). Market-keyed data is keyed by `MarketId`:
`VaultData.market_substrates`, `dependency_graph`, `morpho_positions`,
`aave_positions`, `market_balances`; the `VaultState` protocol,
`fetch_vault_lending_health`, `fetch_unpriceable_priced_tokens` and the
`dependency_graph` helpers (`compute_update_reach`, `compute_update_groups`,
`find_orphan_fuse_markets`, `find_markets_missing_erc20_dependency`).
`find_orphan_fuse_markets` returns `dict[MarketId, list[FuseAddress]]`, so its fuses
go into `PlasmaVault.remove_fuses` as they are.

`VaultData.asset`, `price_oracle`, `rewards_manager`, `withdraw_manager`,
`FeeData.fee_manager` / `ipor_dao_fee_recipient` and `VaultValidation.vault` /
`asset` are `ChecksumAddress` (`VaultValidation.vault` a `PlasmaVaultAddress`);
`VaultData.access_manager` is an `AccessManagerAddress`, `fuses` / `instant_fuses`
and the `fuse_markets` keys are `FuseAddress`, `balance_fuses` is
`list[BalanceFuse]`, and `token_prices_usd` is keyed by lowercase `HexAddress`. A
custom `VaultState` implementation annotates `asset` and `price_oracle` as
`ChecksumAddress`, `fuse_markets` keys as `FuseAddress`, and its
`unpriceable_priced_tokens` items' `market_id` as `MarketId` (`UnpriceableToken`).
The Uniswap V3 / Ramses V2 position events' `version`, `sender` and `recipient` are
`ChecksumAddress`. `VaultValidation.chain_id` is `ChainId`;
`LendingMarketHealth.market_id` and `MiddlewarePricedToken.market_id` are
`MarketId`; `Erc20Holding.address` is `HexAddress`, and `SubstrateInfo.address` is
`HexAddress | None`. `LegacyPriceOracleMiddleware` takes `base_currency_decimals:
Decimals`, and `PriceOracleMiddleware.base_currency_decimals()` returns
`Call[Decimals]`. `get_logs_adaptive(chain_id=)` takes `ChainId | None` and
`LogSource.get_logs` a `ChainId`; `quote_ccip_native_fee(block=)` and
`OracleMappingReader.asset_source_events` take a `BlockIdentifier`.
`OracleMappingReader.token_decimals`, `feed_decimals` and `vault_decimals` return
`Decimals | None` and `vault_convert_to_assets` an `Amount | None`;
`AaveV4OracleReader.decimals()` returns `Call[Decimals]`; and `OraclePrice.decimals`, `OracleNode.decimals` and
`OracleAsset.decimals` are `Decimals` (`| None` on the nodes).

**Address roles.** `PlasmaVault(address)`,
`validate_vault`, `fetch_deployment_info`, the HyperCore, lending-health and
oracle-mapping readers, `read_external_state`, `VaultSimulator(vault=)`,
`CrosschainSimulator`, crosschain discovery, the hub-manager inputs
(`ExecutorInitParams.manager`, `create_executor(manager=)`, `executors_of(manager=)`),
the async command helpers `Command.request_shares` / `redeem_from_request`,
`pending_request_shares` and `WithdrawManager.update_plasma_vault_address` take a
`PlasmaVaultAddress`; `.address`, `FusionInstance.plasma_vault` and the executor's
`manager()` return one. Crosschain deposit and redeem destinations stay
`ChecksumAddress`: `Command.deposit` / `redeem`, `is_allowed_vault`,
`is_tracked_vault` and the remote-vault substrate accept any allowlisted ERC4626.
Every fuse constructor, `FuseAction.fuse`, `BalanceFuse.fuse`,
`PlasmaVault.add_fuses` / `remove_fuses` / `add_balance_fuse` / `remove_balance_fuse`
/ `balance_fuse_value`, `RewardsManager.add_reward_fuses` / `is_reward_fuse_supported`,
`LaneFuses`, the HyperCore balance-fuse argument and the
`PlasmaVault.configure_instant_withdrawal_fuses` configs
(`Sequence[tuple[FuseAddress, Sequence[bytes]]]`) take a `FuseAddress`, and
`get_fuses()`, `get_instant_withdrawal_fuses()`, `get_rewards_fuses()` and
`VaultData.fuses` / `instant_fuses` / `fuse_markets` keys return it.
`AccessManager(address)` takes an `AccessManagerAddress`;
`get_access_manager_address()`, `VaultData.access_manager` and
`FusionInstance.access_manager` return one. `resolve_access_manager` still takes any
`ChecksumAddress`: it is what checks that the address is a vault.

**Chain registry.** `CHAIN_NAMES`, `CHAIN_NAME_TO_ID`, `SUPPORTED_CHAIN_IDS`,
`GET_LOGS_RANGE_HINTS`, `BLOCK_EXPLORER_URLS`, `MORPHO_BLUE_ADDRESSES`,
`AAVE_V3_POOL`, `PUBLIC_ALLOCATOR_ADDRESSES` and `HYPERCORE_PENDING_READERS` are keyed
by `ChainId` (`HYPEREVM_CHAIN_ID` is one) and `explorer.SUPPORTED_CHAINS` is a
`frozenset[ChainId]`, so `ensure_supported_chain(CHAIN_NAME_TO_ID["ethereum"])`
type-checks; `PUBLIC_ALLOCATOR_ADDRESSES` values are `ChecksumAddress`.

**Markets, substrates and access.** Every `IporFusionMarkets` member is a `MarketId`.
`market_name` and `format_market_label` take `MarketId`, `decode_substrate` a
`MarketId | None`.
`AccessManager.has_role`, `grant_role`, `revoke_role` and `read_accounts_with_role`
take `RoleId | Roles`; `RoleAccount.role_id` is `RoleId`.

**Morpho.** `MorphoBlueMarketId` always carries `0x` (see
[Behavior changes](#behavior-changes)); build it with `parse_morpho_market_id`
instead of `MorphoBlueMarketId("<64 hex>")`. `fetch_market` and
`build_morpho_blue_market_json` take it; `MorphoApiMarket.market_id`,
`VaultV1MarketAllocation.market_id`, `VaultV2Cap.market_id`,
`MorphoPositionBreakdown.market_id` (as read from substrates) and the keys of
`VaultV1Info.public_allocator_flow_caps` are canonical. `fetch_market`, `fetch_vault`,
`morpho_blue_address` and the `build_*_json` helpers take `ChainId`; `fetch_vault`
takes a `ChecksumAddress`, and `build_morpho_blue_market_json` takes
`public_allocator` as `ChecksumAddress | None`. Every address on the `morpho_api`
records is `ChecksumAddress`; optional ones (`curator`, `guardian`, `fee_recipient`,
`performance_fee_recipient`, `management_fee_recipient`, `inner_vault`,
`VaultFlowCap.admin`, the `VaultV2Cap` market params) are `ChecksumAddress | None`.
Token amounts on those records are `Amount` (`VaultFlowCap.max_in` / `max_out`, the
`VaultAllocation`, `VaultV1Info`, `VaultV1MarketAllocation`, `VaultV2Info` and
`MorphoApiMarket` asset amounts and caps, `VaultV2Cap.absolute_cap` / `allocation`
/ `room`, `VaultV2Adapter.assets`, `cap_room`), token decimals are `Decimals` and
`MorphoApiMarket.timestamp` is a `Timestamp`. `lltv`, `fee_wad`,
`relative_cap_wad` and `fee_wei` stay `int`.

**Crosschain.** The executor, dispatcher and factory wrappers, the lanes and
`StargateCrosschainCommandFuse.enter(request_id=)` take the crosschain ids above, and
their getters return them (`active_command`, `active_proposal_id`, `propose_balance`
on CCIP, `active_return` → `OperationId`, `asset_id`, `tracked_position_set_hash`,
`pending_request_by_chain`, `CrosschainLane.proposal_id_from_logs` /
`proposal_id_from_receipt`). Records carry them too: `Command`, `ActiveCommand`,
`ExecutorInitParams`, `BalanceObservation`, `LaneObservation`, `Observation`,
`CcipObservation`, `OutboundMessage`, `CcipMessageSent`, `Any2EVMMessage`,
`VerifierResult`, `Packet.guid`, `TaxiMessage`, `StargateChain`; and the helpers
`fetch_verifier_result(message_id=)`, `lz_receive_calldata(guid=)` and
`lz_compose_calldata(guid=)` take a `MessageId`. Lane times are typed:
`last_approved_observed_at()` returns a `Timestamp` and `staleness_max()` (with
`STALENESS_MAX` / `BALANCE_STALENESS_MAX` on the executors) a `Period`, and so do
the `LaneHubState` fields; `attestation(remote_timestamp=, expiry=)` and
`BalanceObservation` take `Timestamp`, `needs_attestation(now=, margin=)` a
`Timestamp` and a `Period`. The other durations are `Period` too: the executors'
`min_update_interval()`, `transfer_staleness_max()` and `deployment_ttl()`, both
factories' `config_delay()`, and the time fields of `ExecutorInitParams` and
`SafetyConfig` (`rescue_delay`, the staleness fields, `min_update_interval`,
`deployment_ttl`); `in_flight_since()`, `last_approval_at()`, `oldest_pending_at()`
and `pending_deployment_created_at()` return a `Timestamp`. Decimals are
`Decimals`: `local_decimals()` / `shared_decimals()` on the executor and dispatcher,
`CcipChain.local_decimals`, `StargateChain.local_decimals` / `shared_decimals`, the
`shared_decimals` of `CcipCrosschainFactory.asset_config()`, `schedule_asset` and
`execute_asset`. Token amounts are `Amount` (`EVMTokenAmount.amount`,
`TokenTransferV1.amount`, `TaxiMessage.amount_sd`, `OutboundMessage.token_amount`,
`StargateChain.to_local_decimals`), and `pending_request_shares()` returns
`Shares`. Counters, sequences, epochs, versions, bps and gas limits stay `int`.
Decoded event values
(`ccip_events` → `CcipEvent.values`) stay a generic mapping: convert a value
before passing it on.

**Transactions, LayerZero and CCIP.** `Web3Context.build_transaction` and
`Call.build_transaction` return web3's `TxParams` (the same keys; `data` is `0x` hex,
the fee fields are `Wei`). LayerZero endpoint ids are `LayerZeroEid`:
`StargateChain.eid`, `Packet.src_eid` / `dst_eid`, `EnforcedOptionParam.eid`,
`executor_eid()` and `eid_of()` return it and `chain_id_of`, `peers`,
`lz_receive_calldata(src_eid=)`, `encode_oft_compose_msg(src_eid=)` and the Stargate
fuse's `eid=` take it (`LayerZeroEid(30101)`). CCIP chain selectors are
`CcipChainSelector`: `CcipChain.chain_selector`, `CcipRouteConfig.chain_selector`,
`CcipTokenLane.chain_selector`, the `MessageV1`, `CcipMessageSent` and
`Any2EVMMessage` selectors and `CcipRouter.get_off_ramps()` carry it, and
`chain_id_of_selector`, `CcipRouter.is_chain_supported` / `get_on_ramp`,
`CcipTokenPool.is_supported_chain` and `ccip_token_lane` take it
(`CcipChainSelector(4949039107694359620)`). `ZERO_ADDRESS` is a `ChecksumAddress`
(same value).

**Simulation.** The `VaultSimulator` override methods name the keys `eth_simulateV1`
accepts instead of taking `**Any`; the keys and the request they produce are
unchanged. `with_state_override(address, **overrides)` takes `AccountOverride` keys
(`balance`, `nonce`, `code`, `movePrecompileToAddress`: `0x` hex strings; `state`,
`stateDiff`: `0x` slot to `0x` word), and a `StateOverrideProvider` returns
`Mapping[str, AccountOverride]`. `with_block_override(**fields)` takes
`BlockOverrides` keys (`number`, `time`, `gasLimit`, `baseFeePerGas`, `blobBaseFee`:
an `int`, sent as hex, or a hex string; `feeRecipient`, `prevRandao`: strings) and
raises `TypeError` for any other value type (`withdrawals` is not supported).
Both TypedDicts are exported from `ipor_fusion`. A misspelled key or an `int` balance
now fails type checking instead of reaching the node:

```python
sim.with_state_override(holder, balance=hex(10**18), nonce=hex(0))
sim.with_block_override(gasLimit=30_000_000)
sim.with_state_override_provider(lambda n: {oracle: {"code": code_hex}})
```

`add_transaction(transaction)` takes web3's `TxParams`, the shape
`Call.build_transaction` returns; a node-shaped dict carrying `input` instead of `data`
is still accepted at run time.

**HyperCore (preview).** The fuses, `HyperCoreSubstrates`, `HyperCoreReader`,
`read_hypercore_transfer_snapshot` and the helpers `system_address`,
`precompile_index_of`, `perp_dex_of`, `is_native_perp_asset`, `is_hip3_asset` take the
HyperCore ids; `HyperCorePerpAssetInfo`, `HyperCorePerpMarket`, `HyperCoreSpotLeg`,
`HyperCorePendingState` and `HyperCoreTransferSnapshot` carry them. Token decimals
(`HyperCoreTokenInfo.sz_decimals` / `wei_decimals` / `evm_extra_wei_decimals`,
`HyperCorePerpAssetInfo.sz_decimals`, `HyperCoreSpotLeg.wei_decimals`) are
`Decimals`, and `HyperCorePendingState.pending_until` / `current_timestamp` are
`Timestamp`; Core amounts, positions, WAD values and L1 block numbers stay `int`.

**External state.** `ExternalStateExecutor.proposal_hash` returns and `confirm_balance`
takes a `ProposalHash`; `BalanceProposal.proposal_hash` is a `ProposalHash`;
`ExternalStateEvent.transaction_hash` is a `TxHash | None`.
`ExternalStateVaultState.last_checked_custodian_timestamp`,
`BalanceFuseSnapshot.last_custodian_update_timestamp`, `BalanceProposal.proposed_at`,
`last_updated()` and `get_oldest_update_timestamp()` are web3's `Timestamp`, and
`ExternalStateExecutor.proposal_hash(proposed_at=)` takes one.
`ExternalStateExecutor.staleness_max()` / `min_update_interval()` return a `Period`,
and `ExternalStateSubstrates.staleness_max` / `min_update_interval` take one
(`Period.DAY`).

**Errors and explorer.** `TransactionError(tx_hash=)` takes a `TxHash | None`,
`LogScanError(address=)` a `ChecksumAddress`; `get_revert_reason` takes a `TxHash`;
`get_deployment_tx` returns a `TxHash` (a malformed hash from Etherscan is reported
as the error `malformed-tx-hash`).

**MCP response models** (Python constructors only; the JSON schemas are unchanged
except as listed under [Behavior changes](#behavior-changes)): `chain_id` fields are
`ChainId`, `role_id` `RoleId`, `market_id` `MarketId` (except
`LendingMarketHealth.market_id`, which stays `str | int`: a Morpho market id is a
string). Calling an MCP tool function
directly from Python takes `ChainId`. Their amount, decimals and time fields stay
`int`: they are JSON output models, and the SDK values above fit them unchanged.
The nested blocks that were `dict[str, Any]` are models now (`deployment`,
`share_price`, `links`, `dependency_graph`, a balance fuse's `position_breakdown`, an
oracle node's `source_detail`, the Morpho `asset`, `adapters`, `caps`,
`liquidity_adapter`, `public_allocator` and `allocations`): read them by attribute
(`result.caps[0].loan_token`, not `["loan_token"]`).

## Typed JSON documents

The JSON document builders return `TypedDict`s instead of `dict[str, Any]`; the
documents themselves (keys, values, order, the MCP schemas) are unchanged, so
untyped code needs none of this section.

| Builder | Returns |
|---|---|
| `build_vault_info_json` | `VaultInfoJson` |
| `build_morpho_blue_market_json` | `MorphoBlueMarketJson` |
| `build_meta_morpho_json` | `MetaMorphoV1Json \| MetaMorphoV2Json`, told apart by `doc["version"]` |
| `RoleAccount.to_dict` | `RoleAccountJson` |
| `OracleMapping.to_dict` / `OracleNode.to_dict` | `OracleMappingJson` / `OracleNodeJson` |

Every nested block has its own TypedDict (`FeesJson`, `WithdrawManagerJson`,
`BalanceFuseJson`, `ReconciliationJson`, `HyperCoreJson`, `MorphoSupplyingVaultJson`,
...), all importable from `ipor_fusion` and from `ipor_fusion.vault_info` /
`ipor_fusion.market_info` (`RoleAccountJson` from `ipor_fusion.core`). Values carry the
SDK's nominal types (`doc["vault"]` is a `PlasmaVaultAddress`, `doc["chain_id"]` a
`ChainId`). A block that can be `null` is `X | None`: check it before indexing
(`fees = doc["fees"]; if fees is not None: fees["management_fee_percent"]`). A key
that only some entries carry is `NotRequired` (a balance fuse's `pct_of_total` and
`position_breakdown`, the Morpho API keys of the market document): read it with
`.get`. A decoded substrate entry stays open-ended, `SubstrateEntryJson`
(`dict[str, str | bool]`), since each decoder adds its own fields.

- A variable or parameter annotated `dict[str, Any]` no longer accepts these
  documents (a `TypedDict` is not a `dict[str, Any]`): annotate it with the TypedDict,
  or `Mapping[str, object]` for code that only passes the document on (`json.dumps`
  accepts either).
- Parsing entry points take `Mapping[str, object]` instead of `Mapping[str, Any]`:
  `decode_external_state_event(log)` and `FusionInstance.from_dict(data)`.
  `VerifierResult.message` is a `Mapping[str, object]`, and `OutboundMessage.raw` is
  `object`: narrow it before use (`isinstance(message.raw, MessageV1)` for CCIP; a
  Stargate `Packet`, or a `(Packet, TaxiMessage)` pair for a token transfer).
- The `vault info` document's `deployment` is `DeploymentJson` (block, timestamp,
  age) or `DeploymentErrorJson` (`{"error": ...}`) or `None`; `share_price` is
  `SharePriceJson` (`asset`, and `usd` when the asset is priced); `dependency_graph`
  is `DependencyGraphJson` (`edges`, `update_reach`, `update_groups`).
- `OracleNode.source_detail` is `SourceDetailJson`, one TypedDict per feed kind
  (`ChainlinkDetailJson`, `DualXrefDetailJson`, `Erc4626DetailJson`,
  `MorphoFeedDetailJson`, `MiddlewareFallbackDetailJson`; importable from
  `ipor_fusion` and `ipor_fusion.readers`). Narrow by a key before reading a
  kind-specific one: `if detail is not None and "aggregator" in detail:
  detail["description"]`.
- `RawLog` (the log shape `ExternalStateExecutor.parse_balance_proposed` /
  `find_balance_proposed`, the event decoders and the crosschain log helpers
  accept) is `Mapping[str, object]`, not `Mapping[str, Any]`; a web3 `LogReceipt`
  still fits it.
- `Command.decode_action_data()` returns `tuple[object, ...]`: narrow each value
  (an `int`, a lowercase hex `str` address or `bytes`, by the action) before use.
- `CrosschainLane.proposal_id_from_receipt(receipt)` is typed to take web3's
  `TxReceipt`; for a raw JSON-RPC or minimal `{"logs": ...}` receipt call
  `lane.proposal_id_from_logs(receipt["logs"])`, which takes any `RawLog`s.

## Decoded events

Decoded events are discriminated unions. `ExternalStateEvent` and `HyperCoreEvent`
are type aliases over one frozen dataclass per event (`ExternalStateUnpausedEvent`,
`HyperCoreDepositFuseEnterEvent`, ...), each with `name: Literal["<event>"]` and
`args` typed as that event's TypedDict (`ExternalStateUnpausedArgs`, ...). Comparing
`name` narrows the event and its `args`, with no key test:

```python
for event in external_state_events(receipt):
    if event.name == "ExternalStateUnpaused":
        event.args["signer"]  # ChecksumAddress
match hc_event:
    case HyperCoreDepositFuseEnterEvent(args=args):
        args["evmAsset"]
```

Without that comparison `event.args["signer"]` does not type-check.
`isinstance(x, ExternalStateEvent)` still works (the alias is a union); the events
are no longer built positionally (`ExternalStateEvent(...)` is not a class: build the
event's own class with keywords, its `name` is preset).
`EXTERNAL_STATE_EVENT_CLASSES` / `HYPERCORE_EVENT_CLASSES` map an event name to its
class. An event's args TypedDict is its class's `args` annotation:
`typing.get_type_hints(EXTERNAL_STATE_EVENT_CLASSES[name])["args"]`. Values carry
nominal types (`Amount`, `MarketId`, `FuseAddress`, `HyperCoreTokenIndex`,
`PayloadHash`, web3's `Timestamp`, ...), and `HyperCoreEvent.fuse` is a
`FuseAddress | None` on every event. Every name is importable from `ipor_fusion` and
from `ipor_fusion.fuses`.

The two families share one input contract. `decode_external_state_event(log)` and
`decode_hypercore_event(log)` take a web3 receipt log or a raw JSON-RPC / indexer
mapping (hex text topics, data, hashes and quantities) and return `None` when topic0
is not one of theirs, or cannot be a topic; once the topic is recognized, a malformed
part raises `ExternalStateEventDecodeError` / `HyperCoreEventDecodeError` (both an
`IporFusionError` and a `ValueError`). `external_state_event_name(topic)` and
`hypercore_event_name(topic)` return `None` for any other topic or a value that
cannot be one. For HyperCore this changes 3.x: `decode_hypercore_event` took only a
receipt log with bytes topics and let eth_abi's errors through,
`hypercore_event_name` raised on bad hex and also matched hex text without `0x`
(which now returns `None`: pass `"0x" + topic.hex()` or `HexBytes(topic).to_0x_hex()`),
and `log_index` was passed through as given (a hex string from a raw log); the events now also carry `block_number` and
`transaction_hash`, like the ExternalState ones.

## Behavior changes

These apply to every caller, type-checked or not. Changes described in full above
are listed with a link.

- **Morpho shares convert to assets as Morpho Blue does.** The position breakdown
  (`MorphoReader.read_position_breakdown`, the `vault info` Morpho rows) and the Morpho
  lending health follow `SharesMathLib`: integer math with 1e6 virtual shares and one
  virtual asset, borrow rounded up, supply rounded down, on the market totals Morpho
  stores (interest accrued since the market's last update is not included). 3.x
  converted borrow shares by dividing by `total_shares + 1` in floating point and
  supply shares without the virtual offsets, so large positions lost their low digits
  and small or empty markets could be off by orders of magnitude: expect materially
  different numbers where they were wrong.

- **MCP `outputSchema` describes every nested block.** The tool output is
  byte-identical; the schemas of `vault_info`, `vault_oracle_mapping`,
  `market_morpho_blue` and `market_meta_morpho` replace each free-form object with
  the block's own definition: `links` (`VaultLinks`), `deployment`
  (`DeploymentModel` or `DeploymentErrorModel`), `share_price` (`SharePriceModel`),
  `dependency_graph` (`DependencyGraphModel`), `position_breakdown` (a list of
  `MorphoBreakdownEntry` or of `AaveBreakdownEntry`, amounts `BreakdownAmount`),
  substrate entries (string or boolean values), `source_detail` (`ChainlinkDetail`,
  `DualXrefDetail`, `Erc4626Detail`, `MorphoFeedDetail` or
  `MiddlewareFallbackDetail`), a supplying vault's `asset` (`MorphoVaultAssetModel`)
  and the MetaMorpho `asset`, `liquidity_adapter` / `adapters`, `caps`,
  `public_allocator` (with `flow_caps`) and `allocations`. Fixed-shape blocks now declare their
  required and optional fields and reject unknown keys (`additionalProperties:
  false`), as the documents always wrote them; substrate entries and keyed maps
  (`flow_caps`, `edges`, `update_reach`) stay open with typed values.

- **Vault reads are pinned to one block**: every read of `fetch_vault_data`, log
  scans included, runs at the resolved block; see
  [Vault reads and validation](#vault-reads-and-validation).
- **`Web3Context.get_logs` and `get_block` default to the context's block**, not
  `"latest"`; see [Vault reads and validation](#vault-reads-and-validation).
- **`metadata=False`** leaves `token_symbols` / `token_decimals` empty and names the
  ERC20 holdings `"?"`, and `validate_vault` defaults to it; see
  [Vault reads and validation](#vault-reads-and-validation).
- **`Web3Context.from_url` bounds requests in flight at 8** by default; see
  [Contract cache and request limit](#contract-cache-and-request-limit).
- **The contract cache** keys every entry by chain and lowercase address, no longer
  caches an absence, and is injected per context; see
  [Contract cache and request limit](#contract-cache-and-request-limit).
- **The supply cap is formatted in share decimals.** `fusion vault info` shows it
  labelled `shares`, and the JSON `supply_cap.formatted` uses `share_decimals`; both
  used the asset's decimals before.
- **`"no contract"` no longer appears in vault output.** The snapshot reads token
  symbols in one batch, which does not tell a token without code from one whose
  symbol cannot be read: the text of `fusion vault info` / `validate` and the ERC20
  holdings show it as `"?"`, and the `vault info` JSON (CLI and MCP) leaves its
  symbol out or `null`. `resolve_token_symbol` itself still returns `"no contract"`.
- **Morpho market ids always carry `0x`.** `fetch_market` and
  `build_morpho_blue_market_json` no longer add the prefix themselves: pass
  `parse_morpho_market_id(raw_id)`, never a bare 64-hex string.
- The Morpho API client checksums every address it returns (market, vault,
  allocation, adapter and cap records, and the `fusion market` / MCP market documents
  built from them), so string comparisons against the API's own spelling change. Its
  error handling is under [Errors](#errors).
- `fusion market meta-morpho --json` and the MCP `market_meta_morpho` tool report
  `null` instead of `""` for an absent curator, guardian or fee recipient; the MCP
  schema's `curator` is nullable.
- Two fuses are equal (and hash alike) only when they have the same class and the
  same stored state. `StakeFuse` subclasses (`GearboxStakeFuse`,
  `FluidInstadappStakingFuse`) with the same fuse address but different
  `staking_address` were equal and collapsed in a set or dict key; they are now
  distinct. A custom `Fuse` subclass is compared on its instance attributes, which
  must be hashable; drop an `__eq__` / `__hash__` override that only added them.
- `SubstrateInfo.address` is `None` instead of `""` for a substrate without an
  address. Truthiness checks are unaffected and the SDK's JSON documents already
  omit it, but `dataclasses.asdict(info)` now gives `"address": None` and
  `info.address == ""` is false.
- The MCP `vault_info` document reports the vault address checksummed (the CLI
  already did).
- The Uniswap V3 / Ramses V2 position events (`extract_new_position_events`,
  `extract_close_position_events`) return their addresses checksummed (they were the
  lowercase ABI-decoded strings).
- `LendingMarketHealth.substrate_id` for a Morpho market is `0x`-prefixed (the JSON
  documents already were).
- `TransactionError.tx_hash` is `0x`-prefixed.
- An observed simulation read that reverted raises `SimulationError` from its
  `value`, one that returned no data raises `EmptyCallResultError`, and one its
  decoder cannot read raises the decoder's error. 3.x left it out of
  `result.observations` (so `result.get(label)` raised `KeyError`) or reported an
  undecodable read as `None`, indistinguishable from a read that returns `None`.
- **Check `success`; `error` is always set on failure.** A failed
  `SimulatedCallResult` always carries a non-empty `error`: the node's message joined
  with the decoded revert (`"execution reverted: NotAllowed"`), the decoded revert
  alone when the node sent no message, else `"reverted without a reason"` (with the
  return data's hex when it is too short to decode). 3.x could report `success=False`
  with `error=None`, and `error` was the node's message only. A successful call's
  `error` is `None`, and constructing one with an `error` raises `ValueError`.
  `revert_reason` is `None` exactly when the call succeeded. The messages of
  `SimulationResult.raise_for_failure` and `SimulationObservation.value`'s
  `SimulationError` are `failed: {error}`; drop any `call.error or "reverted"`
  fallback and match on `revert_reason`, not on the text of `error`.
- **`SimulatedCallResult` and `SimulationResult` are frozen**, so the rule above
  holds after construction too: assigning a field raises
  `dataclasses.FrozenInstanceError`. Build a changed copy with
  `dataclasses.replace(result, ...)` (a failed call's `error` is completed again).
  Both stay unhashable (`__hash__` is `None`, as before). The freeze is shallow:
  the `logs` / `calls` lists themselves can still be mutated.
- Saved config entries are checksummed when loaded; the MCP `vault_add` tool rejects
  a value that is not an address.

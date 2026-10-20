# Migrating to ipor-fusion 4.0

4.0 gives chain, market and role ids, the crosschain, HyperCore and Morpho ids,
vault/oracle/token addresses and transaction hashes a nominal type
(`typing.NewType`) across the public API, and checks the SDK, the examples and a
typed client with pyright's `reportArgumentType`. Other fields keep their types
(e.g. symbols, names and URLs stay `str`).

The wrappers do not change values: `ChainId(1)` is `1` at runtime. The
[behavior changes](#behavior-changes) below, however, apply to **every** caller,
type-checked or not. In particular, `fetch_market` and
`build_morpho_blue_market_json` no longer add a `0x` prefix themselves: pass
`parse_morpho_market_id(raw_id)`, never a bare 64-hex string. Type-checked code
(pyright, mypy, Pylance) also wraps literals and raw values as below. Values the SDK
returns already carry their type and go back in unwrapped.

The SDK's own types are importable from `ipor_fusion` (and `ipor_fusion.types`),
as are `USDC_TOKEN_INDEX` and `hip3_action_asset`. Third-party types are not
re-exported: `ChecksumAddress` and `HexAddress` come from `eth_typing`, and
`BlockIdentifier`, `Timestamp` and `TxParams` from `web3.types`. The wrappers label
values; they do not validate byte widths or address spelling.

## Building the types

| Type | From a literal or raw value |
|---|---|
| `ChainId` | `ChainId(1)` |
| `MarketId` | an `IporFusionMarkets` member, or `MarketId(n)` |
| `RoleId` | a `Roles` member (accepted as is), or `RoleId(n)`; `Roles.resolve(name)` returns one |
| `Decimals`, `Period`, `Amount`, ... | `Decimals(8)`, `Period(0)`, `Amount(n)` (unchanged) |
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
hex digits, with no surrounding whitespace; anything else raises `ValueError`. Strip
user input yourself before parsing it. `parse_morpho_market_id` still accepts hex
without `0x`.

`MessageId` covers both a CCIP `messageId` and a LayerZero `guid`. `AssetId` is the
crosschain executor's `bytes32` asset id; a Stargate pool's `uint16` asset id is
`StargateAssetId`. `DeploymentRequestId` is a factory's `uint256` dispatcher request;
a `bytes32` return (recall) request is `ReturnRequestId`.

## What changed, by area

**Public API.** 4.0 declares it: `ipor_fusion.__all__`, which now also exports the
names earlier reachable only by module path (`FusionFactory`, `FusionInstance`,
`fetch_market`, `resolve_token_symbol`, the crosschain codecs, ...). Import from
`ipor_fusion`: a module path outside the README's list (*Public API and stability*)
still works but is internal.

**Vault reads and validation.** The vault read is one pinned snapshot, and the
chain is always the context's:

- `fetch_vault_data(ctx, vault, *, block=None, lending=True, metadata=True)` takes
  the vault address (not a `PlasmaVault`) and no `chain_id`. It resolves one block
  (``block``, else a numeric `ctx.default_block`, else the latest) and runs every
  read, log scans included, on `ctx.at_block(block)`; `ctx` is not modified. The old
  ``chain_id=0`` switch is ``lending=False``; ``metadata=False`` reads no token
  symbol or decimals (the ERC20 holdings' symbols are then `"?"`), and with
  ``metadata=True`` the holdings are named by the same cached, batched read as
  `token_symbols`. `VaultData` gains `vault`, `chain_id`, `market_balances`, `erc20`,
  `token_symbols` and `token_decimals`; `WithdrawManagerData` gains
  `shares_to_release_assets` and `pending_request_assets`.
- `validate_vault(ctx, vault, *, block=None, metadata=False)` fetches (by default
  without token metadata) and validates; it no longer takes ``resolve_symbol``.
  ``metadata=True`` names the ERC20 holdings in the result and its findings. `validate_vault_state(state)` is
  pure: a custom `VaultState` also provides `vault`, `chain_id`, `market_balances`,
  `erc20` and the queue's `shares_to_release_assets`. `read_erc20_holdings` is gone:
  the holdings are `VaultData.erc20`.
- `build_vault_info_json(data, *, resolve_name=None)` renders from the snapshot and
  reads nothing; the chain and its label come from `data.chain_id`.
- `fetch_deployment_info(ctx, vault, *, api_key=None)` and
  `fetch_vault_lending_health(ctx, vault, *, balance_fuse_market_ids,
  market_substrates, aave_pools=None)` take the chain from `ctx`.
- `Web3Context(web3, chain_id, *, ...)` and `Web3Context.from_url(url, *, ...)` take
  their options by keyword. New: `Web3Context.at_block(block)` (a pinned copy) and
  `Call.call(ctx, *, block=None)` (see [Context-free wrappers](#context-free-wrappers)). `Web3Context.get_logs(to_block=None)` ends at
  the context's default block and `Web3Context.get_block(block=None)` returns it,
  both instead of ``"latest"``.
- `Web3Context.default_block` is read-only: `ctx.default_block = n` raises
  `AttributeError`. Pin a copy instead, `ctx = ctx.at_block(n)`; the old setter
  changed the block under every thread sharing the context.

Snapshot fields carry their units: `VaultData.total_assets`,
`underlying_balance_on_vault` and `market_balances` values are `Amount`;
`total_supply` and `supply_cap` are `Shares`; `share_decimals` / `asset_decimals` are `Decimals`;
`block_timestamp` and `deployment_timestamp` are `Timestamp`. `FeeData` fee fields
are `Fee`, `management_fee_last_update` a `Timestamp`, `unrealized_management_fee` an
`Amount`; `WithdrawManagerData.withdraw_window` is a `Period`, its fees `Fee`,
`shares_to_release` `Shares`, `last_release_funds_timestamp` a `Timestamp` and the
converted amounts `Amount`. A custom `VaultState` annotates the same.
`VaultValidation.block_timestamp` is a `Timestamp` and `asset_decimals` `Decimals`;
`Erc20Holding.decimals` / `balance` are `Decimals | None` / `Amount | None` and
`Erc20Holdings.value_in_asset` / `market_cached_value` `Amount`;
`VaultData.token_decimals` values and `resolve_token_decimals` are `Decimals | None`.
`PlasmaVault.total_supply()`, `balance_of()` and `get_total_supply_cap()` return
`Shares` (vault shares), and `transfer` / `approve` / `transfer_from` /
`set_total_supply_cap` take `Shares`; `ERC20` keeps `Amount`. The supply cap is
now shown in share decimals labelled `shares` (``fusion vault info``) and its JSON
``formatted`` uses `share_decimals`; it was formatted with the asset's decimals.

`Web3Context.from_url(..., max_concurrent_requests=8)` bounds the RPC requests in
flight across every thread using the context (``None`` lifts the bound); the vault
reads fan out on nested thread pools, and the bound is what a rate-limited provider
sees. A context built from your own `Web3` object is not bounded unless you add the
same middleware.

`ensure_supported_chain` takes `ChainId`. `fetch_unpriceable_priced_tokens(ctx, *,
price_oracle, underlying, balance_fuses, market_substrates, morpho_positions)` takes
everything after `ctx` by keyword, the oracle and underlying as `ChecksumAddress`.
`resolve_token_symbol`, `resolve_token_decimals`,
`get_contract_name` and `get_deployment_tx` take `HexAddress` (a checksum address
fits) and `api_key` by keyword only (`get_contract_name(chain_id, address, *,
api_key=None, cache=None)`, `get_deployment_tx(chain_id, address, *, api_key=None)`),
and the `resolve_name` / `resolve_symbol` callbacks receive one (a callback
typed for `str` still fits). Market-keyed data is keyed by `MarketId`:
`VaultData.market_substrates`, `dependency_graph`, `morpho_positions`,
`aave_positions`, `market_balances`; the `VaultState` protocol, `fetch_vault_lending_health`, `fetch_unpriceable_priced_tokens`
and the `dependency_graph` helpers (`compute_update_reach`, `compute_update_groups`,
`find_orphan_fuse_markets`, `find_markets_missing_erc20_dependency`).
`find_orphan_fuse_markets` returns `dict[MarketId, list[FuseAddress]]`, so its
fuses go into `PlasmaVault.remove_fuses` as they are.

`VaultData.price_oracle_addr` is renamed `price_oracle`, like its sibling
address fields (a custom `VaultState` renames it too).
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
`HexAddress | None` (`None`, no longer `""`, when a substrate carries no address).
`LegacyPriceOracleMiddleware` takes `base_currency_decimals: Decimals`, and
`PriceOracleMiddleware.base_currency_decimals()` returns `Call[Decimals]`.
`get_logs_adaptive(chain_id=)` and `LogSource.get_logs` take `ChainId`;
`quote_ccip_native_fee(block=)` and `OracleMappingReader.asset_source_events` take a
`BlockIdentifier`.

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

The vault parameter is named `vault` throughout: `validate_vault`,
`fetch_deployment_info`, `fetch_vault_lending_health`, `build_oracle_mapping`,
`read_external_state`, `ExternalStateExecutor.for_vault`, `read_hypercore_nav`,
`read_hypercore_vault_state`, `read_hypercore_transfer_snapshot`,
`read_hypercore_evm_exit_ceiling` and `resolve_access_manager` renamed
``vault_address`` to ``vault``; only a call that passes it by keyword changes.

**Simulation and factory records.** `VaultSimulator(web3, *, vault=None, alpha=None,
block=...)` takes every argument after `web3` by keyword, and `vault` and `alpha` are
optional: a simulator that only observes, deploys or sends `execute_call(call,
from_=...)` needs neither, so drop a zero-address placeholder. `execute()` without
`vault` and `alpha` raises `ValueError`, as does `execute_call` without `from_` or
`alpha`. `FusionInstance.from_dict(data)` rebuilds a stored instance (the
`dataclasses.asdict` form) with every field parsed; use it instead of
`FusionInstance(**data)`, which passes raw strings into the role-typed fields.

**Transactions, LayerZero and constants.** `Web3Context.build_transaction` and
`Call.build_transaction` return web3's `TxParams` (the same keys; `data` is `0x` hex,
the fee fields are `Wei`). LayerZero endpoint ids are `LayerZeroEid`:
`StargateChain.eid`, `Packet.src_eid` / `dst_eid`, `EnforcedOptionParam.eid`,
`executor_eid()` and `eid_of()` return it and `chain_id_of`, `peers`, `lz_receive_calldata(src_eid=)`
and the Stargate fuse's `eid=` take it (`LayerZeroEid(30101)`). CCIP chain
selectors are `CcipChainSelector`: `CcipChain.chain_selector`,
`CcipRouteConfig.chain_selector`, `CcipTokenLane.chain_selector`, the `MessageV1`,
`CcipMessageSent` and `Any2EVMMessage` selectors and `CcipRouter.get_off_ramps()`
carry it, and `chain_id_of_selector`, `CcipRouter.is_chain_supported` /
`get_on_ramp`, `CcipTokenPool.is_supported_chain` and `ccip_token_lane` take it
(`CcipChainSelector(4949039107694359620)`).
`encode_oft_compose_msg(*, nonce=, src_eid=, amount_ld=, compose_msg=)` takes
keyword arguments only. `ZERO_ADDRESS` is a `ChecksumAddress` (same value).

**Chain registry and removed aliases.** `CHAIN_NAMES`, `CHAIN_NAME_TO_ID`,
`SUPPORTED_CHAIN_IDS`, `GET_LOGS_RANGE_HINTS`, `BLOCK_EXPLORER_URLS`,
`MORPHO_BLUE_ADDRESSES`, `AAVE_V3_POOL`, `PUBLIC_ALLOCATOR_ADDRESSES` and
`HYPERCORE_PENDING_READERS` are keyed by `ChainId` (`HYPEREVM_CHAIN_ID` is one) and
`explorer.SUPPORTED_CHAINS` is a `frozenset[ChainId]`, so
`ensure_supported_chain(CHAIN_NAME_TO_ID["ethereum"])` type-checks;
`PUBLIC_ALLOCATOR_ADDRESSES` values are `ChecksumAddress`. `IporFusionMarkets.RWA`
is gone: market 50 is `IporFusionMarkets.EXTERNAL_STATE`. `MORPHO_BLUE_ADDRESS` is
gone: use `morpho_blue_address(chain_id)`.

**Markets and substrates.** Every `IporFusionMarkets` member is a `MarketId`.
`decode_substrate`, `market_name`, `format_market_label` take `MarketId`.

**Access.** `AccessManager.has_role`, `grant_role`, `revoke_role`,
`read_accounts_with_role` take `RoleId | Roles`; `RoleAccount.role_id` is `RoleId`.

**Morpho.** `MorphoBlueMarketId` now always carries `0x`; build it with
`parse_morpho_market_id` instead of `MorphoBlueMarketId("<64 hex>")`. `fetch_market`,
`build_morpho_blue_market_json` take it; `MorphoApiMarket.market_id`,
`VaultV1MarketAllocation.market_id`, `VaultV2Cap.market_id`,
`MorphoPositionBreakdown.market_id` (as read from substrates) and the keys of
`VaultV1Info.public_allocator_flow_caps` are canonical. `fetch_market`, `fetch_vault`,
`morpho_blue_address`, `build_*_json` take `ChainId`; `fetch_vault` takes a
`ChecksumAddress`. `build_morpho_blue_market_json(*, market_id, chain_id, params,
state, rates, api_market=None, api_error=None, public_allocator=None)` takes every
argument by keyword, `public_allocator` as `ChecksumAddress | None`.
Every address on the `morpho_api` records is `ChecksumAddress`;
optional ones (`curator`, `guardian`, `fee_recipient`, `performance_fee_recipient`,
`management_fee_recipient`, `inner_vault`, `VaultFlowCap.admin`, the `VaultV2Cap`
market params) are `ChecksumAddress | None`.

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
`lz_compose_calldata(guid=)` take a `MessageId`. Decoded event values
(`ccip_events` → `CcipEvent.values`) stay a generic mapping: convert a value
before passing it on.

**HyperCore (preview).** The fuses, `HyperCoreSubstrates`, `HyperCoreReader`,
`read_hypercore_transfer_snapshot` and the helpers `system_address`, `precompile_index_of`
(3.x `read_index_of`, renamed: it reads nothing),
`perp_dex_of`, `is_native_perp_asset`, `is_hip3_asset` take the HyperCore ids;
`HyperCorePerpAssetInfo`, `HyperCorePerpMarket`, `HyperCoreSpotLeg`,
`HyperCorePendingState` and `HyperCoreTransferSnapshot` carry them.
`HyperCorePendingReader(vault, reader)` takes the vault's `PlasmaVaultAddress`, not a
`PlasmaVault` wrapper.

**External state.** `ExternalStateExecutor.proposal_hash` returns and `confirm_balance`
takes a `ProposalHash`; `BalanceProposal.proposal_hash` and `NavMark.proposal_hash`
are `ProposalHash`; `ExternalStateEvent.transaction_hash` is a `TxHash`.
`ExternalStateVaultState.last_checked_custodian_timestamp`,
`BalanceFuseSnapshot.last_custodian_update_timestamp`, `last_updated()` and
`get_oldest_update_timestamp()` are web3's `Timestamp`.

**Errors and explorer.** `TransactionError(tx_hash=)` takes a `TxHash`,
`LogScanError(address=)` a `ChecksumAddress`; `get_revert_reason` takes a `TxHash`;
`get_deployment_tx` returns a `TxHash`
(a malformed hash from Etherscan is reported as the error `malformed-tx-hash`).

**MCP response models** (Python constructors only; the JSON schemas are unchanged
except below): `chain_id` fields are `ChainId`, `role_id` `RoleId`, `market_id`
`MarketId`. Calling an MCP tool function directly from Python takes `ChainId`.

**Fuse base.** `Fuse`, `StakeFuse` and the shared validators and substrate packers
live in `ipor_fusion.core.fuse`; `ipor_fusion.fuses.base`, `ipor_fusion.fuses` and
`ipor_fusion` re-export them, so existing imports keep working. Fuse equality and
hash now cover every attribute a fuse stores, not only its class and address (see
[Behavior changes](#behavior-changes)).

**Crosschain fuse encoders moved.** The crosschain market encoders moved from
`ipor_fusion.fuses.crosschain` into the crosschain package, and
`ipor_fusion.fuses` no longer re-exports them. There is no shim: the old paths
raise `ImportError`. The top-level names (`from ipor_fusion import ...`) are
unchanged.

| 3.x import | 4.0 import |
|---|---|
| `ipor_fusion.fuses.crosschain` | `ipor_fusion.crosschain.fuses` (or `ipor_fusion.crosschain`) |
| `ipor_fusion.fuses.crosschain.base` | `ipor_fusion.crosschain.fuses.base` |
| `ipor_fusion.fuses.crosschain.ccip` | `ipor_fusion.crosschain.fuses.ccip` |
| `ipor_fusion.fuses.crosschain.stargate` | `ipor_fusion.crosschain.fuses.stargate` |
| `from ipor_fusion.fuses import CcipCrosschainSupplyFuse` (and the other crosschain encoders, `SendParams`, `CrosschainSubstrate*`, `crosschain_market_id`) | `from ipor_fusion.crosschain import ...` or `from ipor_fusion import ...` |

`ipor_fusion.crosschain` now exports the whole crosschain surface: besides the
codecs and wrappers, the encoders, `CrosschainLane`, `CcipLane`, `StargateLane`,
the transports and chains, `discover_deployment`, `open_lane(s)`, `LANES` and
`CrosschainSimulator`, which 3.x exported only from `ipor_fusion`.
`ipor_fusion.crosschain.ccip` and `.stargate` keep exporting only their codecs
and wrappers.

**Moved, still importable.** `FuseAction` lives in `ipor_fusion.core.fuse_action` and
`ZERO_ADDRESS` in `ipor_fusion.types`; both are re-exported from
`ipor_fusion.fuses.base` and `FuseAction` from `ipor_fusion`. The CLI's internal
`ipor_fusion.cli.vault_cmd` was split into `options`, `vault_info_view`,
`vault_markets_view` and `oracle_mapping_view` (private names, not API).

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
  argument. The nominal address types are unchanged.
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
  `price_oracle_middleware(ctx, oracle)`, `resolve_access_manager(ctx, vault)`,
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
  (`LaneHubState`) in one batch on `hub_ctx`, `lane.read_observation()` runs the
  observation on `spoke_ctx`. `CrosschainTransport.executor(address)`
  and `.dispatcher(address)` no longer take a context.
- Chain-bound decoding takes the chain explicitly:
  `ExternalStateExecutor.pending_proposals(account, *, chain_id)`,
  `parse_balance_proposed(log, *, chain_id)` and
  `find_balance_proposed(logs, account=None, *, chain_id)` require it (3.x fell back
  to the wrapper's context). The decoder captures it, so `.call(ctx)`, `Multicall3`
  and `VaultSimulator.observe` stamp the same hash for the same bytes, whatever
  context executes them. Pass `ctx.chain_id` for the chain you read from.
  `pending_proposals` now refuses an executor at the zero address when the Call is
  built, not when it is decoded. `mark_nav` takes the chain from its contexts.

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
| `read_index_of(asset)` | `precompile_index_of(asset)` |
| `ctx.default_block = n` | `ctx = ctx.at_block(n)` |
| `Multicall3(ctx, address)` | `Multicall3(ctx, address=address)` |
| `executor.pending_proposals(account)` | `executor.pending_proposals(account, chain_id=ctx.chain_id)` |
| `executor.parse_balance_proposed(log)` / `find_balance_proposed(logs)` | `...(log, chain_id=...)` / `...(logs, chain_id=...)` |
| `CcipLane(executor=, dispatcher=, spoke_chain_id=, ...)` | `CcipLane(hub_ctx=, spoke_ctx=, executor=, dispatcher=, ...)` (same for `StargateLane`) |
| `transport.executor(ctx, address)` / `transport.dispatcher(ctx, address)` | `transport.executor(address)` / `transport.dispatcher(address)` |

A service that caches wrappers per chain can now share one wrapper across chains and
snapshots; what it must not share is a `Call` result decoded with a chain-bound
argument (`chain_id=`) for another chain. An external signer still needs the real
target address (`call.to`) and builds the chain-qualified transaction itself, or
gets it from `call.build_transaction(ctx)`.

## Contract cache

Contract names, token symbols and decimals and deployment blocks are cached in a
`ContractCache`, injected instead of always written to `~/.cache`:

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
  `get_contract_name(chain_id, address, api_key=None, *, cache=None)` takes the cache
  explicitly (default: the file cache); pass `cache=ctx.cache` to share the
  context's policy. The CLI's text and JSON output name contracts through the
  context's cache.
- `build_vault_info_json(data)` without `resolve_name` names no contract (every
  `contract` is `"?"`) instead of reading names from the on-disk cache; pass
  `resolve_name=partial(get_contract_name, chain_id, api_key=..., cache=ctx.cache)`
  for the CLI's document.
- `ipor_fusion.cache` is a public module now and exports `ContractCache`,
  `FileContractCache`, `NullCache`, `CacheKey`, `CacheField`, `cache_key` and
  `default_contract_cache` (also from `ipor_fusion`). Its 3.x functions and constants
  are gone: `load_contract_cache`, `save_contract_cache`, `update_contract_cache`,
  `load_deployment_cache`, `update_deployment_cache`, `CACHE_DIR`, `CACHE_FILE` and
  `DEPLOYMENT_CACHE_FILE`. `cache_key(chain_id, address, field)` returns a `CacheKey`
  and raises `ValueError` for an address that is not 20 bytes of hex, so
  `get_contract_name` does too.

**File format.** One file, keys `<chain_id>:<lowercase address>:<field>` (`name`,
`symbol`, `decimals`, `deployment_block`, `deployment_timestamp`), string values. On
load, a 3.x contract name (`<chain_id>:<address>`) is kept as that chain's `name`;
3.x token entries (`symbol:<address>`, `decimals:<address>`) name no chain and are
discarded, so symbols and decimals are read again once per chain. `deployment_cache.json`
is no longer read: a vault's deployment block is resolved again (it needs an
Etherscan key) and the old file can be deleted.

**Behavior.**

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

## Behavior changes

- Two fuses are equal (and hash alike) only when they have the same class and the
  same stored state. `StakeFuse` subclasses (`GearboxStakeFuse`,
  `FluidInstadappStakingFuse`) with the same fuse address but different
  `staking_address` were equal and collapsed in a set or dict key; they are now
  distinct. A custom `Fuse` subclass is compared on its instance attributes, which
  must be hashable; drop an `__eq__` / `__hash__` override that only added them.
- The MCP `vault_info` document reports the vault address checksummed (the CLI
  already did).
- `SubstrateInfo.address` is `None` instead of `""` for a substrate without an
  address. Truthiness checks are unaffected and the SDK's JSON documents already
  omit it, but `dataclasses.asdict(info)` now gives `"address": None` and
  `info.address == ""` is false.
- The Uniswap V3 / Ramses V2 position events (`extract_new_position_events`,
  `extract_close_position_events`) return their addresses checksummed (they were the
  lowercase ABI-decoded strings).
- `fusion market meta-morpho --json` and the MCP `market_meta_morpho` tool report
  `null` instead of `""` for an absent curator, guardian or fee recipient; the MCP
  schema's `curator` is nullable.
- The Morpho API client checksums every address it returns (market, vault,
  allocation, adapter and cap records, and the `fusion market` / MCP market documents
  built from them), so string comparisons against the API's own spelling change.
- It raises `MorphoApiError` for a market id or address it parses (market and V1
  allocation ids, V1 flow-cap ids, vault/asset/owner/token/oracle/IRM/allocator
  addresses) when the value is invalid, or absent where the field is optional in the
  answer. Any other answer it cannot read (a missing key such as a `loanAsset`
  without `address`, a value of the wrong type, a body that is not a JSON object) and
  any transport failure also raise `MorphoApiError`, never `KeyError`, `TypeError` or
  `ValueError` (see [Errors](#errors)). When matching a market's own allocation and flow cap, an id that
  does not parse matches nothing, and a V1 flow cap without an id is skipped, as
  before. `--market` rejects a malformed id.
- `LendingMarketHealth.substrate_id` for a Morpho market is `0x`-prefixed (the JSON
  documents already were).
- `TransactionError.tx_hash` is `0x`-prefixed.
- Saved config entries are checksummed when loaded; the MCP `vault_add` tool rejects
  a value that is not an address.
- The contract cache keys every entry by chain and lowercase address and no longer
  caches an absence (see [Contract cache](#contract-cache)).

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
  them (`ipor_fusion.morpho_api`, `ipor_fusion.fuses.external_state_events`,
  `ipor_fusion.crosschain.ccip.indexer`) still import the same classes.
- `AccessManager.read_owner(ctx)` raises the new `OwnerNotFoundError` (a
  `LookupError`) when no account holds `OWNER_ROLE`, instead of an `IndexError`.
- `EmptyCallResultError` is no longer an eth_abi `InsufficientDataBytes` (nor a
  `DecodingError`). Code that caught either around `.call()` to tolerate a missing
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
- `VaultSimulator.run` (provider error from `eth_simulateV1`, or a result count that
  does not match the queued calls) and `CrosschainSimulator.relay` (not settled within
  `max_rounds`) raise the new `SimulationRunError`, still a `RuntimeError`.
- The Morpho API client raises `MorphoApiError` for every response it cannot read and
  for any `OSError` or `http.client.HTTPException` while fetching (it caught only
  `URLError` and `TimeoutError` before).
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
  fields; and `mark_nav` when the propose receipt it fetched has no such log or
  records another value or proposer. A log that is not the event or not from the
  executor, and `find_balance_proposed` over logs that hold no match, are still
  `ValueError`.
- `ccip_token_lane` raises the new `UnsupportedContractVersionError` (still a
  `ValueError`) for an OnRamp version whose layout is not verified.
- `quote_ccip_native_fee` raises `SimulationError` (label `"quote"`, with
  `revert_reason`) instead of `ValueError` when the call reverts before the CCIP fee
  check. A call that succeeds without sending a message is still a `ValueError`.

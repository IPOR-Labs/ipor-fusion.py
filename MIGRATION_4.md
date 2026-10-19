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

All types are importable from `ipor_fusion` (and `ipor_fusion.types`);
`ChecksumAddress` and `HexAddress` come from `eth_typing`,
`BlockIdentifier` from `web3.types`, and `USDC_TOKEN_INDEX` / `hip3_action_asset` from
`ipor_fusion.fuses.hypercore`. The wrappers label values; they do not validate byte
widths or address spelling.

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
| crosschain ids | `CommandId(b)`, `MessageId(b)`, `AssetId(b)`, `OperationId(b)`, `TransferId(b)`, `ReturnRequestId(b)`, `PositionSetHash(b)` (bytes32); `ProposalId(n)`, `DeploymentRequestId(n)`, `StargateAssetId(n)` (int) |
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

**Vault reads and validation.** `fetch_vault_data(chain_id=)`, `fetch_deployment_info`,
`build_vault_info_json`, `ensure_supported_chain` and `fetch_vault_lending_health`
take `ChainId`. The vault is a `PlasmaVaultAddress` (see *Address roles*) in
`validate_vault` and `fetch_deployment_info`; `build_vault_info_json(ctx, plasma_vault,
data, *, chain_id, chain_label)` takes no address, takes the chain by keyword, and
labels the document with `plasma_vault.address` (a test that mocks `PlasmaVault`
sets `.address`). `fetch_unpriceable_priced_tokens` takes the oracle and
underlying as `ChecksumAddress`. `resolve_token_symbol`, `resolve_token_decimals`,
`get_contract_name` and `get_deployment_tx` take `HexAddress` (a checksum address
fits), and the `resolve_name` / `resolve_symbol` callbacks receive one (a callback
typed for `str` still fits). Market-keyed data is keyed by `MarketId`:
`VaultData.market_substrates`, `dependency_graph`, `morpho_positions`,
`aave_positions`; the `VaultState` protocol, `validate_vault_state`
(`market_balances`), `fetch_vault_lending_health`, `fetch_unpriceable_priced_tokens`
and the `dependency_graph` helpers (`compute_update_reach`, `compute_update_groups`,
`find_orphan_fuse_markets`, `find_markets_missing_erc20_dependency`).

`VaultData.asset`, `price_oracle_addr`, `rewards_manager`, `withdraw_manager`,
`FeeData.fee_manager` / `ipor_dao_fee_recipient` and `VaultValidation.vault` /
`asset` are `ChecksumAddress` (`VaultValidation.vault` a `PlasmaVaultAddress`);
`VaultData.access_manager` is an `AccessManagerAddress`, `fuses` / `instant_fuses`
and the `fuse_markets` keys are `FuseAddress`, `balance_fuses` is
`list[BalanceFuse]`, and `token_prices_usd` is keyed by lowercase `HexAddress`. A
custom `VaultState` implementation annotates `asset` and `price_oracle_addr` as
`ChecksumAddress`, `fuse_markets` keys as `FuseAddress`, and its
`unpriceable_priced_tokens` items' `market_id` as `MarketId` (`UnpriceableToken`).
The Uniswap V3 / Ramses V2 position events' `version`, `sender` and `recipient` are
`ChecksumAddress`. `VaultValidation.chain_id` is `ChainId`;
`LendingMarketHealth.market_id` and `MiddlewarePricedToken.market_id` are
`MarketId`; `Erc20Holding.address` is `HexAddress`, and `SubstrateInfo.address` is
`HexAddress | None` (`None`, no longer `""`, when a substrate carries no address).
`LegacyPriceOracleMiddleware` takes `base_currency_decimals: Decimals`.
`get_logs_adaptive(chain_id=)` and `LogSource.get_logs` take `ChainId`;
`quote_ccip_native_fee(block=)` and `OracleMappingReader.asset_source_events` take a
`BlockIdentifier`.

**Address roles.** `PlasmaVault(ctx, address)`, `PlasmaVault.encoder(address)`,
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
`LaneFuses` and the HyperCore balance-fuse argument take a `FuseAddress`, and
`get_fuses()`, `get_instant_withdrawal_fuses()`, `get_rewards_fuses()` and
`VaultData.fuses` / `instant_fuses` / `fuse_markets` keys return it.
`AccessManager(ctx, address)` takes an `AccessManagerAddress`;
`get_access_manager_address()`, `VaultData.access_manager` and
`FusionInstance.access_manager` return one. `resolve_access_manager` still takes any
`ChecksumAddress`: it is what checks that the address is a vault.

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
and the Stargate fuse's `eid=` take it (`LayerZeroEid(30101)`).
`encode_oft_compose_msg(*, nonce=, src_eid=, amount_ld=, compose_msg=)` takes
keyword arguments only. `ZERO_ADDRESS` is a `ChecksumAddress` (same value).

**Markets and substrates.** Every `IporFusionMarkets` member is a `MarketId`.
`decode_substrate`, `market_name`, `format_market_label` take `MarketId`.

**Access.** `AccessManager.has_role`, `grant_role`, `revoke_role`,
`get_accounts_with_role` take `RoleId | Roles`; `RoleAccount.role_id` is `RoleId`.

**Morpho.** `MorphoBlueMarketId` now always carries `0x`; build it with
`parse_morpho_market_id` instead of `MorphoBlueMarketId("<64 hex>")`. `fetch_market`,
`build_morpho_blue_market_json` take it; `MorphoApiMarket.market_id`,
`VaultV1MarketAllocation.market_id`, `VaultV2Cap.market_id`,
`MorphoPositionBreakdown.market_id` (as read from substrates) and the keys of
`VaultV1Info.public_allocator_flow_caps` are canonical. `fetch_market`, `fetch_vault`,
`morpho_blue_address`, `build_*_json` take `ChainId`; `fetch_vault` takes a
`ChecksumAddress`. Every address on the `morpho_api` records is `ChecksumAddress`;
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
`read_hypercore_transfer_snapshot` and the helpers `system_address`, `read_index_of`,
`perp_dex_of`, `is_native_perp_asset`, `is_hip3_asset` take the HyperCore ids;
`HyperCorePerpAssetInfo`, `HyperCorePerpMarket`, `HyperCoreSpotLeg`,
`HyperCorePendingState` and `HyperCoreTransferSnapshot` carry them.

**External state.** `ExternalStateExecutor.proposal_hash` returns and `confirm_balance`
takes a `ProposalHash`; `BalanceProposal.proposal_hash` and `NavMark.proposal_hash`
are `ProposalHash`; `ExternalStateEvent.transaction_hash` is a `TxHash`.

**Errors and explorer.** `TransactionError(tx_hash=)` takes a `TxHash`,
`LogScanError(address=)` a `ChecksumAddress`; `get_revert_reason` takes a `TxHash`;
`get_deployment_tx` returns a `TxHash`
(a malformed hash from Etherscan is reported as the error `malformed-tx-hash`).

**MCP response models** (Python constructors only; the JSON schemas are unchanged
except below): `chain_id` fields are `ChainId`, `role_id` `RoleId`, `market_id`
`MarketId`. Calling an MCP tool function directly from Python takes `ChainId`.

**Moved, still importable.** `FuseAction` lives in `ipor_fusion.core.fuse_action` and
`ZERO_ADDRESS` in `ipor_fusion.types`; both are re-exported from
`ipor_fusion.fuses.base` and `FuseAction` from `ipor_fusion`. The CLI's internal
`ipor_fusion.cli.vault_cmd` was split into `options`, `vault_info_view`,
`vault_markets_view` and `oracle_mapping_view` (private names, not API).

## Behavior changes

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
- The on-disk caches (`~/.cache/ipor-fusion/`) key addresses lowercased; older entries
  are rekeyed on load, so a contract name cached for one spelling now also resolves
  for the other.

## Errors

Every exception class the SDK defines now derives from `IporFusionError` and is
importable from `ipor_fusion`; the builtin bases they had are kept. The rule the
changes below apply is: a caller's mistake (a bad argument, or an operation the
object was not built for) raises a plain `ValueError`; a chain, provider or remote
API answering something the SDK cannot use raises an `IporFusionError` subclass.
4.0 applies it to the paths listed here, not yet everywhere: the HyperCore NAV
reader (`read_hypercore_nav`) still raises `ValueError` for a failed precompile read,
a missing oracle price or an unsupported dex configuration,
`fetch_verifier_result` can still raise `KeyError` or `ValueError` on a malformed
indexer answer, and `erc20_balance_slot` raises `ValueError` when no slot answers.

- `MorphoApiError` (still a `RuntimeError`), `ExternalStateEventDecodeError` (still a
  `ValueError`) and `VerifierResultUnavailable` (still a `LookupError`) now derive
  from `IporFusionError`, so an `except IporFusionError` placed before their own
  handlers catches them first. `MorphoApiError` lives in `ipor_fusion.errors` and is
  exported from `ipor_fusion`; `ipor_fusion.morpho_api.MorphoApiError` still works.
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
- `Call.call()` and `Call.decode()` on a write-only `Call` raise `ValueError`, not
  `RuntimeError`. `CrosschainSimulator.relay` raises `ValueError`, not
  `RuntimeError`, for a message to a chain that was not added.
- `VaultSimulator.run` (provider error from `eth_simulateV1`, or a result count that
  does not match the queued calls) and `CrosschainSimulator.relay` (not settled within
  `max_rounds`) raise the new `SimulationRunError`, still a `RuntimeError`.
- The Morpho API client raises `MorphoApiError` for every response it cannot read and
  for any `OSError` or `http.client.HTTPException` while fetching (it caught only
  `URLError` and `TimeoutError` before).

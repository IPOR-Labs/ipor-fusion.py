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
| `HexAddress` | any `ChecksumAddress`, or `parse_hex_address(value)` (20 bytes or hex in any spelling → lowercase `0x` form) |
| `MorphoBlueMarketId` | `parse_morpho_market_id(value)`: 32 bytes or hex in any spelling, always `0x` + 64 lowercase digits |
| `TxHash` | `parse_tx_hash(value)` (32 bytes or hex in any spelling) |
| crosschain ids | `CommandId(b)`, `MessageId(b)`, `AssetId(b)`, `OperationId(b)`, `TransferId(b)`, `ReturnRequestId(b)`, `PositionSetHash(b)` (bytes32); `ProposalId(n)`, `DeploymentRequestId(n)`, `StargateAssetId(n)` (int) |
| HyperCore ids | `HyperCoreTokenIndex(n)` (or `USDC_TOKEN_INDEX`), `HyperCoreAsset(n)` (or `hip3_action_asset(dex, index)`), `HyperCoreOrderId(n)`, `HyperCoreClientOrderId(n)`, `MarginTableId(n)`, `HyperCoreActionId(n)`, `PayloadHash(b)` |
| external state | `ProposalHash(b)` |
| `BlockIdentifier` | a block number, `"latest"` / `"pending"` / ..., or a block hash |

`MessageId` covers both a CCIP `messageId` and a LayerZero `guid`. `AssetId` is the
crosschain executor's `bytes32` asset id; a Stargate pool's `uint16` asset id is
`StargateAssetId`. `DeploymentRequestId` is a factory's `uint256` dispatcher request;
a `bytes32` return (recall) request is `ReturnRequestId`.

## What changed, by area

**Vault reads and validation.** `fetch_vault_data(chain_id=)`, `fetch_deployment_info`,
`build_vault_info_json`, `ensure_supported_chain`, `fetch_vault_lending_health` take
`ChainId`; vault and oracle addresses
(`validate_vault`, `fetch_deployment_info`, `build_vault_info_json`,
`fetch_unpriceable_priced_tokens`) take `ChecksumAddress`; `resolve_token_symbol`,
`resolve_token_decimals`, `get_contract_name`, `get_deployment_tx` take `HexAddress`
(a checksum address fits). The `resolve_name` / `resolve_symbol` callbacks receive a
`HexAddress` (a callback typed for `str` still fits). Market-keyed data is keyed by
`MarketId`: `VaultData.market_substrates`, `dependency_graph`, `fuse_markets`,
`morpho_positions`, `aave_positions`; the `VaultState` protocol, `validate_vault_state`
(`market_balances`), `fetch_vault_lending_health`, `fetch_unpriceable_priced_tokens`
and the `dependency_graph` helpers (`compute_update_reach`, `compute_update_groups`,
`find_orphan_fuse_markets`, `find_markets_missing_erc20_dependency`).
A custom `VaultState` implementation also annotates `asset` as `ChecksumAddress` and
its `unpriceable_priced_tokens` items' `market_id` as `MarketId`
(`UnpriceableToken`), and `price_oracle_addr` / `fuse_markets` keys as
`ChecksumAddress`. `VaultData.asset`, `access_manager`, `price_oracle_addr`,
`rewards_manager`, `withdraw_manager`, `fuses` and `instant_fuses` (and the
`fuse_markets` keys), `FeeData.fee_manager` / `ipor_dao_fee_recipient`, and
`VaultValidation.vault` / `asset` are `ChecksumAddress`; `VaultData.balance_fuses`
is `list[BalanceFuse]` and `token_prices_usd` is keyed by `HexAddress` (lowercase);
the Uniswap V3 / Ramses V2 position events' `version`, `sender` and `recipient` are
`ChecksumAddress`;
`VaultValidation.chain_id` is `ChainId`; `LendingMarketHealth.market_id` and
`MiddlewarePricedToken.market_id` are `MarketId`; `Erc20Holding.address` is
`HexAddress`, and `SubstrateInfo.address` is `HexAddress | None` (`None`, no longer
`""`, when a substrate carries no address).
`LegacyPriceOracleMiddleware` takes `base_currency_decimals: Decimals`.
`get_logs_adaptive(chain_id=)` and `LogSource.get_logs` take `ChainId`;
`quote_ccip_native_fee(block=)` and `OracleMappingReader.asset_source_events` take a
`BlockIdentifier`.

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
  answer. A key the parser requires can instead raise `KeyError`, e.g. a `loanAsset`
  without `address`. When matching a market's own allocation and flow cap, an id that
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

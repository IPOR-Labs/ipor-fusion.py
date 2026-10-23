"""The declared public API (README, "Public API and stability").

The snapshots below are the contract: adding or removing a name from any
``__all__``, or from the names a public module keeps importable by its path,
must change them here too, so every change to the surface is deliberate and
visible in review.
"""

import ast
import importlib
import inspect
import pkgutil
import re
from pathlib import Path
from types import ModuleType

import pytest

import ipor_fusion

# Subpackages that keep their own namespace rather than re-export from the top
# level: the packaged docs (``ipor_fusion.guide.QUICKSTART``) and the preview
# features (``PREVIEW_FEATURES``), whose names may change in a minor release.
_PREVIEW = frozenset(
    {
        "ipor_fusion.crosschain",
        "ipor_fusion.crosschain.ccip",
        "ipor_fusion.crosschain.fuses",
        "ipor_fusion.crosschain.stargate",
        "ipor_fusion.hypercore",
    }
)
_NOT_REEXPORTED = frozenset({"ipor_fusion.guide"}) | _PREVIEW

_ALL_SNAPSHOT: dict[str, frozenset[str]] = {
    "ipor_fusion": frozenset(
        {
            "AaveBreakdownJson",
            "AaveV3BorrowFuse",
            "AaveV3FuseReader",
            "AaveV3PoolAddressesProvider",
            "AaveV3PositionBreakdown",
            "AaveV3Reader",
            "AaveV3ReserveTokens",
            "AaveV3SupplyFuse",
            "AaveV3UserAccountData",
            "AaveV4OracleReader",
            "AaveV4SpokeReader",
            "AaveV4UserAccountData",
            "AccessManager",
            "AccessManagerAddress",
            "AccountOverride",
            "AccountRequest",
            "AddressListJson",
            "AddressLookupJson",
            "AddressMatchJson",
            "AddressNameJson",
            "AddressNamesJson",
            "AddressNotInRegistryError",
            "AddressRegistry",
            "AddressRegistryError",
            "AggregatorDetailJson",
            "AggregatorFeedDetailJson",
            "Amount",
            "AmountJson",
            "AssetPriceSource",
            "AssetSource",
            "AsyncActionFuse",
            "AsyncActionSubstrates",
            "BalanceFuse",
            "BalanceFuseJson",
            "BalanceFuseSnapshot",
            "BalanceProposal",
            "BalanceSlotNotFoundError",
            "BatchValue",
            "BlockOverrides",
            "BreakdownAmountJson",
            "CHAIN_NAMES",
            "CHAIN_NAME_TO_ID",
            "CUSTOM_ERRORS",
            "CacheField",
            "CacheKey",
            "Call",
            "CallPermission",
            "ChainId",
            "ChainlinkDetailJson",
            "ChangelogEntry",
            "Check",
            "CloneArgs",
            "CompoundV3Reader",
            "CompoundV3SupplyFuse",
            "ContextManager",
            "ContractCache",
            "ContractNotFoundError",
            "CrosschainDiscoveryError",
            "DOCS",
            "Decimals",
            "DeltaJson",
            "DependencyGraphJson",
            "DeploymentErrorJson",
            "DeploymentJson",
            "DualXrefDetailJson",
            "ERC20",
            "ERC4626SupplyFuse",
            "EXTERNAL_STATE_EVENTS",
            "EXTERNAL_STATE_EVENT_CLASSES",
            "EXTERNAL_STATE_EVENT_SPECS",
            "EXTERNAL_STATE_TOPICS",
            "EmptyCallResultError",
            "Erc20BalanceJson",
            "Erc20Holding",
            "Erc20Holdings",
            "Erc20HoldingsJson",
            "Erc4626DetailJson",
            "EulerSwapDynamicParams",
            "EulerSwapInitialState",
            "EulerSwapStaticParams",
            "EulerV2BatchFuse",
            "EulerV2BatchItem",
            "EulerV2BorrowFuse",
            "EulerV2CollateralFuse",
            "EulerV2ControllerFuse",
            "EulerV2SupplyFuse",
            "EulerV2SwapDeployFuse",
            "EulerV2SwapReconfigureFuse",
            "EulerV2SwapRegistryFuse",
            "ExternalStateAction",
            "ExternalStateAssetRescuedArgs",
            "ExternalStateAssetRescuedEvent",
            "ExternalStateBalanceFuseLastCustodianTimestampUpdatedEvent",
            "ExternalStateBalanceFuseLastCustodianConfirmCountUpdatedEvent",
            "ExternalStateOperationFuseSweepSurplusEvent",
            "ExternalStateBalanceFuseLastTotalBalanceUpdatedEvent",
            "ExternalStateBigChangeDetectedArgs",
            "ExternalStateBigChangeDetectedEvent",
            "ExternalStateCustodianTimestampUpdatedArgs",
            "ExternalStateCustodianConfirmCountUpdatedArgs",
            "ExternalStateOperationFuseSweepSurplusArgs",
            "ExternalStateError",
            "ExternalStateEvent",
            "ExternalStateEventArgs",
            "ExternalStateEventDecodeError",
            "ExternalStateEventSpec",
            "ExternalStateExecutor",
            "ExternalStateExecutorArgs",
            "ExternalStateExecutorCreatedEvent",
            "ExternalStateExecutorDeployedEvent",
            "ExternalStateOperationArgs",
            "ExternalStateOperationFuse",
            "ExternalStateOperationFuseEnterEvent",
            "ExternalStateOperationFuseExitEvent",
            "ExternalStateSubstrates",
            "ExternalStateTotalBalanceUpdatedArgs",
            "ExternalStateUnpausedArgs",
            "ExternalStateUnpausedEvent",
            "ExternalStateVaultState",
            "Fee",
            "FeeAccount",
            "FeeData",
            "FeeManager",
            "FeesJson",
            "FileContractCache",
            "Finding",
            "FindingJson",
            "FluidInstadappStakingFuse",
            "FluidInstadappSupplyFuse",
            "Fuse",
            "FuseAction",
            "FuseAddress",
            "FuseJson",
            "FusionFactory",
            "FusionInstance",
            "GET_LOGS_RANGE_HINTS",
            "GearboxStakeFuse",
            "GearboxSupplyFuse",
            "HYPEREVM_CHAIN_ID",
            "HealthCheckJson",
            "HighWaterMarkJson",
            "HighWaterMarkPerformanceFee",
            "HyperCoreEventDecodeError",
            "HyperCoreJson",
            "HyperCorePendingJson",
            "HyperCorePerpLegJson",
            "HyperCorePerpMarketJson",
            "HyperCoreSpotLegJson",
            "HyperCoreValuationError",
            "InFlightLimit",
            "IporFusionError",
            "IporFusionMarkets",
            "LegacyPriceOracleMiddleware",
            "LendingHealthJson",
            "LendingMarketHealth",
            "LendingMarketJson",
            "Liquidity",
            "LogScanError",
            "LogSource",
            "LogSourceUnavailable",
            "MAX_UINT256",
            "MULTICALL3_ADDRESS",
            "ManagementFeeData",
            "ManagersJson",
            "MappingStatus",
            "MarketId",
            "MerklClaimWrapperFuse",
            "MetaMorphoAdapterJson",
            "MetaMorphoAllocationJson",
            "MetaMorphoCapJson",
            "MetaMorphoFlowCapJson",
            "MetaMorphoPublicAllocatorJson",
            "MetaMorphoV1Json",
            "MetaMorphoV2Json",
            "MiddlewareFallbackDetailJson",
            "MiddlewarePricedToken",
            "MorphoApiError",
            "MorphoApiMarket",
            "MorphoAssetJson",
            "MorphoBlueMarketId",
            "MorphoBlueMarketJson",
            "MorphoBorrowFuse",
            "MorphoBreakdownJson",
            "MorphoClaimFuse",
            "MorphoCollateralFuse",
            "MorphoFeedDetailJson",
            "MorphoFlashLoanFuse",
            "MorphoFlowCapConfigJson",
            "MorphoMarket",
            "MorphoMarketNotFoundError",
            "MorphoMarketParams",
            "MorphoMarketParamsJson",
            "MorphoMarketRates",
            "MorphoMarketRatesJson",
            "MorphoMarketStateJson",
            "MorphoPosition",
            "MorphoPositionBreakdown",
            "MorphoReader",
            "MorphoSupplyFuse",
            "MorphoSupplyingVaultJson",
            "MorphoVaultAssetJson",
            "Multicall3",
            "Multicall3Batch",
            "NodeStatus",
            "NotPlasmaVaultError",
            "NullCache",
            "OracleAsset",
            "OracleAssetJson",
            "OracleMapping",
            "OracleMappingJson",
            "OracleMappingReader",
            "OracleNode",
            "OracleNodeJson",
            "OraclePrice",
            "OraclePriceJson",
            "OwnerNotFoundError",
            "PREVIEW_FEATURES",
            "PREVIEW_STATEMENT",
            "PUBLIC_ALLOCATOR_ADDRESSES",
            "PendingRequestJson",
            "PendingRequestsInfo",
            "PerformanceFeeData",
            "CustodianMarker",
            "Period",
            "PlasmaVault",
            "PlasmaVaultAddress",
            "PositionData",
            "PreviewFeature",
            "Price",
            "PriceOracleMiddleware",
            "PriceOracleMiddlewareManager",
            "PricedAmountJson",
            "ProposalHash",
            "REGISTRY_CHAIN_IDS",
            "REGISTRY_REPOSITORY",
            "RamsesClaimFuse",
            "RamsesEvents",
            "RamsesNewPositionEvent",
            "RamsesV2CollectFuse",
            "RamsesV2ModifyPositionFuse",
            "RamsesV2NewPositionFuse",
            "RamsesV2Position",
            "RamsesV2Reader",
            "RawLog",
            "RecipientFee",
            "RecipientFeeJson",
            "Reconciliation",
            "ReconciliationJson",
            "RewardsManager",
            "RoleAccount",
            "RoleAccountJson",
            "RoleAccountsJson",
            "RoleId",
            "RoleStatus",
            "Roles",
            "SUPPORTED_CHAIN_IDS",
            "Severity",
            "SharePriceJson",
            "Shares",
            "SimulatedCallResult",
            "SimulatedLog",
            "SimulationError",
            "SimulationObservation",
            "SimulationResult",
            "SimulationRunError",
            "SourceDetailJson",
            "StakeFuse",
            "StateOverrideProvider",
            "SubstrateEntryJson",
            "SubstrateInfo",
            "Tick",
            "TokenId",
            "TransactionError",
            "TxHash",
            "UniswapV3ClosePositionEvent",
            "UniswapV3CollectFuse",
            "UniswapV3Events",
            "UniswapV3ModifyPositionFuse",
            "UniswapV3NewPositionEvent",
            "UniswapV3NewPositionFuse",
            "UniswapV3Position",
            "UniswapV3Reader",
            "UniswapV3SwapFuse",
            "UniversalTokenSwapperAbi",
            "UniversalTokenSwapperFuse",
            "UniversalTokenSwapperSubstrates",
            "UnpriceableCheck",
            "UnpriceableToken",
            "UnreadableLendingMarket",
            "UnreadableLendingMarketJson",
            "UnsupportedChainError",
            "UnsupportedContractVersionError",
            "UnsupportedVaultVersionError",
            "UsdAmountJson",
            "VaultAllocation",
            "VaultAssetJson",
            "VaultData",
            "VaultFlowCap",
            "VaultInfoJson",
            "VaultLendingHealth",
            "VaultLinksJson",
            "VaultSimulator",
            "VaultState",
            "VaultV1Info",
            "VaultV1MarketAllocation",
            "VaultV2Adapter",
            "VaultV2Cap",
            "VaultV2Info",
            "VaultValidation",
            "VaultValidationJson",
            "Verdict",
            "VerifierResultUnavailable",
            "VestingData",
            "Wad",
            "Web3Context",
            "WithdrawManager",
            "WithdrawManagerData",
            "WithdrawManagerJson",
            "WithdrawQueue",
            "WithdrawRequestInfo",
            "ZERO_ADDRESS",
            "__version__",
            "build_address_list_json",
            "build_address_lookup_json",
            "build_address_names_json",
            "build_meta_morpho_json",
            "build_morpho_blue_market_json",
            "build_oracle_mapping",
            "build_role_accounts_json",
            "build_vault_info_json",
            "build_vault_validation_json",
            "cache_key",
            "compute_update_groups",
            "compute_update_reach",
            "decode_custom_error",
            "decode_external_state_event",
            "decode_substrate",
            "default_contract_cache",
            "ensure_supported_chain",
            "erc20_balance_slot",
            "euler_substrate",
            "external_state_event_name",
            "external_state_events",
            "fetch_address_registries",
            "fetch_address_registry",
            "fetch_deployment_info",
            "fetch_market",
            "fetch_unpriceable_priced_tokens",
            "fetch_vault",
            "fetch_vault_data",
            "fetch_vault_lending_health",
            "find_creation_block",
            "find_markets_missing_erc20_dependency",
            "find_orphan_fuse_markets",
            "format_amount",
            "format_market_label",
            "format_shares",
            "get_contract_name",
            "get_deployment_tx",
            "get_logs_adaptive",
            "get_revert_reason",
            "is_simulate_v1_supported",
            "market_name",
            "morpho_blue_address",
            "package_version",
            "parse_access_manager_address",
            "parse_amount",
            "parse_fuse_address",
            "parse_hex_address",
            "parse_morpho_market_id",
            "parse_plasma_vault_address",
            "parse_shares",
            "parse_tx_hash",
            "price_oracle_middleware",
            "read_changelog",
            "read_external_state",
            "register_custom_errors",
            "registry_url",
            "repository_url",
            "resolve_access_manager",
            "resolve_token_decimals",
            "resolve_token_symbol",
            "role_account_sort_key",
            "validate_vault",
            "validate_vault_state",
        }
    ),
    "ipor_fusion.config": frozenset(
        {
            "Roles",
        }
    ),
    "ipor_fusion.hypercore": frozenset(
        {
            "HYPERCORE_EVENTS",
            "HYPERCORE_EVENT_CLASSES",
            "HYPERCORE_EVENT_SPECS",
            "HYPERCORE_MARKET",
            "HYPERCORE_PENDING_READERS",
            "RESERVED_HYPERCORE_CONFIG_KEYS",
            "HyperCoreAccountMarginSummary",
            "HyperCoreActionClass",
            "HyperCoreActionEnqueuedArgs",
            "HyperCoreActionEnqueuedEvent",
            "HyperCoreActionId",
            "HyperCoreActionSettledArgs",
            "HyperCoreActionSettledEvent",
            "HyperCoreAsset",
            "HyperCoreBuilderFeeFuse",
            "HyperCoreBuilderFeeFuseEnterArgs",
            "HyperCoreBuilderFeeFuseEnterEvent",
            "HyperCoreCancelByCloidArgs",
            "HyperCoreCancelByOidArgs",
            "HyperCoreCancelFuse",
            "HyperCoreCancelFuseCancelByCloidEvent",
            "HyperCoreCancelFuseCancelByOidEvent",
            "HyperCoreClientOrderId",
            "HyperCoreConfigKey",
            "HyperCoreEffectKind",
            "HyperCoreDepositFuse",
            "HyperCoreDepositFuseEnterArgs",
            "HyperCoreDepositFuseEnterEvent",
            "HyperCoreEvent",
            "HyperCoreEventArgs",
            "HyperCoreEventSpec",
            "HyperCoreEvmExitCeiling",
            "HyperCoreMarginFuse",
            "HyperCoreMarginFuseEnterArgs",
            "HyperCoreMarginFuseEnterEvent",
            "HyperCoreNav",
            "HyperCoreOrderFuse",
            "HyperCoreOrderFuseEnterArgs",
            "HyperCoreOrderFuseEnterEvent",
            "HyperCoreOrderId",
            "HyperCorePendingReader",
            "HyperCorePendingState",
            "HyperCorePerpAssetInfo",
            "HyperCorePerpLeg",
            "HyperCorePerpMarket",
            "HyperCorePosition",
            "HyperCoreReader",
            "HyperCoreSendFuse",
            "HyperCoreSendFuseSendAssetArgs",
            "HyperCoreSendFuseSendAssetEvent",
            "HyperCoreSendFuseSpotSendArgs",
            "HyperCoreSendFuseSpotSendEvent",
            "HyperCoreSpotBalance",
            "HyperCoreSpotLeg",
            "HyperCoreSubstrates",
            "HyperCoreTokenIndex",
            "HyperCoreTokenInfo",
            "HyperCoreTransferOutcome",
            "HyperCoreTransferSnapshot",
            "HyperCoreTransferStatus",
            "HyperCoreVaultState",
            "MarginTableId",
            "PayloadHash",
            "SPOT_DEX",
            "TimeInForce",
            "TransferDirection",
            "USDC_SYSTEM_ADDRESS",
            "USDC_TOKEN_INDEX",
            "compare_hypercore_transfer",
            "convert_to_wad_int",
            "decode_hypercore_event",
            "find_hypercore_events",
            "hip3_action_asset",
            "hypercore_event_name",
            "hypercore_events",
            "is_hip3_asset",
            "is_native_perp_asset",
            "perp_dex_of",
            "precompile_index_of",
            "read_hypercore_evm_exit_ceiling",
            "read_hypercore_nav",
            "read_hypercore_transfer_outcome",
            "read_hypercore_transfer_snapshot",
            "read_hypercore_vault_state",
            "system_address",
        }
    ),
    "ipor_fusion.core": frozenset(
        {
            "AccessManager",
            "AccountOverride",
            "AccountRequest",
            "AssetPriceSource",
            "BalanceFuse",
            "BalanceFuseSnapshot",
            "BalanceProposal",
            "BatchValue",
            "BlockOverrides",
            "Call",
            "CloneArgs",
            "ERC20",
            "ExternalStateExecutor",
            "ExternalStateVaultState",
            "FeeAccount",
            "FeeManager",
            "Fuse",
            "FuseAction",
            "FusionFactory",
            "FusionInstance",
            "HighWaterMarkPerformanceFee",
            "ContextManager",
            "InFlightLimit",
            "LegacyPriceOracleMiddleware",
            "LogSource",
            "MULTICALL3_ADDRESS",
            "ManagementFeeData",
            "Multicall3",
            "Multicall3Batch",
            "PendingRequestsInfo",
            "PerformanceFeeData",
            "PlasmaVault",
            "PriceOracleMiddleware",
            "PriceOracleMiddlewareManager",
            "RawLog",
            "RecipientFee",
            "RewardsManager",
            "RoleAccount",
            "RoleAccountJson",
            "RoleAccountsJson",
            "CallPermission",
            "RoleStatus",
            "SimulatedCallResult",
            "SimulatedLog",
            "SimulationObservation",
            "SimulationResult",
            "StakeFuse",
            "StateOverrideProvider",
            "VaultSimulator",
            "VestingData",
            "Web3Context",
            "WithdrawManager",
            "WithdrawRequestInfo",
            "build_role_accounts_json",
            "erc20_balance_slot",
            "find_creation_block",
            "get_logs_adaptive",
            "is_simulate_v1_supported",
            "price_oracle_middleware",
            "read_external_state",
            "resolve_access_manager",
            "role_account_sort_key",
        }
    ),
    "ipor_fusion.crosschain": frozenset(
        {
            "ActiveCommand",
            "Any2EVMMessage",
            "AssetId",
            "BalanceObservation",
            "BusinessAction",
            "CCIP_EVENTS",
            "CCIP_EVENT_TOPICS",
            "CCIP_MESSAGE_SENT_TOPIC",
            "CCIP_VERSION",
            "CODEC_VERSION",
            "CROSSCHAIN_ERROR_SIGNATURES",
            "CcipChain",
            "CcipChainSelector",
            "CcipCommandType",
            "CcipCommitteeVerifier",
            "CcipCrosschainCommandFuse",
            "CcipCrosschainDispatcher",
            "CcipCrosschainExecutor",
            "CcipCrosschainFactory",
            "CcipCrosschainSupplyFuse",
            "CcipEvent",
            "CcipEventSpec",
            "CcipLane",
            "CcipMessageSent",
            "CcipMsgType",
            "CcipObservation",
            "CcipOffRamp",
            "CcipOnRamp",
            "CcipRouteConfig",
            "CcipRouter",
            "CcipSendParams",
            "CcipTokenAdminRegistry",
            "CcipTokenLane",
            "CcipTokenPool",
            "CcipTransport",
            "CcipVerifierResolver",
            "CcvRequirements",
            "Command",
            "CommandId",
            "CommandStatus",
            "CrosschainClaimFuse",
            "CrosschainCommandFuse",
            "CrosschainDeployment",
            "CrosschainDispatcher",
            "CrosschainExecutor",
            "CrosschainExecutorInfo",
            "CrosschainFactory",
            "CrosschainLane",
            "CrosschainSimulator",
            "CrosschainSubstrate",
            "CrosschainSubstrateLib",
            "CrosschainSubstrateType",
            "CrosschainSupplyFuse",
            "CrosschainTransport",
            "CrosschainTransportKind",
            "DeliveryCall",
            "DeploymentRequestId",
            "DeploymentStatus",
            "EMPTY_COMMAND",
            "EVMTokenAmount",
            "EnforcedOptionParam",
            "ExecutorInitParams",
            "INDEXER_URLS",
            "INSUFFICIENT_NATIVE_FEE_SELECTOR",
            "LANES",
            "LaneFuses",
            "LaneHubState",
            "LaneObservation",
            "LayerZeroEid",
            "MessageExecutionState",
            "MessageId",
            "MessageV1",
            "MsgType",
            "Observation",
            "OperationId",
            "OptionsBuilder",
            "OutboundMessage",
            "PACKET_SENT_TOPIC",
            "Packet",
            "PositionSetHash",
            "ProposalId",
            "ReturnRequestId",
            "SYNTHETIC_TOKEN_SOURCE",
            "SafetyConfig",
            "SendParams",
            "StargateAssetId",
            "StargateChain",
            "StargateCrosschainCommandFuse",
            "StargateCrosschainCommandType",
            "StargateCrosschainDispatcher",
            "StargateCrosschainExecutor",
            "StargateCrosschainFactory",
            "StargateCrosschainSupplyFuse",
            "StargateLane",
            "StargateSendParams",
            "StargateTokenMessaging",
            "StargateTransport",
            "TaxiMessage",
            "TokenTransferV1",
            "TransferId",
            "VerifierResult",
            "VerifierResultUnavailable",
            "ccip_events",
            "ccip_receive_calldata",
            "ccip_token_lane",
            "crosschain_market_id",
            "decode_ccip_envelope",
            "decode_ccip_event",
            "decode_command",
            "decode_envelope",
            "detect_transport",
            "discover_deployment",
            "discover_lane_fuses",
            "encode_enforced_options",
            "encode_oft_compose_msg",
            "fetch_verifier_result",
            "find_ccip_events",
            "log_bytes",
            "lz_compose_calldata",
            "lz_receive_calldata",
            "manual_execution",
            "open_lane",
            "open_lanes",
            "quote_ccip_native_fee",
        }
    ),
    "ipor_fusion.crosschain.ccip": frozenset(
        {
            "Any2EVMMessage",
            "CCIP_EVENTS",
            "CCIP_EVENT_TOPICS",
            "CCIP_MESSAGE_SENT_TOPIC",
            "CcipCommitteeVerifier",
            "CcipCrosschainDispatcher",
            "CcipCrosschainExecutor",
            "CcipCrosschainFactory",
            "CcipEvent",
            "CcipEventSpec",
            "CcipMessageSent",
            "CcipObservation",
            "CcipOffRamp",
            "CcipOnRamp",
            "CcipRouteConfig",
            "CcipRouter",
            "CcipTokenAdminRegistry",
            "CcipTokenLane",
            "CcipTokenPool",
            "CcipVerifierResolver",
            "CcvRequirements",
            "EVMTokenAmount",
            "INDEXER_URLS",
            "INSUFFICIENT_NATIVE_FEE_SELECTOR",
            "MessageExecutionState",
            "MessageV1",
            "SafetyConfig",
            "TokenTransferV1",
            "VerifierResult",
            "VerifierResultUnavailable",
            "ccip_events",
            "ccip_receive_calldata",
            "ccip_token_lane",
            "decode_ccip_event",
            "fetch_verifier_result",
            "find_ccip_events",
            "manual_execution",
            "quote_ccip_native_fee",
        }
    ),
    "ipor_fusion.crosschain.fuses": frozenset(
        {
            "CcipCommandType",
            "CcipCrosschainCommandFuse",
            "CcipCrosschainSupplyFuse",
            "CcipSendParams",
            "CrosschainClaimFuse",
            "CrosschainCommandFuse",
            "CrosschainSubstrate",
            "CrosschainSubstrateLib",
            "CrosschainSubstrateType",
            "CrosschainSupplyFuse",
            "SendParams",
            "StargateCrosschainCommandFuse",
            "StargateCrosschainCommandType",
            "StargateCrosschainSupplyFuse",
            "StargateSendParams",
            "crosschain_market_id",
        }
    ),
    "ipor_fusion.crosschain.stargate": frozenset(
        {
            "ActiveCommand",
            "BalanceObservation",
            "EnforcedOptionParam",
            "ExecutorInitParams",
            "Observation",
            "OptionsBuilder",
            "PACKET_SENT_TOPIC",
            "Packet",
            "StargateCrosschainDispatcher",
            "StargateCrosschainExecutor",
            "StargateCrosschainFactory",
            "StargateTokenMessaging",
            "TaxiMessage",
            "encode_enforced_options",
            "encode_oft_compose_msg",
            "lz_compose_calldata",
            "lz_receive_calldata",
        }
    ),
    "ipor_fusion.fuses": frozenset(
        {
            "AaveV3BorrowFuse",
            "AaveV3SupplyFuse",
            "AsyncActionFuse",
            "AsyncActionSubstrates",
            "CompoundV3SupplyFuse",
            "ERC4626SupplyFuse",
            "EXTERNAL_STATE_EVENTS",
            "EXTERNAL_STATE_EVENT_CLASSES",
            "EXTERNAL_STATE_EVENT_SPECS",
            "EXTERNAL_STATE_TOPICS",
            "EulerSwapDynamicParams",
            "EulerSwapInitialState",
            "EulerSwapStaticParams",
            "EulerV2BatchFuse",
            "EulerV2BatchItem",
            "EulerV2BorrowFuse",
            "EulerV2CollateralFuse",
            "EulerV2ControllerFuse",
            "EulerV2SupplyFuse",
            "EulerV2SwapDeployFuse",
            "EulerV2SwapReconfigureFuse",
            "EulerV2SwapRegistryFuse",
            "ExternalStateAction",
            "ExternalStateAssetRescuedArgs",
            "ExternalStateAssetRescuedEvent",
            "ExternalStateBalanceFuseLastCustodianTimestampUpdatedEvent",
            "ExternalStateBalanceFuseLastCustodianConfirmCountUpdatedEvent",
            "ExternalStateOperationFuseSweepSurplusEvent",
            "ExternalStateBalanceFuseLastTotalBalanceUpdatedEvent",
            "ExternalStateBigChangeDetectedArgs",
            "ExternalStateBigChangeDetectedEvent",
            "ExternalStateCustodianTimestampUpdatedArgs",
            "ExternalStateCustodianConfirmCountUpdatedArgs",
            "ExternalStateOperationFuseSweepSurplusArgs",
            "ExternalStateEvent",
            "ExternalStateEventArgs",
            "ExternalStateEventSpec",
            "ExternalStateExecutorArgs",
            "ExternalStateExecutorCreatedEvent",
            "ExternalStateExecutorDeployedEvent",
            "ExternalStateOperationArgs",
            "ExternalStateOperationFuse",
            "ExternalStateOperationFuseEnterEvent",
            "ExternalStateOperationFuseExitEvent",
            "ExternalStateSubstrates",
            "ExternalStateTotalBalanceUpdatedArgs",
            "ExternalStateUnpausedArgs",
            "ExternalStateUnpausedEvent",
            "FluidInstadappStakingFuse",
            "FluidInstadappSupplyFuse",
            "Fuse",
            "FuseAction",
            "GearboxStakeFuse",
            "GearboxSupplyFuse",
            "MerklClaimWrapperFuse",
            "MorphoBorrowFuse",
            "MorphoClaimFuse",
            "MorphoCollateralFuse",
            "MorphoFlashLoanFuse",
            "MorphoSupplyFuse",
            "RamsesClaimFuse",
            "RamsesEvents",
            "RamsesNewPositionEvent",
            "RamsesV2CollectFuse",
            "RamsesV2ModifyPositionFuse",
            "RamsesV2NewPositionFuse",
            "StakeFuse",
            "UniswapV3ClosePositionEvent",
            "UniswapV3CollectFuse",
            "UniswapV3Events",
            "UniswapV3ModifyPositionFuse",
            "UniswapV3NewPositionEvent",
            "UniswapV3NewPositionFuse",
            "UniswapV3SwapFuse",
            "UniversalTokenSwapperAbi",
            "UniversalTokenSwapperFuse",
            "UniversalTokenSwapperSubstrates",
            "decode_external_state_event",
            "euler_substrate",
            "external_state_event_name",
            "external_state_events",
        }
    ),
    "ipor_fusion.guide": frozenset(
        {
            "ARCHITECTURE",
            "GLOSSARY",
            "GuideResource",
            "INVARIANTS",
            "QUICKSTART",
            "RESOURCES",
            "guide_text",
        }
    ),
    "ipor_fusion.readers": frozenset(
        {
            "AaveV3FuseReader",
            "AaveV3PoolAddressesProvider",
            "AaveV3PositionBreakdown",
            "AaveV3Reader",
            "AaveV3ReserveTokens",
            "AaveV3UserAccountData",
            "AaveV4OracleReader",
            "AaveV4SpokeReader",
            "AaveV4UserAccountData",
            "AggregatorDetailJson",
            "AggregatorFeedDetailJson",
            "ChainlinkDetailJson",
            "CompoundV3Reader",
            "DualXrefDetailJson",
            "Erc4626DetailJson",
            "HYPEREVM_CHAIN_ID",
            "LendingMarketHealth",
            "MiddlewareFallbackDetailJson",
            "MiddlewarePricedToken",
            "UnpriceableCheck",
            "MorphoFeedDetailJson",
            "MorphoMarket",
            "MorphoMarketParams",
            "MorphoMarketRates",
            "MorphoPosition",
            "MorphoPositionBreakdown",
            "MorphoReader",
            "OracleAsset",
            "OracleAssetJson",
            "OracleMapping",
            "OracleMappingJson",
            "OracleMappingReader",
            "OracleNode",
            "OracleNodeJson",
            "OraclePrice",
            "OraclePriceJson",
            "PositionData",
            "RamsesV2Position",
            "RamsesV2Reader",
            "SourceDetailJson",
            "UniswapV3Position",
            "UniswapV3Reader",
            "UnreadableLendingMarket",
            "VaultLendingHealth",
            "build_oracle_mapping",
            "fetch_unpriceable_priced_tokens",
            "fetch_vault_lending_health",
            "morpho_blue_address",
        }
    ),
}


# Public modules (README) and the top-level names each keeps importable by its
# own path: the names it defines, plus the re-exports README, AGENTS.md or
# MIGRATION_4.md promise at that path (a 3.x home a name moved out of, such as
# ``vault_data.FeeData``). Moving a name out of one of these modules must keep a
# re-export there; a module-path import outside this table is internal.
_MODULE_PATH_SNAPSHOT: dict[str, frozenset[str]] = {
    "ipor_fusion.about": frozenset(
        {
            "ChangelogEntry",
            "PREVIEW_FEATURES",
            "PREVIEW_STATEMENT",
            "PreviewFeature",
            "package_version",
            "read_changelog",
            "repository_url",
        }
    ),
    "ipor_fusion.cache": frozenset(
        {
            "CacheField",
            "CacheKey",
            "ContractCache",
            "FileContractCache",
            "NullCache",
            "cache_key",
            "default_contract_cache",
        }
    ),
    "ipor_fusion.chains": frozenset(
        {
            "CHAIN_NAMES",
            "CHAIN_NAME_TO_ID",
            "GET_LOGS_RANGE_HINTS",
            "SUPPORTED_CHAIN_IDS",
            "ensure_supported_chain",
        }
    ),
    "ipor_fusion.dependency_graph": frozenset(
        {
            "compute_update_groups",
            "compute_update_reach",
            "find_markets_missing_erc20_dependency",
            "find_orphan_fuse_markets",
        }
    ),
    "ipor_fusion.errors": frozenset(
        {
            "AddressNotInRegistryError",
            "AddressRegistryError",
            "ExternalStateEventDecodeError",
            "HyperCoreEventDecodeError",
            "OwnerNotFoundError",
            "VerifierResultUnavailable",
            "BalanceSlotNotFoundError",
            "CUSTOM_ERRORS",
            "ContractNotFoundError",
            "CrosschainDiscoveryError",
            "EmptyCallResultError",
            "ExternalStateError",
            "HyperCoreValuationError",
            "IporFusionError",
            "LogScanError",
            "LogSourceUnavailable",
            "MorphoApiError",
            "MorphoMarketNotFoundError",
            "NotPlasmaVaultError",
            "SimulationError",
            "SimulationRunError",
            "TransactionError",
            "UnsupportedChainError",
            "UnsupportedContractVersionError",
            "UnsupportedVaultVersionError",
            "decode_custom_error",
            "get_revert_reason",
            "register_custom_errors",
        }
    ),
    "ipor_fusion.explorer": frozenset(
        {
            "get_contract_name",
            "get_deployment_tx",
        }
    ),
    "ipor_fusion.field_docs": frozenset(
        {
            "DOCS",
        }
    ),
    "ipor_fusion.market_ids": frozenset(
        {
            "IporFusionMarkets",
        }
    ),
    "ipor_fusion.market_info": frozenset(
        {
            "MetaMorphoAdapterJson",
            "MetaMorphoAllocationJson",
            "MetaMorphoCapJson",
            "MetaMorphoFlowCapJson",
            "MetaMorphoPublicAllocatorJson",
            "MetaMorphoV1Json",
            "MetaMorphoV2Json",
            "MorphoAssetJson",
            "MorphoBlueMarketJson",
            "MorphoFlowCapConfigJson",
            "MorphoMarketParamsJson",
            "MorphoMarketRatesJson",
            "MorphoMarketStateJson",
            "MorphoSupplyingVaultJson",
            "MorphoVaultAssetJson",
            "build_meta_morpho_json",
            "build_morpho_blue_market_json",
        }
    ),
    "ipor_fusion.morpho_api": frozenset(
        {
            "MorphoApiError",
            "MorphoApiMarket",
            "PUBLIC_ALLOCATOR_ADDRESSES",
            "VaultAllocation",
            "VaultFlowCap",
            "VaultV1Info",
            "VaultV1MarketAllocation",
            "VaultV2Adapter",
            "VaultV2Cap",
            "VaultV2Info",
            "fetch_market",
            "fetch_vault",
        }
    ),
    "ipor_fusion.registry": frozenset(
        {
            "AddressListJson",
            "AddressLookupJson",
            "AddressMatchJson",
            "AddressNameJson",
            "AddressNamesJson",
            "AddressRegistry",
            "REGISTRY_CHAIN_IDS",
            "REGISTRY_REPOSITORY",
            "build_address_list_json",
            "build_address_lookup_json",
            "build_address_names_json",
            "fetch_address_registries",
            "fetch_address_registry",
            "registry_url",
        }
    ),
    "ipor_fusion.substrates": frozenset(
        {
            "SubstrateInfo",
            "decode_substrate",
            "format_market_label",
            "market_name",
        }
    ),
    "ipor_fusion.types": frozenset(
        {
            "CcipChainSelector",
            "AccessManagerAddress",
            "Amount",
            "AssetId",
            "AssetSource",
            "ChainId",
            "Check",
            "CommandId",
            "Decimals",
            "DeploymentRequestId",
            "Fee",
            "FuseAddress",
            "HyperCoreActionId",
            "HyperCoreAsset",
            "HyperCoreClientOrderId",
            "HyperCoreOrderId",
            "HyperCoreTokenIndex",
            "LayerZeroEid",
            "Liquidity",
            "MAX_UINT256",
            "MappingStatus",
            "MarginTableId",
            "MarketId",
            "MessageId",
            "MorphoBlueMarketId",
            "NodeStatus",
            "OperationId",
            "PayloadHash",
            "CustodianMarker",
            "Period",
            "PlasmaVaultAddress",
            "PositionSetHash",
            "Price",
            "ProposalHash",
            "ProposalId",
            "ReturnRequestId",
            "RoleId",
            "Severity",
            "Shares",
            "StargateAssetId",
            "Tick",
            "TokenId",
            "TransferId",
            "TxHash",
            "Verdict",
            "Wad",
            "ZERO_ADDRESS",
            "format_amount",
            "format_shares",
            "parse_access_manager_address",
            "parse_amount",
            "parse_fuse_address",
            "parse_hex_address",
            "parse_morpho_market_id",
            "parse_plasma_vault_address",
            "parse_shares",
            "parse_tx_hash",
        }
    ),
    "ipor_fusion.validation": frozenset(
        {
            "Erc20Holding",
            "Erc20Holdings",
            "Finding",
            "Reconciliation",
            "UnpriceableToken",
            "VaultState",
            "VaultValidation",
            "WithdrawQueue",
            "validate_vault",
            "validate_vault_state",
        }
    ),
    "ipor_fusion.vault_data": frozenset(
        {
            "FeeData",
            "VaultData",
            "WithdrawManagerData",
            "fetch_deployment_info",
            "fetch_vault_data",
            "resolve_token_decimals",
            "resolve_token_symbol",
        }
    ),
    "ipor_fusion.vault_info": frozenset(
        {
            "Erc20HoldingsJson",
            "FindingJson",
            "VaultValidationJson",
            "build_vault_validation_json",
            "AaveBreakdownJson",
            "AmountJson",
            "BalanceFuseJson",
            "BreakdownAmountJson",
            "DeltaJson",
            "DependencyGraphJson",
            "DeploymentErrorJson",
            "DeploymentJson",
            "Erc20BalanceJson",
            "FeesJson",
            "FuseJson",
            "HealthCheckJson",
            "HighWaterMarkJson",
            "HyperCoreJson",
            "HyperCorePendingJson",
            "HyperCorePerpLegJson",
            "HyperCorePerpMarketJson",
            "HyperCoreSpotLegJson",
            "LendingHealthJson",
            "LendingMarketJson",
            "ManagersJson",
            "MorphoBreakdownJson",
            "PendingRequestJson",
            "PricedAmountJson",
            "RecipientFeeJson",
            "ReconciliationJson",
            "SharePriceJson",
            "SubstrateEntryJson",
            "UsdAmountJson",
            "UnreadableLendingMarketJson",
            "VaultAssetJson",
            "VaultInfoJson",
            "VaultLinksJson",
            "WithdrawManagerJson",
            "build_vault_info_json",
        }
    ),
}

PUBLIC_MODULES = tuple(sorted(_MODULE_PATH_SNAPSHOT))


def _module(name: str) -> ModuleType:
    return importlib.import_module(name)


@pytest.mark.parametrize("package", sorted(_ALL_SNAPSHOT))
def test_all_matches_snapshot(package: str) -> None:
    declared = _module(package).__all__
    assert len(declared) == len(set(declared)), "duplicate names in __all__"
    expected = _ALL_SNAPSHOT[package]
    assert set(declared) == expected, (
        f"{package}.__all__ changed: "
        f"added {sorted(set(declared) - expected)}, "
        f"removed {sorted(expected - set(declared))}; "
        "update the snapshot in tests/test_public_api.py deliberately"
    )


@pytest.mark.parametrize("package", sorted(_ALL_SNAPSHOT))
def test_every_all_name_resolves(package: str) -> None:
    module = _module(package)
    missing = [name for name in module.__all__ if not hasattr(module, name)]
    assert not missing


@pytest.mark.parametrize("package", sorted(_ALL_SNAPSHOT))
def test_no_exported_name_hides_a_submodule(package: str) -> None:
    module = _module(package)
    if not hasattr(module, "__path__"):
        return
    submodules = {info.name for info in pkgutil.iter_modules(module.__path__)}
    assert not sorted(submodules & set(module.__all__))


@pytest.mark.parametrize(
    "package", sorted(set(_ALL_SNAPSHOT) - _NOT_REEXPORTED - {"ipor_fusion"})
)
def test_subpackage_names_are_exported_from_the_top_level(package: str) -> None:
    """A stable subpackage's names are a subset of the top level."""
    module = _module(package)
    missing = sorted(set(module.__all__) - set(ipor_fusion.__all__))
    assert not missing
    for name in module.__all__:
        assert getattr(ipor_fusion, name) is getattr(module, name), name


def _exported() -> dict[str, object]:
    """Every name of the top level and the preview subpackages, by object."""
    names = {name: getattr(ipor_fusion, name) for name in ipor_fusion.__all__}
    for package in sorted(_PREVIEW):
        module = _module(package)
        names.update((name, getattr(module, name)) for name in module.__all__)
    return names


def _defined_in(module: ModuleType) -> set[str]:
    """Exported names whose object is defined in ``module``: a class, function
    or ``NewType`` by its ``__module__``, a constant or alias by a module-level
    assignment."""
    source = inspect.getsource(module)
    defined = set()
    for name, obj in _exported().items():
        if vars(module).get(name) is not obj:
            continue
        if (
            inspect.isclass(obj)
            or inspect.isfunction(obj)
            or hasattr(obj, "__supertype__")
        ):
            if obj.__module__ == module.__name__:
                defined.add(name)
        elif re.search(rf"^{re.escape(name)}\s*(?::[^=\n]*)?=", source, re.MULTILINE):
            defined.add(name)
    return defined


@pytest.mark.parametrize("module_name", PUBLIC_MODULES)
def test_public_module_paths_resolve(module_name: str) -> None:
    module = _module(module_name)
    exported = _exported()
    for name in _MODULE_PATH_SNAPSHOT[module_name]:
        assert name in exported, name
        assert getattr(module, name, None) is exported[name], name


@pytest.mark.parametrize("module_name", PUBLIC_MODULES)
def test_module_path_snapshot_lists_every_defined_name(module_name: str) -> None:
    missing = _defined_in(_module(module_name)) - _MODULE_PATH_SNAPSHOT[module_name]
    assert not missing, (
        f"{module_name} defines exported names {sorted(missing)} missing from "
        "_MODULE_PATH_SNAPSHOT; add them deliberately"
    )


@pytest.mark.parametrize(
    "package",
    [
        "ipor_fusion.config",
        "ipor_fusion.core",
        "ipor_fusion.crosschain",
        "ipor_fusion.fuses",
        "ipor_fusion.readers",
    ],
)
def test_subpackage_all_lists_every_exported_name_it_defines(package: str) -> None:
    """A top-level name defined anywhere under a public subpackage is also in
    that subpackage's ``__all__``, so the subpackage import offers it too."""
    root = _module(package)
    defined: set[str] = set()
    for info in pkgutil.walk_packages(root.__path__, f"{package}."):
        defined |= _defined_in(_module(info.name))
    # HyperCore modules live under fuses/ and readers/ but export through
    # the preview subpackage.
    preview = set(_module("ipor_fusion.hypercore").__all__)
    missing = sorted(defined - set(root.__all__) - preview)
    assert not missing, f"{package}.__all__ lacks {missing}"


def test_preview_names_are_not_in_the_top_level() -> None:
    """Preview features export only through their subpackages, so the stable
    surface promises nothing about them; their exception classes stay top-level
    names of ``ipor_fusion.errors``."""
    leaked = sorted(
        name
        for package in _PREVIEW
        for name in _module(package).__all__
        if name in ipor_fusion.__all__
        and getattr(ipor_fusion, name) is getattr(_module(package), name)
        and not (
            inspect.isclass(getattr(ipor_fusion, name))
            and issubclass(getattr(ipor_fusion, name), BaseException)
        )
    )
    assert not leaked


# Defined in a preview module yet stable: the chain id the vault snapshot
# gates the HyperCore read on.
_STABLE_IN_PREVIEW_MODULES = frozenset({"HYPEREVM_CHAIN_ID"})
_STABLE_SUBPACKAGES = (
    "ipor_fusion.config",
    "ipor_fusion.core",
    "ipor_fusion.fuses",
    "ipor_fusion.readers",
)


def test_no_stable_package_exports_a_name_a_preview_module_defines() -> None:
    """A class, function or constant defined in a preview module is never
    exported from ``ipor_fusion`` or a stable subpackage (the snapshots pin
    what the preview subpackages export)."""
    root = Path(ipor_fusion.__file__).parent
    modules = [
        *root.joinpath("crosschain").rglob("*.py"),
        *root.joinpath("fuses").glob("hypercore*.py"),
        *root.joinpath("readers").glob("hypercore*.py"),
    ]
    defined: set[str] = set()
    for path in modules:
        for node in ast.parse(path.read_text()).body:
            if isinstance(node, (ast.ClassDef, ast.FunctionDef)):
                defined.add(node.name)
            elif isinstance(node, ast.Assign):
                defined |= {t.id for t in node.targets if isinstance(t, ast.Name)}
            elif isinstance(node, ast.AnnAssign) and isinstance(node.target, ast.Name):
                defined.add(node.target.id)
    stable = {
        name
        for package in ("ipor_fusion", *_STABLE_SUBPACKAGES)
        for name in _module(package).__all__
    }
    assert sorted(defined & stable - _STABLE_IN_PREVIEW_MODULES) == []

from ipor_fusion.types import MarketId


class IporFusionMarkets:
    """Predefined markets used in the IPOR Fusion protocol.

    When new markets are added by authorized property of PlasmaVault
    during runtime, they should be added and described here as well.
    """

    AAVE_V3 = MarketId(1)
    COMPOUND_V3_USDC = MarketId(2)
    GEARBOX_POOL_V3 = MarketId(3)
    # dependence graph: balance of GEARBOX_POOL_V3
    GEARBOX_FARM_DTOKEN_V3 = MarketId(4)
    FLUID_INSTADAPP_POOL = MarketId(5)
    FLUID_INSTADAPP_STAKING = MarketId(6)
    ERC20_VAULT_BALANCE = MarketId(7)
    # dependence graph: balance of ERC20_VAULT_BALANCE
    UNISWAP_SWAP_V3_POSITIONS = MarketId(8)
    # dependence graph: balance of ERC20_VAULT_BALANCE
    UNISWAP_SWAP_V2 = MarketId(9)
    # dependence graph: balance of ERC20_VAULT_BALANCE
    UNISWAP_SWAP_V3 = MarketId(10)
    EULER_V2 = MarketId(11)
    # dependence graph: balance of ERC20_VAULT_BALANCE
    UNIVERSAL_TOKEN_SWAPPER = MarketId(12)
    # dependence graph: balance of ERC20_VAULT_BALANCE
    # substrate type: UniversalTokenSwapperSubstrateType (Token, Target or Slippage)
    UNIVERSAL_TOKEN_SWAPPER_V2 = MarketId(12_02)
    COMPOUND_V3_USDT = MarketId(13)
    MORPHO = MarketId(14)
    SPARK = MarketId(15)
    CURVE_POOL = MarketId(16)
    CURVE_LP_GAUGE = MarketId(17)
    RAMSES_V2_POSITIONS = MarketId(18)
    # dependence graph: balance of ERC20_VAULT_BALANCE
    MORPHO_FLASH_LOAN = MarketId(19)
    AAVE_V3_LIDO = MarketId(20)
    MOONWELL = MarketId(21)
    MORPHO_REWARDS = MarketId(22)
    PENDLE = MarketId(23)
    FLUID_REWARDS = MarketId(24)
    CURVE_GAUGE_ERC4626 = MarketId(25)
    COMPOUND_V3_WETH = MarketId(26)
    HARVEST_HARD_WORK = MarketId(27)
    TAC_STAKING = MarketId(28)
    LIQUITY_V2 = MarketId(29)
    AERODROME = MarketId(30)
    VELODROME_SUPERCHAIN = MarketId(31)
    # substrate type: VelodromeSuperchainSlipstreamSubstrate
    VELODROME_SUPERCHAIN_SLIPSTREAM = MarketId(32)
    # substrate type: AerodromeSlipstreamSubstrate
    AREODROME_SLIPSTREAM = MarketId(33)
    # substrate type: address (Stake DAO Reward Vault contract)
    STAKE_DAO_V2 = MarketId(34)
    # substrate type: address (Silo Config contract)
    SILO_V2 = MarketId(35)
    # substrate type: BalancerSubstrate (pool or gauge addresses)
    BALANCER = MarketId(36)
    # substrate type: address (Yield Basis LT token addresses)
    YIELD_BASIS_LT = MarketId(37)
    # substrate type: EnsoSubstrate (target address + function selector)
    ENSO = MarketId(38)
    # substrate type: EbisuZapperSubstrate
    EBISU = MarketId(39)
    # substrate type: AsyncActionFuseSubstrate
    ASYNC_ACTION = MarketId(40)
    MORPHO_LIQUIDITY_IN_MARKETS = MarketId(41)
    # substrate type: OdosSubstrateType (Token or Slippage)
    ODOS_SWAPPER = MarketId(42)
    # substrate type: VeloraSubstrateType (Token or Slippage)
    VELORA_SWAPPER = MarketId(43)
    # SparkLend via Aave V3 fuses; substrates: underlying asset addresses
    SPARK_LEND = MarketId(44)
    # substrate type: MidasSubstrateType
    MIDAS = MarketId(45)
    NAPIER = MarketId(46)
    # substrate type: DolomiteSubstrate (asset, subAccountId, canBorrow)
    DOLOMITE = MarketId(47)
    LITE_PSM = MarketId(48)
    # substrate type: AaveV4Substrate Reserve (spoke, reserveId, isCollateral, canBorrow)
    AAVE_V4 = MarketId(49)
    # External-state market (id 50): off-vault capital via the external-state
    # operation fuse (e.g. Hyperliquid / Boros margin legs). On-chain substrate
    # types: ExternalStateSubstrateLib (1 ASSET, 2 TARGET, 3 CUSTODIAN,
    # 4 BALANCE_ACCOUNT, 5 STALENESS_MAX, 6 BIG_CHANGE_BPS, 7 DUST_THRESHOLD,
    # 8 MIN_UPDATE_INTERVAL).
    EXTERNAL_STATE = MarketId(50)
    # substrate type: AguaSubstrateLib (VAULT or ASSET)
    AGUA_GLOBAL_CARRY = MarketId(51)
    # dependence graph: balance of ERC20_VAULT_BALANCE (purchase tokens return
    # to the vault on every position exit)
    TERM_FINANCE = MarketId(52)
    # substrate type: UniswapV4SubstrateLib — PoolId (raw bytes32,
    # keccak256(abi.encode(PoolKey))) plus both pool currencies as
    # substrate-as-asset; native-currency pools are not supported
    UNISWAP_V4 = MarketId(53)
    # Crosschain market: a hub PlasmaVault deploys capital into PlasmaVaults
    # on other chains through an executor (Stargate/LayerZero or Chainlink
    # CCIP). Substrate type: CrosschainSubstrateLib (1 EXECUTOR, 2 REMOTE_VAULT
    # bound to a chain id).
    # A legacy HyperEVM HyperCore test vault also uses 54 until it is
    # redeployed on HyperCore's assigned market 55; do not interpret that
    # vault's market-54 substrates as crosschain configuration.
    CROSSCHAIN = MarketId(54)
    # HyperCore market (id 55): a HyperEVM PlasmaVault trading on Hyperliquid
    # Core through delegatecalled fuses (CoreWriter actions, precompile reads).
    # Substrate type: HyperCoreSubstrateLib (1 SpotToken, 2 PerpMarket,
    # 3 Destination, 4 Builder, 5 Config, 6 SendCap). Ahead of the public
    # contracts repo; the mirror test allowlists it until the constant lands.
    # The first HyperEVM test deployment ran on id 54 before the contracts
    # team assigned 54 to crosschain and moved HyperCore to 55; that vault's
    # market 54 is pre-renumbering HyperCore wiring, not a crosschain market.
    HYPERCORE = MarketId(55)
    # POL unstaking via sPOLController. The out-of-band id is baked into the
    # deployed fuses as an immutable (424243 = 0x67933), so it must match the
    # deployments. Substrates per IporFusionMarkets.sol: the sPOLController
    # (this market) and sPOL under ERC20_VAULT_BALANCE, with a dependency edge
    # to it (enter burns wallet sPOL and creates the pending claim atomically).
    SPOL_UNSTAKE = MarketId(424_243)
    ERC4626_0001 = MarketId(100_001)
    ERC4626_0002 = MarketId(100_002)
    ERC4626_0003 = MarketId(100_003)
    ERC4626_0004 = MarketId(100_004)
    ERC4626_0005 = MarketId(100_005)
    ERC4626_0006 = MarketId(100_006)
    ERC4626_0007 = MarketId(100_007)
    ERC4626_0008 = MarketId(100_008)
    ERC4626_0009 = MarketId(100_009)
    ERC4626_0010 = MarketId(100_010)
    ERC4626_0011 = MarketId(100_011)
    ERC4626_0012 = MarketId(100_012)
    ERC4626_0013 = MarketId(100_013)
    ERC4626_0014 = MarketId(100_014)
    ERC4626_0015 = MarketId(100_015)
    ERC4626_0016 = MarketId(100_016)
    ERC4626_0017 = MarketId(100_017)
    ERC4626_0018 = MarketId(100_018)
    ERC4626_0019 = MarketId(100_019)
    ERC4626_0020 = MarketId(100_020)
    META_MORPHO_0001 = MarketId(200_001)
    META_MORPHO_0002 = MarketId(200_002)
    META_MORPHO_0003 = MarketId(200_003)
    META_MORPHO_0004 = MarketId(200_004)
    META_MORPHO_0005 = MarketId(200_005)
    META_MORPHO_0006 = MarketId(200_006)
    META_MORPHO_0007 = MarketId(200_007)
    META_MORPHO_0008 = MarketId(200_008)
    META_MORPHO_0009 = MarketId(200_009)
    META_MORPHO_0010 = MarketId(200_010)
    # substrates: reward token addresses MerklClaimWrapperFuse may forward
    # (type(uint256).max - 3 in Solidity; max == 2**256 - 1, hence the shift)
    MERKL = MarketId(2**256 - 4)
    EXCHANGE_RATE_VALIDATOR = MarketId(2**256 - 3)
    ASSETS_BALANCE_VALIDATION = MarketId(2**256 - 2)
    ZERO_BALANCE_MARKET = MarketId(2**256 - 1)

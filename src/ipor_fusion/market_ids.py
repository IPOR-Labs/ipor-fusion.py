class IporFusionMarkets:
    """Predefined markets used in the IPOR Fusion protocol.

    When new markets are added by authorized property of PlasmaVault
    during runtime, they should be added and described here as well.
    """

    AAVE_V3 = 1
    COMPOUND_V3_USDC = 2
    GEARBOX_POOL_V3 = 3
    # dependence graph: balance of GEARBOX_POOL_V3
    GEARBOX_FARM_DTOKEN_V3 = 4
    FLUID_INSTADAPP_POOL = 5
    FLUID_INSTADAPP_STAKING = 6
    ERC20_VAULT_BALANCE = 7
    # dependence graph: balance of ERC20_VAULT_BALANCE
    UNISWAP_SWAP_V3_POSITIONS = 8
    # dependence graph: balance of ERC20_VAULT_BALANCE
    UNISWAP_SWAP_V2 = 9
    # dependence graph: balance of ERC20_VAULT_BALANCE
    UNISWAP_SWAP_V3 = 10
    EULER_V2 = 11
    # dependence graph: balance of ERC20_VAULT_BALANCE
    UNIVERSAL_TOKEN_SWAPPER = 12
    COMPOUND_V3_USDT = 13
    MORPHO = 14
    SPARK = 15
    CURVE_POOL = 16
    CURVE_LP_GAUGE = 17
    RAMSES_V2_POSITIONS = 18
    # dependence graph: balance of ERC20_VAULT_BALANCE
    MORPHO_FLASH_LOAN = 19
    AAVE_V3_LIDO = 20
    MOONWELL = 21
    MORPHO_REWARDS = 22
    PENDLE = 23
    FLUID_REWARDS = 24
    CURVE_GAUGE_ERC4626 = 25
    COMPOUND_V3_WETH = 26
    HARVEST_HARD_WORK = 27
    TAC_STAKING = 28
    LIQUITY_V2 = 29
    AERODROME = 30
    VELODROME_SUPERCHAIN = 31
    # substrate type: VelodromeSuperchainSlipstreamSubstrate
    VELODROME_SUPERCHAIN_SLIPSTREAM = 32
    # substrate type: AerodromeSlipstreamSubstrate
    AREODROME_SLIPSTREAM = 33
    # substrate type: address (Stake DAO Reward Vault contract)
    STAKE_DAO_V2 = 34
    # substrate type: address (Silo Config contract)
    SILO_V2 = 35
    # substrate type: BalancerSubstrate (pool or gauge addresses)
    BALANCER = 36
    # substrate type: address (Yield Basis LT token addresses)
    YIELD_BASIS_LT = 37
    # substrate type: EnsoSubstrate (target address + function selector)
    ENSO = 38
    # substrate type: EbisuZapperSubstrate
    EBISU = 39
    # substrate type: AsyncActionFuseSubstrate
    ASYNC_ACTION = 40
    MORPHO_LIQUIDITY_IN_MARKETS = 41
    # substrate type: OdosSubstrateType (Token or Slippage)
    ODOS_SWAPPER = 42
    # substrate type: VeloraSubstrateType (Token or Slippage)
    VELORA_SWAPPER = 43
    # substrate type: AaveV4SubstrateType (Asset or Spoke)
    AAVE_V4 = 44
    # substrate type: MidasSubstrateType
    MIDAS = 45
    # substrate type: DolomiteSubstrate (asset, subAccountId, canBorrow)
    DOLOMITE = 46
    ERC4626_0001 = 100_001
    ERC4626_0002 = 100_002
    ERC4626_0003 = 100_003
    ERC4626_0004 = 100_004
    ERC4626_0005 = 100_005
    ERC4626_0006 = 100_006
    ERC4626_0007 = 100_007
    ERC4626_0008 = 100_008
    ERC4626_0009 = 100_009
    ERC4626_0010 = 100_010
    ERC4626_0011 = 100_011
    ERC4626_0012 = 100_012
    ERC4626_0013 = 100_013
    ERC4626_0014 = 100_014
    ERC4626_0015 = 100_015
    ERC4626_0016 = 100_016
    ERC4626_0017 = 100_017
    ERC4626_0018 = 100_018
    ERC4626_0019 = 100_019
    ERC4626_0020 = 100_020
    META_MORPHO_0001 = 200_001
    META_MORPHO_0002 = 200_002
    META_MORPHO_0003 = 200_003
    META_MORPHO_0004 = 200_004
    META_MORPHO_0005 = 200_005
    META_MORPHO_0006 = 200_006
    META_MORPHO_0007 = 200_007
    META_MORPHO_0008 = 200_008
    META_MORPHO_0009 = 200_009
    META_MORPHO_0010 = 200_010
    EXCHANGE_RATE_VALIDATOR = 2**256 - 3
    ASSETS_BALANCE_VALIDATION = 2**256 - 2
    ZERO_BALANCE_MARKET = 2**256 - 1

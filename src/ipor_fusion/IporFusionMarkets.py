class IporFusionMarkets:
    """Predefined markets used in the IPOR Fusion protocol."""

    # AAVE V3 market
    AAVE_V3 = 1

    # Compound V3 market USDC
    COMPOUND_V3_USDC = 2

    # Gearbox V3 market
    GEARBOX_POOL_V3 = 3

    # If this marketId is added to the PlasmaVault, a dependence graph with balance of GEARBOX_POOL_V3 is needed
    GEARBOX_FARM_DTOKEN_V3 = 4

    # Fluid Instadapp market
    FLUID_INSTADAPP_POOL = 5
    FLUID_INSTADAPP_STAKING = 6

    ERC20_VAULT_BALANCE = 7

    # If this marketId is added to the PlasmaVault, a dependence graph with balance of ERC20_VAULT_BALANCE is needed
    UNISWAP_SWAP_V3_POSITIONS = 8

    # Uniswap markets
    UNISWAP_SWAP_V2 = 9
    UNISWAP_SWAP_V3 = 10

    # Euler market
    EULER_V2 = 11

    # Universal token swapper
    UNIVERSAL_TOKEN_SWAPPER = 12

    # Compound V3 market USDT
    COMPOUND_V3_USDT = 13

    # Morpho market
    MORPHO = 14

    # Spark market
    SPARK = 15

    # Curve markets
    CURVE_POOL = 16
    CURVE_LP_GAUGE = 17

    RAMSES_V2_POSITIONS = 18

    # Morpho flash loan market
    MORPHO_FLASH_LOAN = 19

    # ERC4626 Vault markets
    ERC4626_0001 = 100_001
    ERC4626_0002 = 100_002
    ERC4626_0003 = 100_003
    ERC4626_0004 = 100_004
    ERC4626_0005 = 100_005

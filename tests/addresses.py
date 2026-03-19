from web3 import Web3

# Ethereum Mainnet — standard ERC-20 tokens
ETHEREUM_WBTC = Web3.to_checksum_address(
    "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"
)  # 8 decimals
ETHEREUM_WETH = Web3.to_checksum_address(
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
)  # 18 decimals
ETHEREUM_USDC = Web3.to_checksum_address(
    "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
)  # 6 decimals
ETHEREUM_USDT = Web3.to_checksum_address(
    "0xdAC17F958D2ee523a2206206994597C13D831ec7"
)  # 6 decimals

# Base — tokens and Aave V3 receipt tokens
BASE_WSTETH = Web3.to_checksum_address(
    "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452"
)  # wrapped staked ETH
BASE_WETH = Web3.to_checksum_address(
    "0x4200000000000000000000000000000000000006"
)  # Base predeploy address
BASE_USDC = Web3.to_checksum_address(
    "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913"
)  # native USDC (not bridged)
BASE_AAVE_V3_VARIABLE_DEBT_WETH = (
    Web3.to_checksum_address(  # Aave variable debt token for WETH borrows
        "0x24e6e0795b3c7c71D965fCc4f371803d1c1DcA1E"
    )
)
BASE_AAVE_V3_A_WSTETH = (
    Web3.to_checksum_address(  # Aave aToken received when supplying wstETH
        "0x99CBC45ea5bb7eF3a5BC08FB1B7E56bB2442Ef0D"
    )
)

# Arbitrum — tokens and protocol receipt tokens
ARBITRUM_USDC = Web3.to_checksum_address(
    "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
)  # native USDC, 6 decimals
ARBITRUM_WETH = Web3.to_checksum_address(
    "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1"
)  # 18 decimals
ARBITRUM_ARB = Web3.to_checksum_address(
    "0x912CE59144191C1204E64559FE8253a0e49E6548"
)  # ARB governance token
ARBITRUM_USDT = Web3.to_checksum_address(
    "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9"
)  # 6 decimals
ARBITRUM_AAVE_V3_A_USDC = (
    Web3.to_checksum_address(  # Aave aToken received when supplying USDC
        "0x724dc807b04555b71ed48a6896b6f41593b8c637"
    )
)
ARBITRUM_COMPOUND_V3_C_USDC = (
    Web3.to_checksum_address(  # Compound V3 cToken for USDC market
        "0x9c4ec768c28520b50860ea7a15bd7213a9ff58bf"
    )
)
ARBITRUM_RAM_TOKEN = Web3.to_checksum_address(  # Ramses DEX governance token
    "0xAAA6C1E32C55A7Bfa8066A6FAE9b42650F262418"
)
ARBITRUM_XRAM_TOKEN = (
    Web3.to_checksum_address(  # Ramses staked/escrowed governance token
        "0xAAA1eE8DC1864AE49185C368e8c64Dd780a50Fb7"
    )
)

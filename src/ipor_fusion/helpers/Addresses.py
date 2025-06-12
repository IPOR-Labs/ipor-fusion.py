from web3 import Web3

# Token contract addresses on Base network
BASE_WSTETH = Web3.to_checksum_address(
    "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452"
)  # Lido Wrapped Staked ETH - liquid staking derivative token
BASE_WETH = Web3.to_checksum_address(
    "0x4200000000000000000000000000000000000006"
)  # Wrapped ETH token on Base network
BASE_AAVE_V3_VARIABLE_DEBT_WETH = Web3.to_checksum_address(
    "0x24e6e0795b3c7c71D965fCc4f371803d1c1DcA1E"
)  # Aave V3 variable debt token representing borrowed WETH
base_aBaswstETH = Web3.to_checksum_address(
    "0x99CBC45ea5bb7eF3a5BC08FB1B7E56bB2442Ef0D"
)  # Aave V3 interest-bearing token representing supplied WStETH collateral

ETHEREUM_WBTC_ADDRESS = Web3.to_checksum_address(
    "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599"
)  # WBTC token address
ETHEREUM_WETH_ADDRESS = Web3.to_checksum_address(
    "0xC02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2"
)  # WETH token address

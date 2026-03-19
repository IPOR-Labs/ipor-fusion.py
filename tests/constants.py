from eth_account import Account
from web3 import Web3

DEFAULT_TRANSACTION_MAX_PRIORITY_FEE = 2_000_000_000  # 2 gwei
ONE_HUNDRED = 100

# Anvil/Hardhat account #0 — deterministic well-known test wallet
ANVIL_WALLET = Web3.to_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")

ALPHA_WALLET = ANVIL_WALLET  # alias used in alpha/admin role tests

# Well-known private key for Anvil account #0 — NEVER use on mainnet
ANVIL_WALLET_PRIVATE_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
)
ANVIL_ACCOUNT = Account.from_key(ANVIL_WALLET_PRIVATE_KEY)

ALPHA_PRIVATE_KEY = ANVIL_ACCOUNT  # alias used in alpha/admin role tests

ONE_HUNDRED_USDC = 100 * 10**6  # 100 USDC in 6-decimal units
GAS_PRICE_MARGIN = 25  # percentage margin added to gas price estimates
GAS_MARGIN = 20  # percentage margin added to gas limit estimates

# Time constants in seconds
DAY = 24 * 60 * 60
WEEK = 7 * DAY
MONTH = 30 * DAY
YEAR = 365 * DAY


# Arbitrum vault addresses — each represents a different deployment generation
ARBITRUM_PILOT_V3_PLASMA_VAULT = Web3.to_checksum_address(  # earliest pilot vault
    "0x862644e627eb0cdeff10f234bea51b8dfd6ea8e8"
)
ARBITRUM_PILOT_V4_PLASMA_VAULT = Web3.to_checksum_address(  # added UniswapV3 LP support
    "0x707A88CDF02e2b8c98Aff08Be245B835E2784C8b"
)
ARBITRUM_PILOT_V5_PLASMA_VAULT = (
    Web3.to_checksum_address(  # latest vault with all fuses
        "0x3F97CEa640B8B93472143f87a96d5A86f1F5167F"
    )
)
ARBITRUM_PILOT_SCHEDULED_PLASMA_VAULT = (
    Web3.to_checksum_address(  # time-locked execution vault
        "0xAC62eDcdA14aF2e2547F85D56EB2CE36D11333DA"
    )
)


# Arbitrum fuse addresses (chain 42161) — deployed against V5 vault unless noted
ARBITRUM_AAVE_V3_SUPPLY_FUSE = Web3.to_checksum_address(
    "0xd3c752ee5bb80de64f76861b800a8f3b464c50f9"
)
ARBITRUM_COMPOUND_V3_SUPPLY_FUSE = Web3.to_checksum_address(
    "0xb0b3dc1b27c6c8007c9b01a768d6717f6813fe94"
)
ARBITRUM_GEARBOX_V3_FARM_FUSE = Web3.to_checksum_address(
    "0xb0fbf6b7d0586c0a5bc1c3b8a98773f4ed02c983"
)
ARBITRUM_ERC4626_SUPPLY_FUSE_MARKET_ID_3 = (
    Web3.to_checksum_address(  # market_id=3 variant
        "0x07cd27531ee9df28292b26eeba3f457609deae07"
    )
)
ARBITRUM_FLUID_INSTADAPP_STAKING_FUSE = Web3.to_checksum_address(
    "0x2b83f05e463cbc34861b10cb020b6eb4740bd890"
)
ARBITRUM_ERC4626_SUPPLY_FUSE_MARKET_ID_5 = (
    Web3.to_checksum_address(  # market_id=5 variant
        "0x4ae8640b3a6b71fa1a05372a59946e66beb05f9f"
    )
)
ARBITRUM_UNISWAP_V3_SWAP_FUSE = Web3.to_checksum_address(
    "0x84c5ab008c66d664681698a9e4536d942b916f89"
)
ARBITRUM_UNISWAP_V3_NEW_POSITION_FUSE = Web3.to_checksum_address(
    "0x1da7f95e63f12169b3495e2b83d01d0d6592dd86"
)
ARBITRUM_UNISWAP_V3_MODIFY_POSITION_FUSE = Web3.to_checksum_address(
    "0xba503b6f2b95a4a47ee9884bbbcd80cace2d2eb3"
)
ARBITRUM_UNISWAP_V3_COLLECT_FUSE = Web3.to_checksum_address(
    "0x75781ab6cdce9c505dbd0848f4ad8a97c68f53c1"
)
ARBITRUM_RAMSES_V2_NEW_POSITION_FUSE = Web3.to_checksum_address(
    "0xb025cc5e73e2966e12e4d859360b51c1d0f45ea3"
)
ARBITRUM_RAMSES_V2_MODIFY_POSITION_FUSE = Web3.to_checksum_address(
    "0xd41501b46a68dea06a460fd79a7bcda9e3b92674"
)
ARBITRUM_RAMSES_V2_COLLECT_FUSE = Web3.to_checksum_address(
    "0x859f5c9d5cb2800a9ff72c56d79323ea01cb30b9"
)
ARBITRUM_RAMSES_CLAIM_FUSE = Web3.to_checksum_address(
    "0x6f292d12a2966c9b796642cafd67549bbbe3d066"
)
ARBITRUM_UNIVERSAL_SWAP_FUSE = Web3.to_checksum_address(
    "0xb052b0d983e493b4d40dec75a03d21b70b83c2ca"
)

# V3 vault-specific fuses (deployed for 0x862644… V3 vault only)
ARBITRUM_V3_COMPOUND_V3_SUPPLY_FUSE = Web3.to_checksum_address(
    "0x34bcbc3f10ce46894bb39de0c667257efb35c079"
)
ARBITRUM_V3_GEARBOX_V3_FARM_FUSE = Web3.to_checksum_address(
    "0x50fbc3e2eb2ec49204a41ea47946016703ba358d"
)
ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_3 = Web3.to_checksum_address(
    "0xeb58e3adb9e537c06ebe2dee6565b248ec758a93"
)
ARBITRUM_V3_FLUID_INSTADAPP_STAKING_FUSE = Web3.to_checksum_address(
    "0x962a7f0a2cbe97d4004175036a81e643463b76ec"
)
ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_5 = Web3.to_checksum_address(
    "0x0eA739e6218F67dF51d1748Ee153ae7B9DCD9a25"
)

# V4 vault-specific fuses (deployed for 0x707A88… V4 vault only)
ARBITRUM_V4_UNISWAP_V3_NEW_POSITION_FUSE = Web3.to_checksum_address(
    "0x0ce06c57173b7e4079b2afb132cb9ce846ddac9b"
)

# Base fuse addresses (chain 8453)
BASE_AAVE_V3_SUPPLY_FUSE = Web3.to_checksum_address(
    "0x44dcb8a4c40fa9941d99f409b2948fe91b6c15d5"
)
BASE_AAVE_V3_BORROW_FUSE = Web3.to_checksum_address(
    "0x1df60f2a046f3dce8102427e091c1ea99ae1d774"
)
BASE_MORPHO_SUPPLY_FUSE = Web3.to_checksum_address(
    "0xDE3FD3A25534471e92C5940d418B0582802b17B6"
)
BASE_MORPHO_FLASH_LOAN_FUSE = Web3.to_checksum_address(
    "0x20f305ce4fc12f9171fcd7c2fbcd7d11f6119265"
)
BASE_MORPHO_COLLATERAL_FUSE = (
    Web3.to_checksum_address(  # same address as supply fuse (dual-purpose deployment)
        "0xde3fd3a25534471e92c5940d418b0582802b17b6"
    )
)
BASE_MORPHO_BORROW_FUSE = Web3.to_checksum_address(
    "0x35f44ad1d9f2773da05f4664bf574c760ba47bf6"
)
BASE_UNIVERSAL_SWAP_FUSE = Web3.to_checksum_address(
    "0xdbc5f9962ce85749f1b3c51ba0473909229e3807"
)

# Ethereum fuse addresses (chain 1)
ETHEREUM_AAVE_V3_SUPPLY_FUSE = Web3.to_checksum_address(
    "0x465d639eb964158bee11f35e8fc23f704ec936a2"
)
ETHEREUM_AAVE_V3_BORROW_FUSE = Web3.to_checksum_address(
    "0x820d879ef89356b93a7c71addbf45c40a0dde453"
)
ETHEREUM_MORPHO_SUPPLY_FUSE = Web3.to_checksum_address(
    "0xd08cb606cee700628e55b0b0159ad65421e6c8df"
)
ETHEREUM_MORPHO_FLASH_LOAN_FUSE = Web3.to_checksum_address(
    "0x9185033e24db36407b9b1a1886cb47b9533433de"
)
ETHEREUM_MORPHO_CLAIM_FUSE = Web3.to_checksum_address(
    "0x6820df665ba09fbbd3240aa303421928ef4c71a1"
)

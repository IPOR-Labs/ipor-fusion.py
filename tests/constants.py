from eth_account import Account
from web3 import Web3

DEFAULT_TRANSACTION_MAX_PRIORITY_FEE = 2_000_000_000
ONE_HUNDRED = 100

ANVIL_WALLET = Web3.to_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")

ALPHA_WALLET = ANVIL_WALLET

# pylint: disable=no-value-for-parameter
ANVIL_WALLET_PRIVATE_KEY = Account.from_key(
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
)

ALPHA_PRIVATE_KEY = ANVIL_WALLET_PRIVATE_KEY

ONE_HUNDRED_USDC = 100 * 10**6
GAS_PRICE_MARGIN = 25
GAS_MARGIN = 20

DAY = 24 * 60 * 60
WEEK = 7 * DAY
MONTH = 30 * DAY
YEAR = 365 * DAY


class ARBITRUM:

    USDC = Web3.to_checksum_address("0xaf88d065e77c8cc2239327c5edb3a432268e5831")
    USDT = Web3.to_checksum_address("0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9")
    DAI = Web3.to_checksum_address("0xda10009cbd5d07dd0cecc66161fc93d7c9000da1")
    WETH = Web3.to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")

    class PILOT:
        class V3:
            PLASMA_VAULT = Web3.to_checksum_address(
                "0x862644e627eb0cdeff10f234bea51b8dfd6ea8e8"
            )
            ACCESS_MANAGER = Web3.to_checksum_address(
                "0x5e7DF5b9bbF0EAB19acDaFB0A5df70f310263cA8"
            )
            FLUID_INSTADAPP_POOL_FUSE = Web3.to_checksum_address(
                "0x0eA739e6218F67dF51d1748Ee153ae7B9DCD9a25"
            )
            FLUID_INSTADAPP_CLAIM_FUSE = Web3.to_checksum_address(
                "0x12F86cE5a2B95328c882e6A106dE775b04a131bA"
            )
            FLUID_INSTADAPP_STAKING_FUSE = Web3.to_checksum_address(
                "0x962A7F0A2CbE97d4004175036A81E643463b76ec"
            )
            GEARBOX_FARM_FUSE = Web3.to_checksum_address(
                "0x50fbc3e2eb2ec49204a41ea47946016703ba358d"
            )
            GEARBOX_CLAIM_FUSE = Web3.to_checksum_address(
                "0x2496Aaeb9F74CcecCE0902F3459F3dde795d7A65"
            )
            GEARBOX_POOL_FUSE = Web3.to_checksum_address(
                "0xeb58e3adb9e537c06ebe2dee6565b248ec758a93"
            )
            AAVE_V3_FUSE = Web3.to_checksum_address(
                "0xd3c752EE5Bb80dE64F76861B800a8f3b464C50f9"
            )
            COMPOUND_V3_FUSE = Web3.to_checksum_address(
                "0x34BCBC3f10CE46894Bb39De0C667257EFb35c079"
            )

        class V4:
            PLASMA_VAULT = Web3.to_checksum_address(
                "0x707A88CDF02e2b8c98Aff08Be245B835E2784C8b"
            )
            ACCESS_MANAGER = Web3.to_checksum_address(
                "0x7D0d3A697a1ECA7c5e0C44751A628821d125cffb"
            )
            UNISWAP_V3_SWAP_FUSE = Web3.to_checksum_address(
                "0x84C5aB008C66d664681698A9E4536D942B916F89"
            )
            UNISWAP_V3_NEW_POSITION_SWAP_FUSE = Web3.to_checksum_address(
                "0x0ce06c57173b7E4079B2AFB132cB9Ce846dDAC9b"
            )
            UNISWAP_V3_MODIFY_POSITION_SWAP_FUSE = Web3.to_checksum_address(
                "0xba503b6f2b95A4A47ee9884bbBcd80cAce2D2EB3"
            )
            UNISWAP_V3_COLLECT_SWAP_FUSE = Web3.to_checksum_address(
                "0x75781AB6CdcE9c505DbD0848f4Ad8A97c68F53c1"
            )
            UNIVERSAL_TOKEN_SWAPPER_FUSE = Web3.to_checksum_address(
                "0xB052b0D983E493B4D40DeC75A03D21b70b83c2ca"
            )

        class V5:
            PLASMA_VAULT = Web3.to_checksum_address(
                "0x3F97CEa640B8B93472143f87a96d5A86f1F5167F"
            )
            ACCESS_MANAGER = Web3.to_checksum_address(
                "0x18C11d79bF4A14Edd9458455C7E10fc560cCfe1e"
            )
            REWARDS_CLAIM_MANAGER = Web3.to_checksum_address(
                "0xfe6AE161e0C8bE9FAe194476c0c78Af43d1B50B4"
            )
            UNISWAP_V3_SWAP_FUSE = Web3.to_checksum_address(
                "0x84C5aB008C66d664681698A9E4536D942B916F89"
            )
            RAMSES_V2_NEW_POSITION_FUSE = Web3.to_checksum_address(
                "0xb025CC5e73e2966e12e4d859360B51c1D0F45EA3"
            )
            RAMSES_V2_MODIFY_POSITION_FUSE = Web3.to_checksum_address(
                "0xD41501B46a68DeA06a460fD79a7bCda9e3b92674"
            )
            RAMSES_V2_COLLECT_FUSE = Web3.to_checksum_address(
                "0x859F5c9D5CB2800A9Ff72C56d79323EA01cB30b9"
            )
            RAMSES_V2_CLAIM_FUSE = Web3.to_checksum_address(
                "0x6F292d12a2966c9B796642cAFD67549bbbE3D066"
            )

    class UNISWAP:
        class V3:
            UNIVERSAL_ROUTER = Web3.to_checksum_address(
                "0x5E325eDA8064b456f4781070C0738d849c824258"
            )

    class RAMSES:
        class V2:
            RAM = Web3.to_checksum_address("0xAAA6C1E32C55A7Bfa8066A6FAE9b42650F262418")
            X_REM = Web3.to_checksum_address(
                "0xAAA1eE8DC1864AE49185C368e8c64Dd780a50Fb7"
            )

    class FLUID_INSTADAPP:
        class V1:
            class USDC:
                POOL = Web3.to_checksum_address(
                    "0x1a996cb54bb95462040408c06122d45d6cdb6096"
                )
                STAKING_POOL = Web3.to_checksum_address(
                    "0x48f89d731C5e3b5BeE8235162FC2C639Ba62DB7d"
                )

    class GEARBOX:
        class V3:
            class USDC:
                POOL = Web3.to_checksum_address(
                    "0x890A69EF363C9c7BdD5E36eb95Ceb569F63ACbF6"
                )
                FARM_POOL = Web3.to_checksum_address(
                    "0xD0181a36B0566a8645B7eECFf2148adE7Ecf2BE9"
                )

    class AAVE:
        class V3:
            class USDC:
                A_TOKEN_ARB_USDC_N = Web3.to_checksum_address(
                    "0x724dc807b04555b71ed48a6896b6f41593b8c637"
                )

    class COMPOUND:
        class V3:
            class USDC:
                C_TOKEN = Web3.to_checksum_address(
                    "0x9c4ec768c28520b50860ea7a15bd7213a9ff58bf"
                )

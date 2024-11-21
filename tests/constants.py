from eth_account import Account
from web3 import Web3

DEFAULT_TRANSACTION_MAX_PRIORITY_FEE = 2_000_000_000
ONE_HUNDRED = 100

ANVIL_WALLET = Web3.to_checksum_address("0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266")

ALPHA_WALLET = ANVIL_WALLET

# pylint: disable=no-value-for-parameter
ANVIL_WALLET_PRIVATE_KEY = (
    "0xac0974bec39a17e36ba4a6b4d238ff944bacb478cbed5efcae784d7bf4f2ff80"
)
ANVIL_ACCOUNT = Account.from_key(ANVIL_WALLET_PRIVATE_KEY)

ALPHA_PRIVATE_KEY = ANVIL_ACCOUNT

ONE_HUNDRED_USDC = 100 * 10**6
GAS_PRICE_MARGIN = 25
GAS_MARGIN = 20

DAY = 24 * 60 * 60
WEEK = 7 * DAY
MONTH = 30 * DAY
YEAR = 365 * DAY


class ARBITRUM:
    class PILOT:
        class V3:
            PLASMA_VAULT = Web3.to_checksum_address(
                "0x862644e627eb0cdeff10f234bea51b8dfd6ea8e8"
            )

        class V4:
            PLASMA_VAULT = Web3.to_checksum_address(
                "0x707A88CDF02e2b8c98Aff08Be245B835E2784C8b"
            )

        class V5:
            PLASMA_VAULT = Web3.to_checksum_address(
                "0x3F97CEa640B8B93472143f87a96d5A86f1F5167F"
            )

        class SCHEDULED:
            PLASMA_VAULT = Web3.to_checksum_address(
                "0xAC62eDcdA14aF2e2547F85D56EB2CE36D11333DA"
            )

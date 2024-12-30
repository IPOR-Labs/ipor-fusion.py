from typing import Optional

from eth_typing import ChecksumAddress
from web3 import Web3

from ipor_fusion.error.UnsupportedChainId import UnsupportedChainId

asset_mapping = {
    "42161": [
        {
            "address": "0x724dc807b04555b71ed48a6896b6f41593b8c637",
            "symbol": "aArbUSDCn",
        },
        {"address": "0xaf88d065e77c8cc2239327c5edb3a432268e5831", "symbol": "USDC"},
        {"address": "0x9c4ec768c28520b50860ea7a15bd7213a9ff58bf", "symbol": "cUSDCv3"},
        {"address": "0x1a996cb54bb95462040408c06122d45d6cdb6096", "symbol": "fUSDC"},
        {
            "address": "0x48f89d731C5e3b5BeE8235162FC2C639Ba62DB7d",
            "symbol": "FluidLendingStakingRewardsUsdc",
        },
        {"address": "0xAAA6C1E32C55A7Bfa8066A6FAE9b42650F262418", "symbol": "RAM"},
        {"address": "0xAAA1eE8DC1864AE49185C368e8c64Dd780a50Fb7", "symbol": "xRAM"},
        {"address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", "symbol": "USDT"},
        {"address": "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1", "symbol": "DAI"},
        {"address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "symbol": "WETH"},
        {"address": "0x890A69EF363C9c7BdD5E36eb95Ceb569F63ACbF6", "symbol": "dUSDCV3"},
        {
            "address": "0xD0181a36B0566a8645B7eECFf2148adE7Ecf2BE9",
            "symbol": "farmdUSDCV3",
        },
        {"address": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", "symbol": "WBTC"},
    ],
    "1": [{"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "symbol": "USDC"}],
    "8453": [
        {"address": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", "symbol": "cbBTC"},
        {"address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "symbol": "USDC"},
        {"address": "0x4200000000000000000000000000000000000006", "symbol": "WETH"},
        {"address": "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22", "symbol": "cbETH"},
        {"address": "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452", "symbol": "wstETH"},
    ],
}


class AssetMapper:
    @staticmethod
    def map(chain_id: int, asset_symbol: str) -> Optional[ChecksumAddress]:
        if not asset_mapping[str(chain_id)]:
            raise UnsupportedChainId(chain_id)

        for asset in asset_mapping[str(chain_id)]:
            if asset.get("symbol") == asset_symbol:
                return Web3.to_checksum_address(asset.get("address"))

        return None

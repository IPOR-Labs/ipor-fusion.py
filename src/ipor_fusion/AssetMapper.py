from web3 import Web3

from ipor_fusion.error.UnsupportedAssetSymbol import UnsupportedAssetSymbol
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
            "symbol": "FluidLendingStakingRewards",
        },
        {"address": "0xAAA6C1E32C55A7Bfa8066A6FAE9b42650F262418", "symbol": "RAM"},
        {"address": "0xAAA1eE8DC1864AE49185C368e8c64Dd780a50Fb7", "symbol": "xRAM"},
        {"address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", "symbol": "USDT"},
        {"address": "0xda10009cbd5d07dd0cecc66161fc93d7c9000da1", "symbol": "DAI"},
        {"address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "symbol": "WETH"},
    ]
}


class AssetMapper:
    @staticmethod
    def map(chain_id: int, asset_symbol: str):
        if not asset_mapping[str(chain_id)]:
            raise UnsupportedChainId(chain_id)

        for asset in asset_mapping[str(chain_id)]:
            if asset.get("symbol") == asset_symbol:
                return Web3.to_checksum_address(asset.get("address"))

        raise UnsupportedAssetSymbol()

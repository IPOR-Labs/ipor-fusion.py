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
        {"address": "0xaf88d065e77c8cC2239327C5EDb3A432268e5831", "symbol": "USDC"},
        {"address": "0x9c4ec768c28520b50860ea7a15bd7213a9ff58bf", "symbol": "cUSDCv3"},
        {"address": "0x1a996cb54bb95462040408c06122d45d6cdb6096", "symbol": "fUSDC"},
        {
            "address": "0x48f89d731C5e3b5BeE8235162FC2C639Ba62DB7d",
            "symbol": "FluidLendingStakingRewardsUsdc",
        },
        {"address": "0xAAA6C1E32C55A7Bfa8066A6FAE9b42650F262418", "symbol": "RAM"},
        {"address": "0xAAA1eE8DC1864AE49185C368e8c64Dd780a50Fb7", "symbol": "xRAM"},
        {"address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9", "symbol": "USDT"},
        {"address": "0xDA10009cBd5D07dd0CeCc66161FC93D7c9000da1", "symbol": "DAI"},
        {"address": "0x82aF49447D8a07e3bd95BD0d56f35241523fBab1", "symbol": "WETH"},
        {"address": "0x890A69EF363C9c7BdD5E36eb95Ceb569F63ACbF6", "symbol": "dUSDCV3"},
        {
            "address": "0xD0181a36B0566a8645B7eECFf2148adE7Ecf2BE9",
            "symbol": "farmdUSDCV3",
        },
        {"address": "0x2f2a2543B76A4166549F7aaB2e75Bef0aefC5B0f", "symbol": "WBTC"},
        {"address": "0xED65C5085a18Fa160Af0313E60dcc7905E944Dc7", "symbol": "ETHx"},
        {"address": "0x4186BFC76E2E237523CBC30FD220FE055156b41F", "symbol": "rsETH"},
        {"address": "0x5979D7b546E38E414F7E9822514be443A4800529", "symbol": "wstETH"},
        {"address": "0x35751007a407ca6FEFfE80b3cB397736D2cf4dbe", "symbol": "weETH"},
        {"address": "0xFF970A61A04b1cA14834A43f5dE4533eBDDB5CC8", "symbol": "USDC.e"},
        {
            "address": "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9",
            "symbol": "USD\u20ae0",
        },
    ],
    "1": [
        {"address": "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48", "symbol": "USDC"},
        {"address": "0xdAC17F958D2ee523a2206206994597C13D831ec7", "symbol": "USDT"},
        {"address": "0x83F20F44975D03b1b09e64809B757c47f942BEeA", "symbol": "sDAI"},
        {
            "address": "0xE00bd3Df25fb187d6ABBB620b3dfd19839947b81",
            "symbol": "PT-sUSDE-27MAR2025",
        },
        {
            "address": "0xEe9085fC268F6727d5D4293dBABccF901ffDCC29",
            "symbol": "PT-sUSDE-26DEC2024",
        },
        {"address": "0x2260FAC5E5542a773Aa44fBCfeDf7C193bc2C599", "symbol": "WBTC"},
        {"address": "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0", "symbol": "wstETH"},
        {
            "address": "0x5BaE9a5D67d1CA5b09B14c91935f635CFBF3b685",
            "symbol": "PT-USD0++-27MAR2025",
        },
        {
            "address": "0x386AE941D4262B0Ee96354499dF2ab8442734EC0",
            "symbol": "PT-sUSDE-27FEB2025",
        },
    ],
    "8453": [
        {"address": "0xcbB7C0000aB88B473b1f5aFd9ef808440eed33Bf", "symbol": "cbBTC"},
        {"address": "0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913", "symbol": "USDC"},
        {"address": "0x4200000000000000000000000000000000000006", "symbol": "WETH"},
        {"address": "0x2Ae3F1Ec7F1F5012CFEab0185bfc7aa3cf0DEc22", "symbol": "cbETH"},
        {"address": "0xc1CBa3fCea344f92D9239c08C0568f6F2F0ee452", "symbol": "wstETH"},
        {"address": "0x60a3E35Cc302bFA44Cb288Bc5a4F316Fdb1adb42", "symbol": "EURC"},
        {"address": "0x4ed4E862860beD51a9570b96d89aF5E1B0Efefed", "symbol": "DEGEN"},
        {"address": "0x940181a94A35A4569E4529A3CDfB74e38FD98631", "symbol": "AERO"},
        {"address": "0x52b492a33E447Cdb854c7FC19F1e57E8BfA1777D", "symbol": "PEPE"},
        {"address": "0xf42f5795D9ac7e9D757dB633D693cD548Cfd9169", "symbol": "fUSDC"},
        {
            "address": "0x48f89d731C5e3b5BeE8235162FC2C639Ba62DB7d",
            "symbol": "FluidLendingStakingRewardsUsdc",
        },
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

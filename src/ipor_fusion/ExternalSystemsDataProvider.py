from dataclasses import dataclass

from web3 import Web3

from ipor_fusion.TransactionExecutor import TransactionExecutor


@dataclass
class ExternalSystemsData:
    usdc_address: str
    usdt_address: str
    weth_address: str
    dai_address: str


class ExternalSystemsDataProvider:
    _USDC = {
        42161: Web3.to_checksum_address("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    }
    _USDT = {
        42161: Web3.to_checksum_address("0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9")
    }
    _DAI = {
        42161: Web3.to_checksum_address("0xda10009cbd5d07dd0cecc66161fc93d7c9000da1")
    }
    _WETH = {
        42161: Web3.to_checksum_address("0x82aF49447D8a07e3bd95BD0d56f35241523fBab1")
    }

    def __init__(self, transaction_executor: TransactionExecutor, chain_id: int):
        self._transaction_executor = transaction_executor
        self._chain_id = chain_id

    def get(self) -> ExternalSystemsData:
        return ExternalSystemsData(
            usdc_address=self._USDC.get(self._chain_id),
            usdt_address=self._USDT.get(self._chain_id),
            weth_address=self._WETH.get(self._chain_id),
            dai_address=self._DAI.get(self._chain_id),
        )

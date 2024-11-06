from dataclasses import dataclass

from ipor_fusion.TransactionExecutor import TransactionExecutor


@dataclass
class ExternalSystemsData:
    usdc_address: str
    usdt_address: str


class ExternalSystemsDataProvider:
    _USDC = {42161: "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"}
    _USDT = {42161: "0xFd086bC7CD5C481DCC9C85ebE478A1C0b69FCbb9"}

    def __init__(self, transaction_executor: TransactionExecutor, chain_id: int):
        self._transaction_executor = transaction_executor
        self._chain_id = chain_id

    def get(self) -> ExternalSystemsData:
        return ExternalSystemsData(
            usdc_address=self._USDC.get(self._chain_id),
            usdt_address=self._USDT.get(self._chain_id),
        )

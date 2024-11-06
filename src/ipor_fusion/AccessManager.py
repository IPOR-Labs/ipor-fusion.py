from ipor_fusion.TransactionExecutor import TransactionExecutor


class AccessManager:

    def __init__(
        self, transaction_executor: TransactionExecutor, access_manager_address: str
    ):
        self._transaction_executor = transaction_executor
        self._access_manager_address = access_manager_address

    def address(self) -> str:
        return self._access_manager_address

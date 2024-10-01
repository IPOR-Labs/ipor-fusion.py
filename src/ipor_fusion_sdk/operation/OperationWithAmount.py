from typing import Any

from ipor_fusion_sdk.operation.BaseOperation import BaseOperation, MarketId


class OperationWithAmount(BaseOperation):

    def __init__(self, market_id: MarketId, amount: int):
        super().__init__(market_id)
        if amount is None:
            raise ValueError("amount is required")
        self._amount = amount

    def amount(self) -> int:
        return self._amount

    def __eq__(self, other: Any) -> bool:
        if self is other:
            return True
        if other is None or not isinstance(other, OperationWithAmount):
            return False
        return self._market_id == other._market_id and self._amount == other._amount

    def __hash__(self) -> int:
        return hash((self._market_id, self._amount))

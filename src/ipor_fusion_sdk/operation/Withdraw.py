from typing import Any

from ipor_fusion_sdk import MarketId
from ipor_fusion_sdk.operation.Operation import Operation


class Withdraw(Operation):
    def __init__(self, market_id: MarketId, amount: int):
        if market_id is None:
            raise ValueError("marketId is required")
        if amount is None:
            raise ValueError("amount is required")
        self._market_id = market_id
        self._amount = amount

    def market_id(self) -> MarketId:
        return self._market_id

    def amount(self) -> int:
        return self._amount

    def __eq__(self, other: Any) -> bool:
        if self is other:
            return True
        if other is None or not isinstance(other, Withdraw):
            return False
        return self._market_id == other._market_id and self._amount == other._amount

    def __hash__(self) -> int:
        return hash((self._market_id, self._amount))

    def __str__(self) -> str:
        return f"Withdraw[marketId={self._market_id}, amount={self._amount}]"

    def __repr__(self) -> str:
        return self.__str__()

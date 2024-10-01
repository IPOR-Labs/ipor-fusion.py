from abc import ABC

from ipor_fusion_sdk import MarketId


class BaseOperation(ABC):

    def __init__(self, market_id: MarketId):
        if market_id is None:
            raise ValueError("marketId is required")
        self._market_id = market_id

    def market_id(self) -> MarketId:
        return self._market_id
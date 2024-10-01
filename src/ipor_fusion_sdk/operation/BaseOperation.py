from abc import ABC


class MarketId:
    def __init__(self, protocol_id: str, market_id: str):
        if protocol_id is None:
            raise ValueError("protocolId is required")
        if market_id is None:
            raise ValueError("marketId is required")

        self._protocol_id = protocol_id
        self._market_id = market_id

    @property
    def protocol_id(self) -> str:
        return self._protocol_id

    @property
    def market_id(self) -> str:
        return self._market_id

    def __eq__(self, other):
        if not isinstance(other, MarketId):
            return False
        return (
            self._protocol_id == other._protocol_id
            and self._market_id == other._market_id
        )

    def __hash__(self):
        return hash((self._protocol_id, self._market_id))

    def __str__(self):
        return (
            f"MarketId[protocolId='{self._protocol_id}', marketId='{self._market_id}']"
        )

    def __repr__(self):
        return self.__str__()


class BaseOperation(ABC):

    def __init__(self, market_id: MarketId):
        if market_id is None:
            raise ValueError("marketId is required")
        self._market_id = market_id

    def market_id(self) -> MarketId:
        return self._market_id

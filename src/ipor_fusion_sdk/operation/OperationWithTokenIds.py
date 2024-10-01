from typing import List

from ipor_fusion_sdk.operation.BaseOperation import BaseOperation, MarketId


class OperationWithTokenIds(BaseOperation):
    def __init__(self, market_id: MarketId, token_ids: List[int]):
        super().__init__(market_id)
        if token_ids is None:
            raise ValueError("token_ids is required")
        self._token_ids = token_ids

    def token_ids(self) -> List[int]:
        return self._token_ids

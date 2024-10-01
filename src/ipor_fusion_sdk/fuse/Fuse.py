from abc import ABC
from typing import List

from ipor_fusion_sdk.fuse import FuseActionDynamicStruct
from ipor_fusion_sdk.operation.BaseOperation import MarketId


class Fuse(ABC):
    def supports(self, market_id: MarketId) -> bool:
        raise NotImplementedError("Method not implemented")

    def create_fuse_enter_action(
        self, market_id: MarketId, amount: int
    ) -> List[FuseActionDynamicStruct]:
        raise NotImplementedError("Method not implemented")

    def create_fuse_exit_action(
        self, market_id: MarketId, amount: int
    ) -> List[FuseActionDynamicStruct]:
        raise NotImplementedError("Method not implemented")

    def create_fuse_claim_action(
        self, market_id: MarketId
    ) -> List[FuseActionDynamicStruct]:
        raise NotImplementedError("Method not implemented")

    @staticmethod
    def _require_non_null(value, message):
        if value is None:
            raise ValueError(message)
        return value

from ipor_fusion_sdk.operation.BaseOperation import BaseOperation, MarketId


class DecreasePosition(BaseOperation):
    def __init__(
        self,
        market_id: MarketId,
        token_id: int,
        liquidity: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ):
        super().__init__(market_id)
        if token_id is None:
            raise ValueError("token_id is required")
        if liquidity is None:
            raise ValueError("liquidity is required")
        if amount0_min is None:
            raise ValueError("amount0_min is required")
        if amount1_min is None:
            raise ValueError("amount1_min is required")
        if deadline is None:
            raise ValueError("deadline is required")

        self._token_id = token_id
        self._liquidity = liquidity
        self._amount0_min = amount0_min
        self._amount1_min = amount1_min
        self._deadline = deadline

    def token_id(self) -> int:
        return self._token_id

    def liquidity(self) -> int:
        return self._liquidity

    def amount0_min(self) -> int:
        return self._amount0_min

    def amount1_min(self) -> int:
        return self._amount1_min

    def deadline(self) -> int:
        return self._deadline

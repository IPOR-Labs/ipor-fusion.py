from ipor_fusion_sdk.operation.BaseOperation import BaseOperation, MarketId


class IncreasePosition(BaseOperation):
    def __init__(
        self,
        market_id: MarketId,
        token0: str,
        token1: str,
        token_id: int,
        amount0_desired: int,
        amount1_desired: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ):
        super().__init__(market_id)
        if token0 is None:
            raise ValueError("token0 is required")
        if token1 is None:
            raise ValueError("token1 is required")
        if token_id is None:
            raise ValueError("token_id is required")
        if amount0_desired is None:
            raise ValueError("amount0_desired is required")
        if amount1_desired is None:
            raise ValueError("amount1_desired is required")
        if amount0_min is None:
            raise ValueError("amount0_min is required")
        if amount1_min is None:
            raise ValueError("amount1_min is required")
        if deadline is None:
            raise ValueError("deadline is required")

        self._token0 = token0
        self._token1 = token1
        self._token_id = token_id
        self._amount0_desired = amount0_desired
        self._amount1_desired = amount1_desired
        self._amount0_min = amount0_min
        self._amount1_min = amount1_min
        self._deadline = deadline

    def token0(self) -> str:
        return self._token0

    def token1(self) -> str:
        return self._token1

    def token_id(self) -> int:
        return self._token_id

    def amount0_desired(self) -> int:
        return self._amount0_desired

    def amount1_desired(self) -> int:
        return self._amount1_desired

    def amount0_min(self) -> int:
        return self._amount0_min

    def amount1_min(self) -> int:
        return self._amount1_min

    def deadline(self) -> int:
        return self._deadline

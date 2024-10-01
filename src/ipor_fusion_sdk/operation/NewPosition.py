from ipor_fusion_sdk.operation.BaseOperation import BaseOperation, MarketId


class NewPosition(BaseOperation):
    def __init__(
        self,
        market_id: MarketId,
        token0: str,
        token1: str,
        fee: int,
        tick_lower: int,
        tick_upper: int,
        amount0_desired: int,
        amount1_desired: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ):
        required_fields = {
            "market_id": market_id,
            "token0": token0,
            "token1": token1,
            "fee": fee,
            "tick_lower": tick_lower,
            "tick_upper": tick_upper,
            "amount0_desired": amount0_desired,
            "amount1_desired": amount1_desired,
            "amount0_min": amount0_min,
            "amount1_min": amount1_min,
            "deadline": deadline,
        }

        for field_name, field_value in required_fields.items():
            if field_value is None:
                raise ValueError(f"{field_name} is required")

        super().__init__(market_id)
        self._token0 = token0
        self._token1 = token1
        self._fee = fee
        self._tick_lower = tick_lower
        self._tick_upper = tick_upper
        self._amount0_desired = amount0_desired
        self._amount1_desired = amount1_desired
        self._amount0_min = amount0_min
        self._amount1_min = amount1_min
        self._deadline = deadline

    def token0(self) -> str:
        return self._token0

    def token1(self) -> str:
        return self._token1

    def fee(self) -> int:
        return self._fee

    def tick_lower(self) -> int:
        return self._tick_lower

    def tick_upper(self) -> int:
        return self._tick_upper

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

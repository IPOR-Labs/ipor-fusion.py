from ipor_fusion_sdk.operation.BaseOperation import BaseOperation, MarketId


class Swap(BaseOperation):

    def __init__(
        self,
        market_id: MarketId,
        token_in_address: str,
        token_out_address: str,
        fee: int,
        token_in_amount: int,
        min_out_amount: int,
    ):
        super().__init__(market_id)
        if token_in_address is None:
            raise ValueError("token_in_address is required")
        if token_out_address is None:
            raise ValueError("token_out_address is required")
        if fee is None:
            raise ValueError("fee is required")
        if token_in_amount is None:
            raise ValueError("token_in_amount is required")
        if min_out_amount is None:
            raise ValueError("min_out_amount is required")

        self._market_id = market_id
        self._token_in_address = token_in_address
        self._token_out_address = token_out_address
        self._fee = fee
        self._token_in_amount = token_in_amount
        self._min_out_amount = min_out_amount

    def token_in_address(self) -> str:
        return self._token_in_address

    def token_out_address(self) -> str:
        return self._token_out_address

    def fee(self) -> int:
        return self._fee

    def token_in_amount(self) -> int:
        return self._token_in_amount

    def min_out_amount(self) -> int:
        return self._min_out_amount

    def __str__(self) -> str:
        return f"Swap[marketId={self._market_id}, tokenInAddress={self._token_in_address}, tokenOutAddress={self._token_out_address}, fee={self._fee}, tokenInAmount={self._token_in_amount}, minOutAmount={self._min_out_amount}]"

    def __repr__(self) -> str:
        return self.__str__()

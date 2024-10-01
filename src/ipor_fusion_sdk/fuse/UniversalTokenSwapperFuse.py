from typing import List

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion_sdk.fuse.Fuse import Fuse
from ipor_fusion_sdk.fuse.FuseActionDynamicStruct import FuseActionDynamicStruct
from ipor_fusion_sdk.operation.BaseOperation import MarketId


class UniversalTokenSwapperData:
    def __init__(
        self,
        targets: List[str],
        data: List[bytes],
    ):
        self._targets = targets
        self._data = data

    def encode(self) -> bytes:
        return encode(
            ["(address[],bytes[])"],
            [[self._targets, self._data]],
        )

    def targets(self) -> List[str]:
        return self._targets

    def data(self) -> List[bytes]:
        return self._data


class UniversalTokenSwapperEnterData:
    def __init__(
        self,
        token_in: str,
        token_out: str,
        amount_in: int,
        data: UniversalTokenSwapperData,
    ):
        self._token_in = token_in
        self._token_out = token_out
        self._amount_in = amount_in
        self._data = data

    def encode(self) -> bytes:
        return encode(
            ["(address,address,uint256,(address[],bytes[]))"],
            [
                [
                    self._token_in,
                    self._token_out,
                    self._amount_in,
                    [self._data.targets(), self._data.data()],
                ]
            ],
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector(
            "enter((address,address,uint256,(address[],bytes[])))"
        )

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class UniversalTokenSwapperFuse(Fuse):
    PROTOCOL_ID = "universal-token-swapper"

    def __init__(self, universal_token_swapper_fuse_address: str):
        self._universal_token_swapper_fuse_address = self._require_non_null(
            universal_token_swapper_fuse_address,
            "universal_token_swapper_fuse_address is required",
        )

    def create_fuse_swap_action(
        self,
        token_in: str,
        token_out: str,
        amount_in: int,
        targets: List[str],
        data: List[bytes],
    ):
        universal_token_swapper_data = UniversalTokenSwapperData(
            targets=targets,
            data=data,
        )

        universal_token_swapper_enter_data = UniversalTokenSwapperEnterData(
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            data=universal_token_swapper_data,
        )

        return [
            FuseActionDynamicStruct(
                self._universal_token_swapper_fuse_address,
                universal_token_swapper_enter_data.function_call(),
            )
        ]

    def supports(self, market_id: MarketId) -> bool:
        if market_id is None:
            raise ValueError("marketId is required")
        if not hasattr(market_id, "protocol_id"):
            raise AttributeError("marketId does not have attribute 'protocol_id'")
        if not hasattr(market_id, "market_id"):
            raise AttributeError("marketId does not have attribute 'market_id'")
        return (
            market_id.protocol_id == self.PROTOCOL_ID
            and market_id.market_id == "universal-swap"
        )

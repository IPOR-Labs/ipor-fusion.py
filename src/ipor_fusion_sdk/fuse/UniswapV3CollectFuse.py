from typing import List

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion_sdk.fuse.FuseActionDynamicStruct import FuseActionDynamicStruct
from ipor_fusion_sdk.operation.BaseOperation import MarketId


class UniswapV3CollectFuseEnterData:
    def __init__(self, token_ids: List[int]):
        self.token_ids = token_ids

    def encode(self) -> bytes:
        return encode(["(uint256[])"], [[self.token_ids]])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((uint256[]))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class UniswapV3CollectFuse:
    PROTOCOL_ID = "uniswap-v3"

    def __init__(self, uniswap_v3_collect_fuse_address: str):
        self.uniswap_v3_collect_fuse_address = self._require_non_null(
            uniswap_v3_collect_fuse_address,
            "uniswap_v3_collect_fuse_address is required",
        )

    def create_fuse_enter_action(self, token_ids: List[int]):
        data = UniswapV3CollectFuseEnterData(token_ids)
        return [
            FuseActionDynamicStruct(
                self.uniswap_v3_collect_fuse_address, data.function_call()
            )
        ]

    def create_fuse_exit_action(self):
        raise NotImplementedError(
            "Fuse exit action not supported for UniswapV3CollectFuse"
        )

    @staticmethod
    def _require_non_null(value, message):
        if value is None:
            raise ValueError(message)
        return value

    def supports(self, market_id: MarketId) -> bool:
        if market_id is None:
            raise ValueError("marketId is required")
        if not hasattr(market_id, "protocol_id"):
            raise AttributeError("marketId does not have attribute 'protocol_id'")
        if not hasattr(market_id, "market_id"):
            raise AttributeError("marketId does not have attribute 'market_id'")
        return (
            market_id.protocol_id == self.PROTOCOL_ID
            and market_id.market_id == "collect"
        )

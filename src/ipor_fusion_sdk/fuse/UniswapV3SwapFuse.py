from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion_sdk.fuse.Fuse import Fuse
from ipor_fusion_sdk.fuse.FuseActionDynamicStruct import FuseActionDynamicStruct
from ipor_fusion_sdk.operation.BaseOperation import MarketId


class UniswapV3SwapPathFuseEnterData:
    def __init__(self, token_in_address: str, token_out_address: str, fee: int):
        self.token_in_address = token_in_address
        self.token_out_address = token_out_address
        self.fee = fee

    def encode(self) -> bytes:
        return encode_packed(
            ["address", "uint24", "address"],
            [self.token_in_address, self.fee, self.token_out_address],
        )


class UniswapV3SwapFuseEnterData:
    def __init__(self, token_in_amount: int, min_out_amount: int, path: bytes):
        self.token_in_amount = token_in_amount
        self.min_out_amount = min_out_amount
        self.path = path

    def encode(self) -> bytes:
        return encode(
            ["(uint256,uint256,bytes)"],
            [[self.token_in_amount, self.min_out_amount, self.path]],
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((uint256,uint256,bytes))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class UniswapV3SwapFuse(Fuse):
    PROTOCOL_ID = "uniswap-v3"

    def __init__(self, uniswap_v_3_swap_fuse_address: str):
        self.uniswap_v_3_swap_fuse_address = self._require_non_null(
            uniswap_v_3_swap_fuse_address, "uniswap_v_3_swap_fuse_address is required"
        )

    def create_fuse_swap_action(
        self,
        token_in_address: str,
        token_out_address: str,
        fee: int,
        token_in_amount: int,
        min_out_amount: int,
    ):
        path = UniswapV3SwapPathFuseEnterData(
            token_in_address, token_out_address, fee
        ).encode()
        data = UniswapV3SwapFuseEnterData(token_in_amount, min_out_amount, path)
        return [
            FuseActionDynamicStruct(
                self.uniswap_v_3_swap_fuse_address, data.function_call()
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
            market_id.protocol_id == self.PROTOCOL_ID and market_id.market_id == "swap"
        )

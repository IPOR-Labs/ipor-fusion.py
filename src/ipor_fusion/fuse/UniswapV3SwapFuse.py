from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class UniswapV3SwapPathFuseEnterData:
    def __init__(
        self,
        token_in_address: ChecksumAddress,
        token_out_address: ChecksumAddress,
        fee: int,
    ):
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


class UniswapV3SwapFuse:
    PROTOCOL_ID = "uniswap-v3"

    def __init__(self, uniswap_v_3_swap_fuse_address: ChecksumAddress):
        self.uniswap_v_3_swap_fuse_address = uniswap_v_3_swap_fuse_address

    def swap(
        self,
        token_in_address: ChecksumAddress,
        token_out_address: ChecksumAddress,
        fee: int,
        token_in_amount: int,
        min_out_amount: int,
    ) -> FuseAction:
        path = UniswapV3SwapPathFuseEnterData(
            token_in_address, token_out_address, fee
        ).encode()
        data = UniswapV3SwapFuseEnterData(token_in_amount, min_out_amount, path)
        return FuseAction(self.uniswap_v_3_swap_fuse_address, data.function_call())

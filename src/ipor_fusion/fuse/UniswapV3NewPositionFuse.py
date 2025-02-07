from typing import List

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class UniswapV3NewPositionFuseEnterData:
    def __init__(
        self,
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
        self.token0 = token0
        self.token1 = token1
        self.fee = fee
        self.tick_lower = tick_lower
        self.tick_upper = tick_upper
        self.amount0_desired = amount0_desired
        self.amount1_desired = amount1_desired
        self.amount0_min = amount0_min
        self.amount1_min = amount1_min
        self.deadline = deadline

    def encode(self) -> bytes:
        return encode(
            [
                "(address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256)"
            ],
            [
                [
                    self.token0,
                    self.token1,
                    self.fee,
                    self.tick_lower,
                    self.tick_upper,
                    self.amount0_desired,
                    self.amount1_desired,
                    self.amount0_min,
                    self.amount1_min,
                    self.deadline,
                ]
            ],
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector(
            "enter((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256))"
        )

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class UniswapV3NewPositionFuseExitData:
    def __init__(self, token_ids: List[int]):
        self.token_ids = token_ids

    def encode(self) -> bytes:
        return encode(["(uint256[])"], [[self.token_ids]])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("exit((uint256[]))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class UniswapV3NewPositionFuse:
    PROTOCOL_ID = "uniswap-v3"

    def __init__(self, uniswap_v_3_new_position_fuse_address: ChecksumAddress):
        self.uniswap_v_3_new_position_fuse_address = (
            uniswap_v_3_new_position_fuse_address
        )

    def new_position(
        self,
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
    ) -> FuseAction:
        data = UniswapV3NewPositionFuseEnterData(
            token0,
            token1,
            fee,
            tick_lower,
            tick_upper,
            amount0_desired,
            amount1_desired,
            amount0_min,
            amount1_min,
            deadline,
        )
        return FuseAction(
            self.uniswap_v_3_new_position_fuse_address, data.function_call()
        )

    def close_position(self, token_ids: List[int]) -> FuseAction:
        data = UniswapV3NewPositionFuseExitData(token_ids)
        return FuseAction(
            self.uniswap_v_3_new_position_fuse_address, data.function_call()
        )

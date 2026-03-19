from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuses.base import Fuse, FuseAction


class UniswapV3SwapFuse(Fuse):
    def swap(
        self,
        token_in: ChecksumAddress,
        token_out: ChecksumAddress,
        fee: int,
        amount_in: int,
        min_amount_out: int,
    ) -> FuseAction:
        path = encode_packed(
            ["address", "uint24", "address"], [token_in, fee, token_out]
        )
        data = encode(["(uint256,uint256,bytes)"], [[amount_in, min_amount_out, path]])
        selector = function_signature_to_4byte_selector(
            "enter((uint256,uint256,bytes))"
        )
        return FuseAction(fuse=self._address, data=selector + data)


class UniswapV3NewPositionFuse(Fuse):
    def new_position(
        self,
        token0: ChecksumAddress,
        token1: ChecksumAddress,
        fee: int,
        tick_lower: int,
        tick_upper: int,
        amount0_desired: int,
        amount1_desired: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ) -> FuseAction:
        data = encode(
            [
                "(address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256)"
            ],
            [
                [
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
                ]
            ],
        )
        selector = function_signature_to_4byte_selector(
            "enter((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256))"
        )
        return FuseAction(fuse=self._address, data=selector + data)

    def close_position(self, token_ids: list[int]) -> FuseAction:
        data = encode(["(uint256[])"], [[token_ids]])
        selector = function_signature_to_4byte_selector("exit((uint256[]))")
        return FuseAction(fuse=self._address, data=selector + data)


class UniswapV3ModifyPositionFuse(Fuse):
    def increase_liquidity(
        self,
        token0: ChecksumAddress,
        token1: ChecksumAddress,
        token_id: int,
        amount0_desired: int,
        amount1_desired: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ) -> FuseAction:
        data = encode(
            ["(address,address,uint256,uint256,uint256,uint256,uint256,uint256)"],
            [
                [
                    token0,
                    token1,
                    token_id,
                    amount0_desired,
                    amount1_desired,
                    amount0_min,
                    amount1_min,
                    deadline,
                ]
            ],
        )
        selector = function_signature_to_4byte_selector(
            "enter((address,address,uint256,uint256,uint256,uint256,uint256,uint256))"
        )
        return FuseAction(fuse=self._address, data=selector + data)

    def decrease_liquidity(
        self,
        token_id: int,
        liquidity: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ) -> FuseAction:
        data = encode(
            ["(uint256,uint128,uint256,uint256,uint256)"],
            [[token_id, liquidity, amount0_min, amount1_min, deadline]],
        )
        selector = function_signature_to_4byte_selector(
            "exit((uint256,uint128,uint256,uint256,uint256))"
        )
        return FuseAction(fuse=self._address, data=selector + data)


class UniswapV3CollectFuse(Fuse):
    def collect(self, token_ids: list[int]) -> FuseAction:
        data = encode(["(uint256[])"], [[token_ids]])
        selector = function_signature_to_4byte_selector("enter((uint256[]))")
        return FuseAction(fuse=self._address, data=selector + data)

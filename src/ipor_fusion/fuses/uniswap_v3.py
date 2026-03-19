from eth_abi.packed import encode_packed
from eth_typing import ChecksumAddress

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
        self._validate_address(token_in, "token_in")
        self._validate_address(token_out, "token_out")
        self._validate_amount(amount_in, "amount_in")
        path = encode_packed(
            ["address", "uint24", "address"], [token_in, fee, token_out]
        )
        return self._action_raw(
            "enter((uint256,uint256,bytes))",
            ["(uint256,uint256,bytes)"],
            [[amount_in, min_amount_out, path]],
        )


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
        self._validate_address(token0, "token0")
        self._validate_address(token1, "token1")
        self._validate_amount(amount0_desired, "amount0_desired")
        self._validate_amount(amount1_desired, "amount1_desired")
        return self._action_raw(
            "enter((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256))",
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

    def close_position(self, token_ids: list[int]) -> FuseAction:
        return self._action_raw("exit((uint256[]))", ["(uint256[])"], [[token_ids]])


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
        self._validate_address(token0, "token0")
        self._validate_address(token1, "token1")
        self._validate_amount(amount0_desired, "amount0_desired")
        self._validate_amount(amount1_desired, "amount1_desired")
        return self._action_raw(
            "enter((address,address,uint256,uint256,uint256,uint256,uint256,uint256))",
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

    def decrease_liquidity(
        self,
        token_id: int,
        liquidity: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ) -> FuseAction:
        self._validate_amount(liquidity, "liquidity")
        return self._action_raw(
            "exit((uint256,uint128,uint256,uint256,uint256))",
            ["(uint256,uint128,uint256,uint256,uint256)"],
            [[token_id, liquidity, amount0_min, amount1_min, deadline]],
        )


class UniswapV3CollectFuse(Fuse):
    def collect(self, token_ids: list[int]) -> FuseAction:
        return self._action_raw("enter((uint256[]))", ["(uint256[])"], [[token_ids]])

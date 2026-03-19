from typing import List

from eth_typing import ChecksumAddress

from ipor_fusion.errors import UnsupportedFuseError
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.fuses.uniswap_v3 import (
    UniswapV3SwapFuse,
    UniswapV3NewPositionFuse,
    UniswapV3ModifyPositionFuse,
    UniswapV3CollectFuse,
)
from ipor_fusion.markets.base import SwapProtocol, LiquidityProtocol


class UniswapV3Market(SwapProtocol, LiquidityProtocol):
    def __init__(
        self,
        swap_fuse: ChecksumAddress = None,
        new_position_fuse: ChecksumAddress = None,
        modify_position_fuse: ChecksumAddress = None,
        collect_fuse: ChecksumAddress = None,
    ):
        self._swap_fuse = UniswapV3SwapFuse(swap_fuse) if swap_fuse else None
        self._new_position_fuse = (
            UniswapV3NewPositionFuse(new_position_fuse) if new_position_fuse else None
        )
        self._modify_position_fuse = (
            UniswapV3ModifyPositionFuse(modify_position_fuse)
            if modify_position_fuse
            else None
        )
        self._collect_fuse = (
            UniswapV3CollectFuse(collect_fuse) if collect_fuse else None
        )

    def swap(
        self,
        token_in: ChecksumAddress = None,
        token_out: ChecksumAddress = None,
        fee: int = 3000,
        amount_in: int = 0,
        min_amount_out: int = 0,
        **kwargs,
    ) -> FuseAction:
        if not self._swap_fuse:
            raise UnsupportedFuseError("UniswapV3SwapFuse")
        return self._swap_fuse.swap(token_in, token_out, fee, amount_in, min_amount_out)

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
        **kwargs,
    ) -> FuseAction:
        if not self._new_position_fuse:
            raise UnsupportedFuseError("UniswapV3NewPositionFuse")
        return self._new_position_fuse.new_position(
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

    def close_position(self, token_ids: List[int]) -> FuseAction:
        if not self._new_position_fuse:
            raise UnsupportedFuseError("UniswapV3NewPositionFuse")
        return self._new_position_fuse.close_position(token_ids)

    def increase_liquidity(
        self,
        token_id: int,
        amount0_desired: int,
        amount1_desired: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
        token0: ChecksumAddress = None,
        token1: ChecksumAddress = None,
        **kwargs,
    ) -> FuseAction:
        if not self._modify_position_fuse:
            raise UnsupportedFuseError("UniswapV3ModifyPositionFuse")
        return self._modify_position_fuse.increase_liquidity(
                token0,
                token1,
                token_id,
                amount0_desired,
                amount1_desired,
                amount0_min,
                amount1_min,
                deadline,
            )

    def decrease_liquidity(
        self,
        token_id: int,
        liquidity: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
        **kwargs,
    ) -> FuseAction:
        if not self._modify_position_fuse:
            raise UnsupportedFuseError("UniswapV3ModifyPositionFuse")
        return self._modify_position_fuse.decrease_liquidity(
                token_id, liquidity, amount0_min, amount1_min, deadline
            )

    def collect(
        self,
        token_ids: List[int] = None,
        token_id: int = None,
        amount0_max: int = 0,
        amount1_max: int = 0,
        **kwargs,
    ) -> FuseAction:
        if not self._collect_fuse:
            raise UnsupportedFuseError("UniswapV3CollectFuse")
        if token_ids:
            return self._collect_fuse.collect(token_ids)
        if token_id:
            return self._collect_fuse.collect([token_id])
        raise ValueError("Either token_ids or token_id must be provided")

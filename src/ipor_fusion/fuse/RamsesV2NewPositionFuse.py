from typing import List

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class RamsesV2NewPositionFuseEnterData:
    _args_signature = "(address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256,uint256)"
    _function_signature = f"enter({_args_signature})"

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
        ve_ram_token_id: int,
    ):
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
        self._ve_ram_token_id = ve_ram_token_id

    def encode(self) -> bytes:
        return encode(
            [self._args_signature],
            [
                [
                    self._token0,
                    self._token1,
                    self._fee,
                    self._tick_lower,
                    self._tick_upper,
                    self._amount0_desired,
                    self._amount1_desired,
                    self._amount0_min,
                    self._amount1_min,
                    self._deadline,
                    self._ve_ram_token_id,
                ]
            ],
        )

    def function_selector(self) -> bytes:
        return function_signature_to_4byte_selector(self._function_signature)

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class RamsesV2NewPositionFuseExitData:
    _args_signature = "(uint256[])"
    _function_signature = f"exit({_args_signature})"

    def __init__(self, token_ids: List[int]):
        self._token_ids = token_ids

    def encode(self) -> bytes:
        return encode([self._args_signature], [[self._token_ids]])

    def function_selector(self) -> bytes:
        return function_signature_to_4byte_selector(self._function_signature)

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class RamsesV2NewPositionFuse:
    PROTOCOL_ID = "ramses-v2"

    def __init__(self, ramses_v2_new_position_fuse_address: ChecksumAddress):
        self._ramses_v2_new_position_fuse_address = ramses_v2_new_position_fuse_address

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
        ve_ram_token_id: int,
    ) -> FuseAction:
        data = RamsesV2NewPositionFuseEnterData(
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
            ve_ram_token_id,
        )
        return FuseAction(
            self._ramses_v2_new_position_fuse_address, data.function_call()
        )

    def close_position(self, token_ids: List[int]) -> FuseAction:
        data = RamsesV2NewPositionFuseExitData(token_ids)
        return FuseAction(
            self._ramses_v2_new_position_fuse_address, data.function_call()
        )

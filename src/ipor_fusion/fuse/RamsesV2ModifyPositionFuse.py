from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class RamsesV2ModifyPositionFuseEnterData:

    _args_signature = (
        "(address,address,uint256,uint256,uint256,uint256,uint256,uint256)"
    )
    _function_signature = f"enter({_args_signature})"
    _function_selector = function_signature_to_4byte_selector(_function_signature)

    def __init__(
        self,
        token0: str,
        token1: str,
        token_id: int,
        amount0_desired: int,
        amount1_desired: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ):
        self.token0 = token0
        self.token1 = token1
        self.token_id = token_id
        self.amount0_desired = amount0_desired
        self.amount1_desired = amount1_desired
        self.amount0_min = amount0_min
        self.amount1_min = amount1_min
        self.deadline = deadline

    def __encode(self) -> bytes:
        return encode(
            [self._args_signature],
            [
                [
                    self.token0,
                    self.token1,
                    self.token_id,
                    self.amount0_desired,
                    self.amount1_desired,
                    self.amount0_min,
                    self.amount1_min,
                    self.deadline,
                ]
            ],
        )

    def function_call(self) -> bytes:
        return self._function_selector + self.__encode()


class RamsesV2ModifyPositionFuseExitData:

    _args_signature = "(uint256,uint128,uint256,uint256,uint256)"
    _function_signature = f"exit({_args_signature})"
    _function_selector = function_signature_to_4byte_selector(_function_signature)

    def __init__(
        self,
        token_id: int,
        liquidity: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ):
        self.token_id = token_id
        self.liquidity = liquidity
        self.amount0_min = amount0_min
        self.amount1_min = amount1_min
        self.deadline = deadline

    def __encode(self) -> bytes:
        return encode(
            [self._args_signature],
            [
                [
                    self.token_id,
                    self.liquidity,
                    self.amount0_min,
                    self.amount1_min,
                    self.deadline,
                ]
            ],
        )

    def function_call(self) -> bytes:
        return self._function_selector + self.__encode()


class RamsesV2ModifyPositionFuse:
    PROTOCOL_ID = "ramses-v2"

    def __init__(self, ramses_v2_modify_position_fuse_address: ChecksumAddress):
        self.ramses_v2_modify_position_fuse_address = self._require_non_null(
            ramses_v2_modify_position_fuse_address,
            "ramses_v2_modify_position_fuse_address is required",
        )

    def increase_position(
        self,
        token0: str,
        token1: str,
        token_id: int,
        amount0_desired: int,
        amount1_desired: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ) -> FuseAction:
        data = RamsesV2ModifyPositionFuseEnterData(
            token0,
            token1,
            token_id,
            amount0_desired,
            amount1_desired,
            amount0_min,
            amount1_min,
            deadline,
        )
        return FuseAction(
            self.ramses_v2_modify_position_fuse_address, data.function_call()
        )

    def decrease_position(
        self,
        token_id: int,
        liquidity: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
    ) -> FuseAction:
        data = RamsesV2ModifyPositionFuseExitData(
            token_id, liquidity, amount0_min, amount1_min, deadline
        )
        return FuseAction(
            self.ramses_v2_modify_position_fuse_address, data.function_call()
        )

    @staticmethod
    def _require_non_null(value, message):
        if value is None:
            raise ValueError(message)
        return value

from dataclasses import dataclass

from eth_abi import decode
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.fuses.base import Fuse, FuseAction


class RamsesV2NewPositionFuse(Fuse):
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
        ve_ram_token_id: int,
    ) -> FuseAction:
        self._validate_address(token0, "token0")
        self._validate_address(token1, "token1")
        self._validate_amount(amount0_desired, "amount0_desired")
        self._validate_amount(amount1_desired, "amount1_desired")
        return self._action_raw(
            "enter((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256,uint256))",
            [
                "(address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256,uint256)"
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
                    ve_ram_token_id,
                ]
            ],
        )

    def close_position(self, token_ids: list[int]) -> FuseAction:
        return self._action_raw("exit((uint256[]))", ["(uint256[])"], [[token_ids]])


class RamsesV2ModifyPositionFuse(Fuse):
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


class RamsesV2CollectFuse(Fuse):
    def collect(self, token_ids: list[int]) -> FuseAction:
        return self._action_raw("enter((uint256[]))", ["(uint256[])"], [[token_ids]])


class RamsesClaimFuse(Fuse):
    def claim(self, token_ids: list[int], token_rewards: list[list[str]]) -> FuseAction:
        return self._action_raw(
            "claim(uint256[],address[][])",
            ["uint256[]", "address[][]"],
            [token_ids, token_rewards],
        )


@dataclass
class RamsesNewPositionEvent:
    version: str
    token_id: int
    liquidity: int
    amount0: int
    amount1: int
    sender: str
    recipient: str
    fee: int
    tick_lower: int
    tick_upper: int


def extract_ramses_new_position_events(
    receipt: TxReceipt,
) -> list[RamsesNewPositionEvent]:
    event_signature_hash = Web3.keccak(
        text="RamsesV2NewPositionFuseEnter(address,uint256,uint128,uint256,uint256,address,address,uint24,int24,int24)"
    )
    events = []
    for log in receipt["logs"]:
        if log["topics"][0] == event_signature_hash:
            decoded = tuple(
                decode(
                    [
                        "address",
                        "uint256",
                        "uint128",
                        "uint256",
                        "uint256",
                        "address",
                        "address",
                        "uint24",
                        "int24",
                        "int24",
                    ],
                    log["data"],
                )
            )
            events.append(
                RamsesNewPositionEvent(
                    version=decoded[0],
                    token_id=decoded[1],
                    liquidity=decoded[2],
                    amount0=decoded[3],
                    amount1=decoded[4],
                    sender=decoded[5],
                    recipient=decoded[6],
                    fee=decoded[7],
                    tick_lower=decoded[8],
                    tick_upper=decoded[9],
                )
            )
    return events

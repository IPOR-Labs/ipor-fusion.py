from dataclasses import dataclass
from typing import List

from eth_abi import decode
from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.addresses import ARBITRUM_RAM_TOKEN, ARBITRUM_XRAM_TOKEN
from ipor_fusion.errors import UnsupportedFuseError
from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.fuses.ramses_v2 import (
    RamsesV2NewPositionFuse,
    RamsesV2ModifyPositionFuse,
    RamsesV2CollectFuse,
    RamsesClaimFuse,
)
from ipor_fusion.markets.base import LiquidityProtocol


@dataclass
class RamsesNewPositionEvent:
    version: ChecksumAddress
    token_id: int
    liquidity: int
    amount0: int
    amount1: int
    sender: str
    recipient: str
    fee: int
    tick_lower: int
    tick_upper: int


class RamsesV2Market(LiquidityProtocol):
    def __init__(
        self,
        new_position_fuse: ChecksumAddress = None,
        modify_position_fuse: ChecksumAddress = None,
        collect_fuse: ChecksumAddress = None,
        claim_fuse: ChecksumAddress = None,
    ):
        self._new_position_fuse = (
            RamsesV2NewPositionFuse(new_position_fuse) if new_position_fuse else None
        )
        self._modify_position_fuse = (
            RamsesV2ModifyPositionFuse(modify_position_fuse)
            if modify_position_fuse
            else None
        )
        self._collect_fuse = RamsesV2CollectFuse(collect_fuse) if collect_fuse else None
        self._claim_fuse = RamsesClaimFuse(claim_fuse) if claim_fuse else None

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
        ve_ram_token_id: int = 0,
        **kwargs,
    ) -> FuseAction:
        if not self._new_position_fuse:
            raise UnsupportedFuseError("RamsesV2NewPositionFuse")
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
                ve_ram_token_id,
            )

    def close_position(self, token_ids: List[int]) -> FuseAction:
        if not self._new_position_fuse:
            raise UnsupportedFuseError("RamsesV2NewPositionFuse")
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
            raise UnsupportedFuseError("RamsesV2ModifyPositionFuse")
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
            raise UnsupportedFuseError("RamsesV2ModifyPositionFuse")
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
            raise UnsupportedFuseError("RamsesV2CollectFuse")
        if token_ids:
            return self._collect_fuse.collect(token_ids)
        if token_id:
            return self._collect_fuse.collect([token_id])
        raise ValueError("Either token_ids or token_id must be provided")

    def claim_rewards(
        self, token_ids: List[int], token_rewards: List[List[str]]
    ) -> List[FuseAction]:
        if not self._claim_fuse:
            raise UnsupportedFuseError("RamsesClaimFuse")
        return [self._claim_fuse.claim(token_ids, token_rewards)]

    @staticmethod
    def ram():
        from ipor_fusion.core.erc20 import ERC20Token

        return ERC20Token(ARBITRUM_RAM_TOKEN)

    @staticmethod
    def x_ram():
        from ipor_fusion.core.erc20 import ERC20Token

        return ERC20Token(ARBITRUM_XRAM_TOKEN)

    @staticmethod
    def extract_new_position_enter_events(
        receipt: TxReceipt,
    ) -> List[RamsesNewPositionEvent]:
        event_signature_hash = Web3.keccak(
            text="RamsesV2NewPositionFuseEnter(address,uint256,uint128,uint256,uint256,address,address,uint24,int24,int24)"
        )
        events = []
        for log in receipt.logs:
            if log.topics[0] == event_signature_hash:
                decoded = decode(
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

from dataclasses import dataclass

from eth_abi.packed import encode_packed
from eth_typing import ChecksumAddress
from web3.types import TxReceipt

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.fuses.events import extract_events
from ipor_fusion.types import Amount, Fee, Tick, TokenId


class UniswapV3SwapFuse(Fuse):
    """Fuse for executing token swaps on Uniswap V3."""

    def swap(
        self,
        *,
        token_in: ChecksumAddress,
        token_out: ChecksumAddress,
        fee: Fee,
        amount_in: Amount,
        min_amount_out: Amount,
    ) -> FuseAction:
        self._validate_address(token_in, "token_in")
        self._validate_address(token_out, "token_out")
        self._validate_amount(amount_in, "amount_in")
        path = encode_packed(
            ["address", "uint24", "address"], [token_in, fee, token_out]
        )
        return self._action_raw(
            "enter((uint256,uint256,bytes))",
            [[amount_in, min_amount_out, path]],
        )


class UniswapV3NewPositionFuse(Fuse):
    """Fuse for minting and closing liquidity positions on Uniswap V3."""

    def new_position(
        self,
        *,
        token0: ChecksumAddress,
        token1: ChecksumAddress,
        fee: Fee,
        tick_lower: Tick,
        tick_upper: Tick,
        amount0_desired: Amount,
        amount1_desired: Amount,
        amount0_min: Amount,
        amount1_min: Amount,
        deadline: int,
    ) -> FuseAction:
        self._validate_address(token0, "token0")
        self._validate_address(token1, "token1")
        self._validate_amount(amount0_desired, "amount0_desired")
        self._validate_amount(amount1_desired, "amount1_desired")
        return self._action_raw(
            "enter((address,address,uint24,int24,int24,uint256,uint256,uint256,uint256,uint256))",
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

    def close_position(self, token_ids: list[TokenId]) -> FuseAction:
        self._validate_non_empty_list(token_ids, "token_ids")
        return self._action_raw("exit((uint256[]))", [[token_ids]])


class UniswapV3ModifyPositionFuse(Fuse):
    """Fuse for increasing and decreasing liquidity on Uniswap V3 positions."""

    def increase_liquidity(
        self,
        *,
        token0: ChecksumAddress,
        token1: ChecksumAddress,
        token_id: TokenId,
        amount0_desired: Amount,
        amount1_desired: Amount,
        amount0_min: Amount,
        amount1_min: Amount,
        deadline: int,
    ) -> FuseAction:
        self._validate_address(token0, "token0")
        self._validate_address(token1, "token1")
        self._validate_token_id(token_id, "token_id")
        self._validate_amount(amount0_desired, "amount0_desired")
        self._validate_amount(amount1_desired, "amount1_desired")
        return self._action_raw(
            "enter((address,address,uint256,uint256,uint256,uint256,uint256,uint256))",
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
        *,
        token_id: TokenId,
        liquidity: Amount,
        amount0_min: Amount,
        amount1_min: Amount,
        deadline: int,
    ) -> FuseAction:
        self._validate_token_id(token_id, "token_id")
        self._validate_amount(liquidity, "liquidity")
        return self._action_raw(
            "exit((uint256,uint128,uint256,uint256,uint256))",
            [[token_id, liquidity, amount0_min, amount1_min, deadline]],
        )


class UniswapV3CollectFuse(Fuse):
    """Fuse for collecting accrued fees from Uniswap V3 positions."""

    def collect(self, token_ids: list[TokenId]) -> FuseAction:
        self._validate_non_empty_list(token_ids, "token_ids")
        return self._action_raw("enter((uint256[]))", [[token_ids]])


@dataclass
class UniswapV3NewPositionEvent:
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


@dataclass
class UniswapV3ClosePositionEvent:
    version: str
    token_id: int


class UniswapV3Events:
    """Decoder for Uniswap V3 fuse events emitted in transaction receipts."""

    @staticmethod
    def extract_new_position_events(
        receipt: TxReceipt,
    ) -> list[UniswapV3NewPositionEvent]:
        return extract_events(
            receipt,
            "UniswapV3NewPositionFuseEnter(address,uint256,uint128,uint256,uint256,address,address,uint24,int24,int24)",
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
            UniswapV3NewPositionEvent,
        )

    @staticmethod
    def extract_close_position_events(
        receipt: TxReceipt,
    ) -> list[UniswapV3ClosePositionEvent]:
        return extract_events(
            receipt,
            "UniswapV3NewPositionFuseExit(address,uint256)",
            ["address", "uint256"],
            UniswapV3ClosePositionEvent,
        )

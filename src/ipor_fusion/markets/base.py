from abc import ABC, abstractmethod
from typing import List

from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import FuseAction


class LendingProtocol(ABC):
    @abstractmethod
    def supply(self, asset: ChecksumAddress, amount: int, **kwargs) -> FuseAction:
        pass

    @abstractmethod
    def withdraw(
        self, asset: ChecksumAddress, amount: int, **kwargs
    ) -> FuseAction:
        pass


class BorrowingProtocol(LendingProtocol):
    @abstractmethod
    def borrow(self, asset: ChecksumAddress, amount: int, **kwargs) -> FuseAction:
        pass

    @abstractmethod
    def repay(self, asset: ChecksumAddress, amount: int, **kwargs) -> FuseAction:
        pass


class SwapProtocol(ABC):
    @abstractmethod
    def swap(
        self,
        token_in: ChecksumAddress,
        token_out: ChecksumAddress,
        amount_in: int,
        min_amount_out: int,
        **kwargs,
    ) -> FuseAction:
        pass


class LiquidityProtocol(ABC):
    @abstractmethod
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
        pass

    @abstractmethod
    def increase_liquidity(
        self,
        token_id: int,
        amount0_desired: int,
        amount1_desired: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
        **kwargs,
    ) -> FuseAction:
        pass

    @abstractmethod
    def decrease_liquidity(
        self,
        token_id: int,
        liquidity: int,
        amount0_min: int,
        amount1_min: int,
        deadline: int,
        **kwargs,
    ) -> FuseAction:
        pass

    @abstractmethod
    def collect(
        self,
        token_id: int,
        amount0_max: int,
        amount1_max: int,
        **kwargs,
    ) -> FuseAction:
        pass

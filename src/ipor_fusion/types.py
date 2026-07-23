from dataclasses import dataclass
from typing import ClassVar, Literal, NewType, TypeAlias

from eth_typing import ChecksumAddress

Amount = NewType("Amount", int)
Shares = NewType("Shares", int)
Decimals = NewType("Decimals", int)
MarketId = NewType("MarketId", int)
TokenId = NewType("TokenId", int)
Fee = NewType("Fee", int)
Tick = NewType("Tick", int)
Liquidity = NewType("Liquidity", int)
RoleId = NewType("RoleId", int)
ChainId = NewType("ChainId", int)
# Hex string without 0x prefix (e.g. "abc123..." not "0xabc123...")
MorphoBlueMarketId = NewType("MorphoBlueMarketId", str)

MAX_UINT256 = (1 << 256) - 1

# Oracle-mapping status vocabulary — semantics documented in the
# readers/oracle_mapping.py module docstring. Lives here so the MCP models
# (pydantic-only runtime imports) can share the values without importing
# the reader stack.
NodeStatus: TypeAlias = Literal["resolved", "partially_resolved", "partial"]
MappingStatus: TypeAlias = Literal["resolved", "partially_resolved", "unresolved"]
AssetSource: TypeAlias = Literal["getConfiguredAssets", "events"]


@dataclass(slots=True)
class Price:
    """USD-denominated price of an on-chain asset."""

    asset: ChecksumAddress
    amount: Amount
    decimals: Decimals

    def __post_init__(self):
        if self.decimals < 0:
            raise ValueError(f"decimals must be non-negative, got {self.decimals}")

    def readable(self) -> float:
        return self.amount / (10**self.decimals)

    def __repr__(self):
        return f"Price(asset={self.asset}, amount={self.amount}, decimals={self.decimals}, readable={self.readable()} USD)"

    def __str__(self):
        return self.__repr__()


class Period(int):
    """Time duration in seconds with common unit constants."""

    SECOND: ClassVar["Period"]
    MINUTE: ClassVar["Period"]
    HOUR: ClassVar["Period"]
    DAY: ClassVar["Period"]
    WEEK: ClassVar["Period"]


Period.SECOND = Period(1)
Period.MINUTE = Period(60 * Period.SECOND)
Period.HOUR = Period(60 * Period.MINUTE)
Period.DAY = Period(24 * Period.HOUR)
Period.WEEK = Period(7 * Period.DAY)

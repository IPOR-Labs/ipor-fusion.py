from dataclasses import dataclass
from typing import NewType

from eth_typing import ChecksumAddress

Amount = NewType("Amount", int)
Shares = NewType("Shares", int)
Decimals = NewType("Decimals", int)
MarketId = NewType("MarketId", int)
# Hex string without 0x prefix (e.g. "abc123..." not "0xabc123...")
MorphoBlueMarketId = NewType("MorphoBlueMarketId", str)

MAX_UINT256 = (1 << 256) - 1


@dataclass
class Price:
    """USD-denominated price of an on-chain asset."""

    asset: ChecksumAddress
    amount: Amount
    decimals: Decimals

    def readable(self) -> float:
        return self.amount / (10**self.decimals)

    def __repr__(self):
        return f"Price(asset={self.asset}, amount={self.amount}, decimals={self.decimals}, readable={self.readable()} USD)"

    def __str__(self):
        return self.__repr__()


class Period(int):
    """Time duration in seconds with common unit constants."""

    SECOND: "Period"
    MINUTE: "Period"
    HOUR: "Period"
    DAY: "Period"


Period.SECOND = Period(1)
Period.MINUTE = Period(60 * Period.SECOND)
Period.HOUR = Period(60 * Period.MINUTE)
Period.DAY = Period(24 * Period.HOUR)

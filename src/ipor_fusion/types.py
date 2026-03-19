from typing import NewType

from eth_typing import ChecksumAddress

Amount = NewType("Amount", int)
Shares = NewType("Shares", int)
Decimals = NewType("Decimals", int)
MarketId = NewType("MarketId", int)
# Hex string without 0x prefix (e.g. "abc123..." not "0xabc123...")
MorphoBlueMarketId = NewType("MorphoBlueMarketId", str)

MAX_UINT256 = (1 << 256) - 1


class Price:
    def __init__(self, asset: ChecksumAddress, amount: Amount, decimals: Decimals):
        self.asset = asset
        self.amount = amount
        self.decimals = decimals

    def readable(self) -> float:
        return self.amount / (10**self.decimals)

    def __repr__(self):
        return f"Price(asset={self.asset}, amount={self.amount}, decimals={self.decimals}, readable={self.readable()} USD)"

    def __str__(self):
        return self.__repr__()


class Period(int):
    SECOND = 1
    MINUTE = 60 * SECOND
    HOUR = 60 * MINUTE
    DAY = 24 * HOUR

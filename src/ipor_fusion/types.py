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
# A Morpho Blue market id (bytes32) in its canonical spelling: "0x" and 64
# lowercase hex digits, as the Morpho API reports it. Build one with
# parse_morpho_market_id.
MorphoBlueMarketId = NewType("MorphoBlueMarketId", str)
# Crosschain identifiers (bytes32 unless noted), named after the contracts' fields.
CommandId = NewType("CommandId", bytes)
# A transport's message id: the CCIP messageId or the LayerZero guid.
MessageId = NewType("MessageId", bytes)
# The executor's asset id; a Stargate pool's uint16 asset id is StargateAssetId.
AssetId = NewType("AssetId", bytes)
StargateAssetId = NewType("StargateAssetId", int)
ProposalId = NewType("ProposalId", int)
# A factory's uint256 dispatcher deployment request; a return (recall) request
# is the bytes32 ReturnRequestId.
DeploymentRequestId = NewType("DeploymentRequestId", int)
ReturnRequestId = NewType("ReturnRequestId", bytes)
TransferId = NewType("TransferId", bytes)
OperationId = NewType("OperationId", bytes)
PositionSetHash = NewType("PositionSetHash", bytes)

MAX_UINT256 = (1 << 256) - 1
ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"

# Oracle-mapping status vocabulary — semantics documented in the
# readers/oracle_mapping.py module docstring. Lives here so the MCP models
# (pydantic-only runtime imports) can share the values without importing
# the reader stack.
NodeStatus: TypeAlias = Literal["resolved", "partially_resolved", "partial"]
MappingStatus: TypeAlias = Literal["resolved", "partially_resolved", "unresolved"]
AssetSource: TypeAlias = Literal["getConfiguredAssets", "events"]

# Vault-validation vocabulary of `ipor_fusion.validation`, here for the same
# reason: the MCP models share it without importing the validation stack.
Severity: TypeAlias = Literal["ok", "warning", "critical"]
Verdict: TypeAlias = Literal["ok", "warning", "critical"]
Check: TypeAlias = Literal[
    "unimplemented_getter",
    "orphan_fuse",
    "missing_erc20_dependency",
    "unpriceable_token",
    "lending_health",
    "total_assets_reconciliation",
    "pending_withdrawals",
    "market_storage_drift",
    "uncovered_erc20",
    "erc20_cache_stale",
    "missing_price_feed",
]


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


def parse_morpho_market_id(value: str | bytes) -> MorphoBlueMarketId:
    """A Morpho Blue market id from 32 raw bytes or from hex with or without
    ``0x``, in any case; raises ``ValueError`` on anything else."""
    if isinstance(value, bytes):
        if len(value) != 32:
            raise ValueError(f"invalid Morpho market ID (expected 32 bytes): {value!r}")
        return MorphoBlueMarketId("0x" + value.hex())
    digits = value.strip().removeprefix("0x").removeprefix("0X").lower()
    if len(digits) != 64 or any(c not in "0123456789abcdef" for c in digits):
        raise ValueError(f"invalid Morpho market ID (expected 32-byte hex): {value}")
    return MorphoBlueMarketId("0x" + digits)

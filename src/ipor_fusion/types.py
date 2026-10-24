import re
from dataclasses import dataclass
from typing import ClassVar, Literal, NewType, TypeAlias

from eth_typing import ChecksumAddress, HexAddress, HexStr
from eth_utils import to_checksum_address

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
# An AccessManager operation id: keccak256(abi.encode(caller, target, calldata)).
ScheduleId = NewType("ScheduleId", bytes)
# An 18-decimal fixed-point fraction: 10**18 is 1, i.e. 100% (a market limit).
Wad = NewType("Wad", int)
# The ExternalState executor's custodian marker the balance fuse and pre-hook
# compare for equality only: the last confirmed update's block timestamp on an
# executor before the 2026-10-21 contracts release, the confirm count on one of
# that release. Opaque: never compare it with a clock or do arithmetic on it.
CustodianMarker = NewType("CustodianMarker", int)
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
# A LayerZero V2 endpoint id (uint32), distinct from the EVM chain id.
LayerZeroEid = NewType("LayerZeroEid", int)
# A CCIP chain selector (uint64), Chainlink's chain id, distinct from the EVM one.
CcipChainSelector = NewType("CcipChainSelector", int)
ProposalId = NewType("ProposalId", int)
# A factory's uint256 dispatcher deployment request; a return (recall) request
# is the bytes32 ReturnRequestId.
DeploymentRequestId = NewType("DeploymentRequestId", int)
ReturnRequestId = NewType("ReturnRequestId", bytes)
TransferId = NewType("TransferId", bytes)
OperationId = NewType("OperationId", bytes)
PositionSetHash = NewType("PositionSetHash", bytes)
# HyperCore (market 55) identifiers, sized as in the fuses: a spot token's uint64
# index; a uint32 action asset (native perp below 10_000, HIP-3 100_000 +
# dex * 10_000 + index; Hyperliquid's spot pairs are 10_000 + the spot *pair*
# index, which the Fusion order and cancel fuses reject); an order's uint64 oid
# and uint128 cloid; a perp's uint32 margin table.
HyperCoreTokenIndex = NewType("HyperCoreTokenIndex", int)
HyperCoreAsset = NewType("HyperCoreAsset", int)
HyperCoreOrderId = NewType("HyperCoreOrderId", int)
HyperCoreClientOrderId = NewType("HyperCoreClientOrderId", int)
MarginTableId = NewType("MarginTableId", int)
# The pending action's uint24 CoreWriter opcode (HyperCoreLib.ACTION_*, 0 for an
# EVM-side deposit): what kind of action, not which one (that is the nonce).
HyperCoreActionId = NewType("HyperCoreActionId", int)
PayloadHash = NewType("PayloadHash", bytes)
# Addresses whose role the SDK relies on: the wrappers, getters and records that
# produce them return the role type, so a vault, a fuse and an access manager
# cannot be passed for each other. They assert the role; nothing checks on chain.
PlasmaVaultAddress = NewType("PlasmaVaultAddress", ChecksumAddress)
FuseAddress = NewType("FuseAddress", ChecksumAddress)
AccessManagerAddress = NewType("AccessManagerAddress", ChecksumAddress)
# A transaction hash as 0x and 64 hex digits (a HexStr, so web3 takes it).
TxHash = NewType("TxHash", HexStr)
# ExternalStateExecutor's bytes32 balance proposal hash.
ProposalHash = NewType("ProposalHash", bytes)

MAX_UINT256 = (1 << 256) - 1
ZERO_ADDRESS = to_checksum_address("0x" + "00" * 20)

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
    "rewards_manager_balance",
    "orphan_fuse",
    "missing_erc20_dependency",
    "unpriceable_token",
    "lending_health",
    "incomplete_read",
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


def _hex_digits(value: str | bytes, size: int, what: str) -> str:
    if isinstance(value, bytes):
        if len(value) != size:
            raise ValueError(f"invalid {what} (expected {size} bytes): {value!r}")
        return value.hex()
    digits = value[2:].lower()
    if (
        value[:2] not in ("0x", "0X")
        or len(digits) != 2 * size
        or any(c not in "0123456789abcdef" for c in digits)
    ):
        raise ValueError(f"invalid {what} (expected 0x and {size}-byte hex): {value!r}")
    return digits


def parse_hex_address(value: str | bytes) -> HexAddress:
    """An address as ``0x`` and 40 lowercase hex digits, from 20 raw bytes or
    ``0x``-prefixed hex in any case; raises ``ValueError`` on anything else,
    surrounding whitespace included. For a checksummed
    address use `parse_checksum_address`."""
    return HexAddress(HexStr("0x" + _hex_digits(value, 20, "address")))


def parse_tx_hash(value: str | bytes) -> TxHash:
    """A transaction hash as ``0x`` and 64 lowercase hex digits, from 32 raw bytes
    or ``0x``-prefixed hex in any case; raises ``ValueError`` on anything else,
    surrounding whitespace included."""
    return TxHash(HexStr("0x" + _hex_digits(value, 32, "transaction hash")))


def parse_checksum_address(value: str | bytes) -> ChecksumAddress:
    """An address, checksummed from 20 raw bytes or ``0x``/``0X`` and 40 hex
    digits in any case; raises ``ValueError`` on anything else, surrounding
    whitespace included."""
    return to_checksum_address("0x" + _hex_digits(value, 20, "address"))


def parse_plasma_vault_address(value: str | bytes) -> PlasmaVaultAddress:
    """`parse_checksum_address`, labeled as a Plasma Vault's address. The label
    checks nothing on chain."""
    return PlasmaVaultAddress(parse_checksum_address(value))


def parse_fuse_address(value: str | bytes) -> FuseAddress:
    """`parse_checksum_address`, labeled as a fuse's address. The label checks
    nothing on chain."""
    return FuseAddress(parse_checksum_address(value))


def parse_access_manager_address(value: str | bytes) -> AccessManagerAddress:
    """`parse_checksum_address`, labeled as an access manager's address. The
    label checks nothing on chain."""
    return AccessManagerAddress(parse_checksum_address(value))


# ASCII digits only: ``\d`` would also take other scripts' digits, which int() reads.
_DECIMAL_TEXT = re.compile(r"([0-9]*)(?:\.([0-9]*))?")


_UINT256_DIGITS = len(str(MAX_UINT256))


def _check_decimals(decimals: Decimals) -> None:
    if not 0 <= decimals <= 255:
        raise ValueError(f"decimals must be 0..255 (ERC-20 uint8), got {decimals}")


def _parse_units(text: str, decimals: Decimals, what: str) -> int:
    if not isinstance(text, str):
        raise TypeError(
            f"{what} must be text, not {type(text).__name__}: floats lose precision"
        )
    _check_decimals(decimals)
    match = _DECIMAL_TEXT.fullmatch(text.strip())
    if match is None or not (match.group(1) or match.group(2)):
        raise ValueError(f"not a decimal {what}: {text!r}")
    whole, fraction = match.group(1).lstrip("0"), match.group(2) or ""
    if len(fraction) > decimals:
        raise ValueError(
            f"{what} {text!r} has {len(fraction)} fractional digits,"
            f" more than the {decimals} the token carries"
        )
    # Bounded before int(): a longer whole part overflows uint256 at any scale.
    if len(whole) > _UINT256_DIGITS:
        raise ValueError(f"{what} {text!r} exceeds uint256")
    value = int(whole + fraction.ljust(decimals, "0") or "0")
    if value > MAX_UINT256:
        raise ValueError(f"{what} {text!r} exceeds uint256")
    return value


def _format_units(value: int, decimals: Decimals, what: str) -> str:
    if not 0 <= value <= MAX_UINT256:
        raise ValueError(f"{what} must be within uint256, got {value}")
    _check_decimals(decimals)
    whole, fraction = divmod(value, 10**decimals)
    if fraction == 0:
        return str(whole)
    return f"{whole}.{str(fraction).zfill(decimals).rstrip('0')}"


def parse_amount(text: str, decimals: Decimals) -> Amount:
    """The raw `Amount` of ``text`` token units at ``decimals``, exactly:
    ``parse_amount("1.5", Decimals(6))`` is ``Amount(1_500_000)``.

    ``text`` is digits with at most one ``.`` (surrounding whitespace ignored);
    no sign, exponent or separators. ``decimals`` is 0..255, an ERC-20 uint8.
    Raises ``ValueError`` for anything else, for more fractional digits than
    ``decimals`` (nothing is rounded) and for a value above uint256; ``TypeError`` for a non-string, so a float is never
    accepted. Inverse of `format_amount`.
    """
    return Amount(_parse_units(text, decimals, "amount"))


def parse_shares(text: str, decimals: Decimals) -> Shares:
    """`parse_amount` for vault shares at the share token's ``decimals``
    (`PlasmaVault.decimals`, not the asset's)."""
    return Shares(_parse_units(text, decimals, "shares"))


def format_amount(amount: Amount, decimals: Decimals) -> str:
    """``amount`` in token units as exact decimal text, no trailing zeros:
    ``format_amount(Amount(1_500_000), Decimals(6))`` is ``"1.5"``; a whole
    number has no ``.``. Raises ``ValueError`` on an amount outside uint256 or
    ``decimals`` outside 0..255. Inverse of `parse_amount`."""
    return _format_units(amount, decimals, "amount")


def format_shares(shares: Shares, decimals: Decimals) -> str:
    """`format_amount` for vault shares."""
    return _format_units(shares, decimals, "shares")

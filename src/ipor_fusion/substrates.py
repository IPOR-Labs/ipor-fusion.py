"""Public per-market decoding of PlasmaVault bytes32 market substrates.

Each Fusion market stores its substrate grants as raw ``bytes32`` values whose
internal layout is market-specific (plain padded address, typed structs,
Morpho market ids, ...). This module is the canonical decoder registry used by
the CLI/MCP rendering and importable by external services (e.g. the fusion
registry) via :func:`decode_substrate` (re-exported from :mod:`ipor_fusion`).

Encodings are sourced from the Solidity fuse libraries in the ipor-fusion
contracts repo; every non-trivial decoder cites its library.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ipor_fusion.market_ids import IporFusionMarkets


@dataclass
class SubstrateInfo:
    address: str = ""
    raw_hex: str = ""
    type_label: str = ""
    is_error: bool = False
    extra: dict[str, str] = field(default_factory=dict)


# ── per-market substrate decoders ────────────────────────────────────────────
#
# Bit layout "type<<160": 11 zero bytes + 1 type byte + 20 address bytes
#   hex: [22 zeros][2 type chars][40 address chars]
#
# Bit layout "type<<248": 1 type byte + 11 zero bytes + 20 address bytes
#   hex: [2 type chars][22 zeros][40 address chars]
#   Slippage variant: [2 type chars][62 value chars]


def _decode_type_lshift160(hex_str: str, types: dict[int, str]) -> SubstrateInfo:
    """Decode type<<160 | address (Ebisu, Midas, Balancer, Velodrome)."""
    type_byte = int(hex_str[22:24], 16)
    addr = f"0x{hex_str[24:]}"
    label = types.get(type_byte, f"type={type_byte}")
    return SubstrateInfo(address=addr, type_label=label)


def _decode_type_lshift248(hex_str: str, types: dict[int, str]) -> SubstrateInfo:
    """Decode type<<248 | address_or_value (Odos, Velora, UTS, Aave V4)."""
    type_byte = int(hex_str[0:2], 16)
    if (label := types.get(type_byte, f"type={type_byte}")) == "Slippage":
        value = int(hex_str[2:], 16)
        return SubstrateInfo(
            raw_hex=f"0x{hex_str}", type_label=label, extra={"slippage": str(value)}
        )
    addr = f"0x{hex_str[24:]}"
    return SubstrateInfo(address=addr, type_label=label)


def _decode_plain_address(hex_str: str) -> SubstrateInfo:
    """Decode zero-padded address: 12 zero bytes + 20 address bytes."""
    return SubstrateInfo(address=f"0x{hex_str[24:]}")


def _decode_morpho(hex_str: str) -> SubstrateInfo:
    """Raw bytes32 Morpho market ID — no structure."""
    return SubstrateInfo(raw_hex=f"0x{hex_str}", type_label="morpho_market_id")


def _decode_enso(hex_str: str) -> SubstrateInfo:
    """Decode address<<96 | selector<<64 (Enso)."""
    addr = f"0x{hex_str[0:40]}"
    selector = f"0x{hex_str[40:48]}"
    return SubstrateInfo(address=addr, extra={"selector": selector})


def _decode_dolomite(hex_str: str) -> SubstrateInfo:
    """Decode asset<<96 | subAccountId<<88 | canBorrow<<80 (Dolomite)."""
    addr = f"0x{hex_str[0:40]}"
    sub_account_id = int(hex_str[40:42], 16)
    can_borrow = (int(hex_str[42:44], 16) & 0x01) == 1
    return SubstrateInfo(
        address=addr,
        extra={"sub_account_id": str(sub_account_id), "can_borrow": str(can_borrow)},
    )


def _decode_euler_v2(hex_str: str) -> SubstrateInfo:
    """Decode eulerVault<<96 | isCollateral<<88 | canBorrow<<80 | subAccounts<<72.

    Source: EulerFuseLib.substrateToBytes32 — address occupies the high 20 bytes
    (left-aligned), followed by three 1-byte flags. Decoding via the generic
    plain-address path silently produces a malformed address (last 20 bytes are
    flag bytes + zero padding).
    """
    addr = f"0x{hex_str[0:40]}"
    is_collateral = (int(hex_str[40:42], 16) & 0x01) == 1
    can_borrow = (int(hex_str[42:44], 16) & 0x01) == 1
    sub_account = f"0x{hex_str[44:46]}"
    return SubstrateInfo(
        address=addr,
        extra={
            "is_collateral": str(is_collateral),
            "can_borrow": str(can_borrow),
            "sub_account": sub_account,
        },
    )


def _decode_async_action(hex_str: str) -> SubstrateInfo:
    """Decode AsyncActionFuse typed substrates.

    Source: AsyncActionFuseLib.sol — layout [substrateType 1 byte | data 31 bytes]:
    - ALLOWED_AMOUNT_TO_OUTSIDE (0): data = asset<<88 | uint88 amount, so the
      asset address sits directly after the type byte and the cap amount (in
      token decimals) is baked into the substrate key itself.
    - ALLOWED_TARGETS (1): data = target<<32 | bytes4 selector (7 high bytes
      of the data are zero).
    - ALLOWED_EXIT_SLIPPAGE (2): data = uint248 slippage, WAD (1e18 = 100%).
    """
    type_byte = int(hex_str[0:2], 16)
    if type_byte == 0:
        return SubstrateInfo(
            address=f"0x{hex_str[2:42]}",
            type_label="ALLOWED_AMOUNT_TO_OUTSIDE",
            extra={"amount": str(int(hex_str[42:64], 16))},
        )
    if type_byte == 1:
        return SubstrateInfo(
            address=f"0x{hex_str[16:56]}",
            type_label="ALLOWED_TARGETS",
            extra={"selector": f"0x{hex_str[56:64]}"},
        )
    if type_byte == 2:
        return SubstrateInfo(
            raw_hex=f"0x{hex_str}",
            type_label="ALLOWED_EXIT_SLIPPAGE",
            extra={"slippage": str(int(hex_str[2:64], 16))},
        )
    return SubstrateInfo(raw_hex=f"0x{hex_str}", type_label=f"type={type_byte}")


# Market ID → decoder function.  Markets not listed here get raw hex output.
_SUBSTRATE_DECODERS: dict[int, Callable[[str], SubstrateInfo]] = {}


def _register_markets(
    market_ids: list[int], decoder: Callable[[str], SubstrateInfo]
) -> None:
    for mid in market_ids:
        _SUBSTRATE_DECODERS[mid] = decoder


# plain address (zero-padded) — most markets
_register_markets(
    [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9,
        10,
        13,
        15,
        16,
        17,
        18,
        20,
        21,
        23,
        24,
        25,
        26,
        27,
        28,
        29,
        30,
        33,
        34,
        35,
        37,
        46,  # NAPIER — isSubstrateAsAssetGranted, plain asset addresses
        *range(100_001, 100_021),  # ERC4626_0001 .. ERC4626_0020
    ],
    _decode_plain_address,
)
# Morpho markets — raw bytes32
_register_markets([14, 19, 22, 41], _decode_morpho)
# Ebisu
_register_markets(
    [39],
    lambda h: _decode_type_lshift160(h, {0: "UNDEFINED", 1: "ZAPPER", 2: "REGISTRY"}),
)
# Midas
_register_markets(
    [45],
    lambda h: _decode_type_lshift160(
        h,
        {
            0: "UNDEFINED",
            1: "M_TOKEN",
            2: "DEPOSIT_VAULT",
            3: "REDEMPTION_VAULT",
            4: "INSTANT_REDEMPTION_VAULT",
            5: "ASSET",
        },
    ),
)
# Balancer
_register_markets(
    [36],
    lambda h: _decode_type_lshift160(
        h, {0: "UNDEFINED", 1: "GAUGE", 2: "POOL", 3: "TOKEN"}
    ),
)
# Velodrome Superchain Slipstream
_register_markets(
    [32],
    lambda h: _decode_type_lshift160(h, {0: "UNDEFINED", 1: "Gauge", 2: "Pool"}),
)
# Aave V4 (id 49 per IporFusionMarkets.sol; 44 is SPARK_LEND, which has no
# fuse of its own and stays undecoded rather than guessed)
_register_markets(
    [49],
    lambda h: _decode_type_lshift248(h, {0: "Undefined", 1: "Asset", 2: "Spoke"}),
)
# Odos
_register_markets(
    [42],
    lambda h: _decode_type_lshift248(h, {0: "Unknown", 1: "Token", 2: "Slippage"}),
)
# Velora
_register_markets(
    [43],
    lambda h: _decode_type_lshift248(h, {0: "Unknown", 1: "Token", 2: "Slippage"}),
)
# Universal Token Swapper
_register_markets(
    [12],
    lambda h: _decode_type_lshift248(
        h, {0: "Unknown", 1: "Token", 2: "Target", 3: "Slippage"}
    ),
)
# Enso
_register_markets([38], _decode_enso)
# Dolomite (id 47 per IporFusionMarkets.sol)
_register_markets([47], _decode_dolomite)
# Euler V2 (eulerVault<<96 | isCollateral<<88 | canBorrow<<80 | subAccounts<<72)
_register_markets([11], _decode_euler_v2)
# Async Action (typed: amount-to-outside / target+selector / exit slippage)
_register_markets([40], _decode_async_action)


def _build_market_lookup() -> dict[int, str]:
    lookup: dict[int, str] = {}
    for name in dir(IporFusionMarkets):
        if not name.startswith("_"):
            val = getattr(IporFusionMarkets, name)
            if isinstance(val, int):
                lookup[val] = name
    return lookup


_MARKET_LOOKUP: dict[int, str] = _build_market_lookup()


def market_name(market_id: int) -> str:
    return _MARKET_LOOKUP.get(market_id, "UNKNOWN")


_UINT256_MAX = 2**256 - 1


def format_market_label(market_id: int) -> str:
    """Render a market id as ``NAME (id)`` for display.

    Special-cases the ``uint256.max`` sentinel used by burn-fee fuses
    (ZERO_BALANCE_MARKET) — printing the full 78-digit number is noisy.
    """
    if market_id == _UINT256_MAX:
        name = market_name(market_id)
        return f"{name} (uint256.max)" if name != "UNKNOWN" else "uint256.max"
    label = market_name(market_id)
    return f"{label} ({market_id})" if label != "UNKNOWN" else str(market_id)


def decode_substrate(raw: bytes | str, market_id: int | None = None) -> SubstrateInfo:
    """Decode one bytes32 substrate in the context of ``market_id``.

    ``raw`` is the 32-byte value either as ``bytes`` or as a hex string
    (``0x``-prefixed or bare). Anything that is not exactly 32 bytes comes
    back as ``is_error=True``; a market without a registered decoder comes
    back raw with a ``no_decoder(NAME)`` label — never a guessed address.
    """
    if isinstance(raw, str):
        try:
            raw = bytes.fromhex(raw.removeprefix("0x"))
        except ValueError:
            return SubstrateInfo(raw_hex=raw, is_error=True)
    hex_str = raw.hex()
    if len(hex_str) != 64:
        return SubstrateInfo(raw_hex=f"0x{hex_str}", is_error=True)

    if market_id is not None:
        if decoder := _SUBSTRATE_DECODERS.get(market_id):
            return decoder(hex_str)
        # Known-length but no decoder — show raw with warning
        return SubstrateInfo(
            raw_hex=f"0x{hex_str}",
            type_label=f"no_decoder({market_name(market_id)})",
        )

    # No market context — show raw hex
    return SubstrateInfo(raw_hex=f"0x{hex_str}")

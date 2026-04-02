from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from ipor_fusion.market_ids import IporFusionMarkets


@dataclass
class _SubstrateInfo:
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


def _decode_type_lshift160(hex_str: str, types: dict[int, str]) -> _SubstrateInfo:
    """Decode type<<160 | address (Ebisu, Midas, Balancer, Velodrome)."""
    type_byte = int(hex_str[22:24], 16)
    addr = f"0x{hex_str[24:]}"
    label = types.get(type_byte, f"type={type_byte}")
    return _SubstrateInfo(address=addr, type_label=label)


def _decode_type_lshift248(hex_str: str, types: dict[int, str]) -> _SubstrateInfo:
    """Decode type<<248 | address_or_value (Odos, Velora, UTS, Aave V4)."""
    type_byte = int(hex_str[0:2], 16)
    if (label := types.get(type_byte, f"type={type_byte}")) == "Slippage":
        value = int(hex_str[2:], 16)
        return _SubstrateInfo(
            raw_hex=f"0x{hex_str}", type_label=label, extra={"value": str(value)}
        )
    addr = f"0x{hex_str[24:]}"
    return _SubstrateInfo(address=addr, type_label=label)


def _decode_plain_address(hex_str: str) -> _SubstrateInfo:
    """Decode zero-padded address: 12 zero bytes + 20 address bytes."""
    return _SubstrateInfo(address=f"0x{hex_str[24:]}")


def _decode_morpho(hex_str: str) -> _SubstrateInfo:
    """Raw bytes32 Morpho market ID — no structure."""
    return _SubstrateInfo(raw_hex=f"0x{hex_str}", type_label="morpho_market_id")


def _decode_enso(hex_str: str) -> _SubstrateInfo:
    """Decode address<<96 | selector<<64 (Enso)."""
    addr = f"0x{hex_str[0:40]}"
    selector = f"0x{hex_str[40:48]}"
    return _SubstrateInfo(address=addr, extra={"selector": selector})


def _decode_dolomite(hex_str: str) -> _SubstrateInfo:
    """Decode asset<<96 | subAccountId<<88 | canBorrow<<80 (Dolomite)."""
    addr = f"0x{hex_str[0:40]}"
    sub_account_id = int(hex_str[40:42], 16)
    can_borrow = (int(hex_str[42:44], 16) & 0x01) == 1
    return _SubstrateInfo(
        address=addr,
        extra={"sub_account_id": str(sub_account_id), "can_borrow": str(can_borrow)},
    )


# Market ID → decoder function.  Markets not listed here get raw hex output.
_SUBSTRATE_DECODERS: dict[int, Callable[[str], _SubstrateInfo]] = {}


def _register_markets(
    market_ids: list[int], decoder: Callable[[str], _SubstrateInfo]
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
        11,
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
        40,
        47,
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
# Aave V4
_register_markets(
    [44],
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
# Dolomite
_register_markets([46], _decode_dolomite)


def _build_market_lookup() -> dict[int, str]:
    lookup: dict[int, str] = {}
    for name in dir(IporFusionMarkets):
        if not name.startswith("_"):
            val = getattr(IporFusionMarkets, name)
            if isinstance(val, int):
                lookup[val] = name
    return lookup


_MARKET_LOOKUP: dict[int, str] = _build_market_lookup()


def _market_name(market_id: int) -> str:
    return _MARKET_LOOKUP.get(market_id, "UNKNOWN")


def _format_substrate(raw: bytes, market_id: int | None = None) -> _SubstrateInfo:
    hex_str = raw.hex()
    if len(hex_str) != 64:
        return _SubstrateInfo(raw_hex=f"0x{hex_str}", is_error=True)

    if market_id is not None:
        if decoder := _SUBSTRATE_DECODERS.get(market_id):
            return decoder(hex_str)
        # Known-length but no decoder — show raw with warning
        market_name = _market_name(market_id)
        return _SubstrateInfo(
            raw_hex=f"0x{hex_str}",
            type_label=f"no_decoder({market_name})",
        )

    # No market context — show raw hex
    return _SubstrateInfo(raw_hex=f"0x{hex_str}")

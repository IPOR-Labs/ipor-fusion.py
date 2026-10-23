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

from eth_typing import HexAddress

from ipor_fusion.fuses.hypercore import (
    is_hip3_asset,
    is_native_perp_asset,
    perp_dex_of,
    precompile_index_of,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import HyperCoreAsset, MarketId, parse_hex_address


@dataclass
class SubstrateInfo:
    address: HexAddress | None = None
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


def _address(hex_digits: str) -> HexAddress:
    """The address in 40 hex digits sliced out of a substrate word."""
    return parse_hex_address("0x" + hex_digits)


def _decode_type_lshift160(hex_str: str, types: dict[int, str]) -> SubstrateInfo:
    """Decode type<<160 | address (Ebisu, Midas, Balancer, Velodrome)."""
    type_byte = int(hex_str[22:24], 16)
    addr = _address(hex_str[24:])
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
    addr = _address(hex_str[24:])
    return SubstrateInfo(address=addr, type_label=label)


def _decode_plain_address(hex_str: str) -> SubstrateInfo:
    """Decode zero-padded address: 12 zero bytes + 20 address bytes."""
    return SubstrateInfo(address=_address(hex_str[24:]))


def _noncanonical(hex_str: str) -> SubstrateInfo:
    return SubstrateInfo(
        raw_hex=f"0x{hex_str}", type_label="noncanonical", is_error=True
    )


def _decode_strict_plain_address(hex_str: str) -> SubstrateInfo:
    """A substrate-as-asset address: ``isSubstrateAsAssetGranted`` grants only
    the zero-padded word, so dirty upper bits are no grant of the low address."""
    if hex_str[:24] != "0" * 24:
        return _noncanonical(hex_str)
    return _decode_plain_address(hex_str)


def _decode_strict_type_lshift160(hex_str: str, types: dict[int, str]) -> SubstrateInfo:
    """type<<160 | address where Solidity converts the whole ``word >> 160`` to
    the enum: any other upper bits, or a value outside it, reverts there."""
    if hex_str[:22] != "0" * 22 or int(hex_str[22:24], 16) not in types:
        return _noncanonical(hex_str)
    return _decode_type_lshift160(hex_str, types)


def _decode_term_finance(hex_str: str) -> SubstrateInfo:
    """TermFinanceSubstrateLib.sol: a SERVICER is the TermRepoServicer address
    in the plain zero-padded form (the whole upper 96 bits zero, as the fuses'
    strict ``isServicerSubstrate`` requires); a COLLATERAL_TOKEN is type byte
    0x01 over ``keccak256(abi.encode(servicer, collateralToken))`` truncated to
    248 bits, a pair key and not an address."""
    if hex_str[:24] == "0" * 24:
        return SubstrateInfo(address=_address(hex_str[24:]), type_label="SERVICER")
    if hex_str[:2] == "01":
        return SubstrateInfo(raw_hex=f"0x{hex_str}", type_label="COLLATERAL_TOKEN")
    return SubstrateInfo(
        raw_hex=f"0x{hex_str}",
        type_label=f"type={int(hex_str[:2], 16)}",
        is_error=True,
    )


def _decode_morpho(hex_str: str) -> SubstrateInfo:
    """Raw bytes32 Morpho market ID — no structure."""
    return SubstrateInfo(raw_hex=f"0x{hex_str}", type_label="morpho_market_id")


def _decode_uniswap_v4(hex_str: str) -> SubstrateInfo:
    """Uniswap V4 two-level whitelist (UniswapV4SubstrateLib.sol): a PoolId
    (raw bytes32, keccak256(abi.encode(PoolKey)) — carries the hook, fee tier
    and tick spacing, unrecoverable offline) or a pool currency granted as
    substrate-as-asset (12 zero bytes + address)."""
    if hex_str[:24] == "0" * 24:
        return SubstrateInfo(
            address=_address(hex_str[24:]), type_label="uniswap_v4_token"
        )
    return SubstrateInfo(raw_hex=f"0x{hex_str}", type_label="uniswap_v4_pool_id")


def _decode_enso(hex_str: str) -> SubstrateInfo:
    """Decode address<<96 | selector<<64 (Enso)."""
    addr = _address(hex_str[0:40])
    selector = f"0x{hex_str[40:48]}"
    return SubstrateInfo(
        address=addr, type_label="ENSO_TARGET", extra={"selector": selector}
    )


def _decode_dolomite(hex_str: str) -> SubstrateInfo:
    """Decode asset<<96 | subAccountId<<88 | canBorrow<<80 (Dolomite)."""
    addr = _address(hex_str[0:40])
    sub_account_id = int(hex_str[40:42], 16)
    can_borrow = (int(hex_str[42:44], 16) & 0x01) == 1
    return SubstrateInfo(
        address=addr,
        type_label="DOLOMITE_ASSET",
        extra={"sub_account_id": str(sub_account_id), "can_borrow": str(can_borrow)},
    )


def _decode_aave_v4(hex_str: str) -> SubstrateInfo:
    """Decode type<<248 | spoke<<88 | reserveId<<56 | flags<<48 (Aave V4).

    Source: AaveV4SubstrateLib.sol — type flag 1 = Reserve; the spoke address
    is left-aligned right after the type byte (bits 247..88), followed by a
    uint32 reserveId and a flags byte (bit0 isCollateral, bit1 canBorrow);
    bits 47..0 are reserved. The (spoke, reserveId) pair — not an asset
    address — identifies the market in Aave V4's Hub & Spoke design, so the
    generic type<<248 helper (address in the low 20 bytes) rendered these as
    garbage addresses.
    """
    type_byte = int(hex_str[0:2], 16)
    if type_byte != 1:
        label = "Undefined" if type_byte == 0 else f"type={type_byte}"
        return SubstrateInfo(raw_hex=f"0x{hex_str}", type_label=label)
    flags = int(hex_str[50:52], 16)
    return SubstrateInfo(
        address=_address(hex_str[2:42]),
        type_label="AAVE_V4_RESERVE",
        extra={
            "reserve_id": str(int(hex_str[42:50], 16)),
            "is_collateral": str((flags & 0x01) == 1),
            "can_borrow": str((flags & 0x02) == 2),
        },
    )


def _decode_euler_v2(hex_str: str) -> SubstrateInfo:
    """Decode eulerVault<<96 | isCollateral<<88 | canBorrow<<80 | subAccounts<<72.

    Source: EulerFuseLib.substrateToBytes32 — address occupies the high 20 bytes
    (left-aligned), followed by three 1-byte flags. Decoding via the generic
    plain-address path silently produces a malformed address (last 20 bytes are
    flag bytes + zero padding).
    """
    addr = _address(hex_str[0:40])
    is_collateral = (int(hex_str[40:42], 16) & 0x01) == 1
    can_borrow = (int(hex_str[42:44], 16) & 0x01) == 1
    sub_account = f"0x{hex_str[44:46]}"
    return SubstrateInfo(
        address=addr,
        type_label="EULER_VAULT",
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
            address=_address(hex_str[2:42]),
            type_label="ALLOWED_AMOUNT_TO_OUTSIDE",
            extra={"amount": str(int(hex_str[42:64], 16))},
        )
    if type_byte == 1:
        return SubstrateInfo(
            address=_address(hex_str[16:56]),
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


_EXTERNAL_STATE_ADDRESS_TYPES = {1: "ASSET", 3: "CUSTODIAN", 4: "BALANCE_ACCOUNT"}
# Scalar guards carry their unit in the extra key, since the four types share
# one payload shape but not one unit.
_EXTERNAL_STATE_SCALAR_TYPES = {
    5: ("STALENESS_MAX", "seconds"),
    6: ("BIG_CHANGE_BPS", "bps"),
    7: ("DUST_THRESHOLD", "percent"),
    8: ("MIN_UPDATE_INTERVAL", "seconds"),
}


def _decode_external_state(hex_str: str) -> SubstrateInfo:
    """Decode ExternalState typed substrates.

    Source: ExternalStateSubstrateLib.sol — layout [substrateType 1 byte |
    payload 31 bytes]:
    - ASSET (1), CUSTODIAN (3), BALANCE_ACCOUNT (4): payload = address in the
      low 20 bytes.
    - TARGET (2): payload = selector<<160 | target, so the 4-byte selector sits
      above the address (the 7 high payload bytes are zero). Note this is the
      mirror image of the async-action TARGET layout, where the selector is
      below the address.
    - STALENESS_MAX (5), BIG_CHANGE_BPS (6), DUST_THRESHOLD (7),
      MIN_UPDATE_INTERVAL (8): payload = uint248 scalar. The extra key names
      the unit -- ``seconds``, ``bps`` or ``percent`` (of one token) -- since
      the four types share a payload shape but not a unit.
    - UNDEFINED (0), the enum's invalid member, and any tag above 8 stay raw.
    """
    type_byte = int(hex_str[0:2], 16)
    if type_byte == 0:
        return SubstrateInfo(raw_hex=f"0x{hex_str}", type_label="UNDEFINED")
    if label := _EXTERNAL_STATE_ADDRESS_TYPES.get(type_byte):
        return SubstrateInfo(address=_address(hex_str[24:]), type_label=label)
    if type_byte == 2:
        return SubstrateInfo(
            address=_address(hex_str[24:]),
            type_label="TARGET",
            extra={"selector": f"0x{hex_str[16:24]}"},
        )
    if scalar := _EXTERNAL_STATE_SCALAR_TYPES.get(type_byte):
        label, unit = scalar
        return SubstrateInfo(
            raw_hex=f"0x{hex_str}",
            type_label=label,
            extra={unit: str(int(hex_str[2:], 16))},
        )
    return SubstrateInfo(raw_hex=f"0x{hex_str}", type_label=f"type={type_byte}")


_CROSSCHAIN_TYPES = {0: "UNDEFINED", 1: "EXECUTOR", 2: "REMOTE_VAULT"}


def _decode_crosschain(hex_str: str) -> SubstrateInfo:
    """Decode type<<248 | chainId<<160 | address (Crosschain market).

    Source: CrosschainSubstrateLib.sol -- EXECUTOR (1) is an executor on the
    vault's own chain (chain id slot zero); REMOTE_VAULT (2) is a remote
    PlasmaVault bound to the 88-bit destination chain id in bits 247..160, so
    the same vault address granted for another chain is a different grant.
    Non-canonical chain slots decode as errors because no fuse can match them.
    """
    type_byte = int(hex_str[0:2], 16)
    label = _CROSSCHAIN_TYPES.get(type_byte, f"type={type_byte}")
    if type_byte not in (1, 2):
        return SubstrateInfo(raw_hex=f"0x{hex_str}", type_label=label)
    chain_id = int(hex_str[2:24], 16)
    if (type_byte == 1 and chain_id != 0) or (type_byte == 2 and chain_id == 0):
        return SubstrateInfo(
            raw_hex=f"0x{hex_str}",
            type_label=label,
            is_error=True,
            extra={"chain_id": str(chain_id)},
        )
    info = SubstrateInfo(address=_address(hex_str[24:]), type_label=label)
    if type_byte == 2:
        info.extra = {"chain_id": str(chain_id)}
    return info


_HYPERCORE_TYPES = {
    1: "SPOT_TOKEN",
    2: "PERP_MARKET",
    3: "DESTINATION",
    4: "BUILDER",
    5: "CONFIG",
    6: "SEND_CAP",
    7: "PERP_POSITION_CAP",
}
# HyperCoreSubstrateLib.ConfigKey member names, by enum value.
_HYPERCORE_CONFIG_KEYS = {
    1: "WindowTransferSeconds",
    2: "WindowOrderSeconds",
    3: "MaxUsdClassTransferUsd6",
    4: "PerpDexIds",
    5: "SettlementMode",
    6: "SpotSendBridgeEnabled",
    7: "TransferTimeoutSeconds",
    8: "ActivationFeeAllowanceUsdcWei",
    9: "MaxNavJumpBps",
    10: "MaxMarkOracleDeviationBps",
    11: "NavJumpFloorUsd6",
}
# Keys the library keeps for their stable numbering; the fuses ignore them.
_HYPERCORE_RESERVED_CONFIG_KEYS = frozenset({2, 5})


def _decode_hypercore(hex_str: str) -> SubstrateInfo:
    """Decode HyperCore typed substrates.

    Source: HyperCoreSubstrateLib.sol -- ``bytes32(uint256(type) << 248 | data)``,
    a one-byte type tag then a 31-byte (248-bit) payload:
    - SpotToken (1): ``tokenIndex << 160 | evmAsset``.
    - PerpMarket (2): ``asset << 216 | maxNotionalUsd6 << 128 |
      reduceOnlyRequired << 120``; ``asset`` is the CoreWriter action asset
      (``100000 + dex * 10000 + index`` for a HIP-3 market), the cap is USD
      with 6 decimals, the low 120 bits are zero.
    - Destination (3): address in the low 20 bytes.
    - Builder (4): ``maxFeeRateDecibps << 160 | builder``.
    - Config (5): ``key << 240 | uint240 value``. Keys 1..11 are
      WindowTransferSeconds, WindowOrderSeconds (reserved, ignored),
      MaxUsdClassTransferUsd6, PerpDexIds (bitmap: bit i enables perp dex i),
      SettlementMode (reserved, ignored), SpotSendBridgeEnabled,
      TransferTimeoutSeconds, ActivationFeeAllowanceUsdcWei, MaxNavJumpBps,
      MaxMarkOracleDeviationBps and NavJumpFloorUsd6.
    - SendCap (6): ``tokenIndex << 128 | maxWei`` (Core wei).
    - PerpPositionCap (7): ``asset << 216 | maxPositionNotionalUsd6 << 128``,
      the low 128 bits zero.
    Words the library's decoders revert on come back raw with ``is_error``
    (a tag outside 1..7, an unknown config key, reserved bits set, a
    reduce-only flag other than 0/1): the fuses would reject them, so no
    field of theirs may look granted.
    """
    type_byte = int(hex_str[0:2], 16)
    label = _HYPERCORE_TYPES.get(type_byte)
    if label is None:
        return _hypercore_invalid(hex_str, f"type={type_byte}")
    if type_byte == 5:
        return _decode_hypercore_config(hex_str)
    start, end = _HYPERCORE_RESERVED_SPANS[type_byte]
    if hex_str[start:end].strip("0"):
        return _hypercore_invalid(hex_str, label)
    return _HYPERCORE_FIELD_DECODERS[type_byte](hex_str, label)


def _hypercore_spot_token(hex_str: str, label: str) -> SubstrateInfo:
    return SubstrateInfo(
        address=_address(hex_str[24:]),
        type_label=label,
        extra={"token_index": str(int(hex_str[8:24], 16))},
    )


def _hypercore_perp_market(hex_str: str, label: str) -> SubstrateInfo:
    reduce_flag = int(hex_str[32:34], 16)
    if reduce_flag > 1:
        return _hypercore_invalid(hex_str, label)
    asset = HyperCoreAsset(int(hex_str[2:10], 16))
    extra = {
        "asset": str(asset),
        "max_notional_usd6": str(int(hex_str[10:32], 16)),
        "reduce_only_required": str(reduce_flag == 1).lower(),
    }
    # The HIP-3 coordinates the read precompiles want; absent for an asset
    # outside the native and HIP-3 families, which the perp fuses reject.
    if is_native_perp_asset(asset) or is_hip3_asset(asset):
        extra["dex"] = str(perp_dex_of(asset))
        extra["read_index"] = str(precompile_index_of(asset))
    return SubstrateInfo(raw_hex=f"0x{hex_str}", type_label=label, extra=extra)


def _hypercore_destination(hex_str: str, label: str) -> SubstrateInfo:
    return SubstrateInfo(address=_address(hex_str[24:]), type_label=label)


def _hypercore_builder(hex_str: str, label: str) -> SubstrateInfo:
    return SubstrateInfo(
        address=_address(hex_str[24:]),
        type_label=label,
        extra={"max_fee_rate_decibps": str(int(hex_str[8:24], 16))},
    )


def _hypercore_send_cap(hex_str: str, label: str) -> SubstrateInfo:
    return SubstrateInfo(
        raw_hex=f"0x{hex_str}",
        type_label=label,
        extra={
            "token_index": str(int(hex_str[16:32], 16)),
            "max_wei": str(int(hex_str[32:], 16)),
        },
    )


def _hypercore_perp_position_cap(hex_str: str, label: str) -> SubstrateInfo:
    return SubstrateInfo(
        raw_hex=f"0x{hex_str}",
        type_label=label,
        extra={
            "asset": str(int(hex_str[2:10], 16)),
            "max_position_notional_usd6": str(int(hex_str[10:32], 16)),
        },
    )


# Reserved bits the library requires to be zero, as hex spans of the word.
_HYPERCORE_RESERVED_SPANS = {
    1: (2, 8),
    2: (34, 64),
    3: (2, 24),
    4: (2, 8),
    6: (2, 16),
    7: (32, 64),
}
_HYPERCORE_FIELD_DECODERS: dict[int, Callable[[str, str], SubstrateInfo]] = {
    1: _hypercore_spot_token,
    2: _hypercore_perp_market,
    3: _hypercore_destination,
    4: _hypercore_builder,
    6: _hypercore_send_cap,
    7: _hypercore_perp_position_cap,
}


def _decode_hypercore_config(hex_str: str) -> SubstrateInfo:
    key = int(hex_str[2:4], 16)
    value = int(hex_str[4:], 16)
    name = _HYPERCORE_CONFIG_KEYS.get(key)
    if name is None:
        return _hypercore_invalid(hex_str, f"CONFIG key={key}")
    extra = {"key": name, "value": str(value)}
    if key in _HYPERCORE_RESERVED_CONFIG_KEYS:
        extra["reserved"] = "true"
    return SubstrateInfo(raw_hex=f"0x{hex_str}", type_label="CONFIG", extra=extra)


def _hypercore_invalid(hex_str: str, label: str) -> SubstrateInfo:
    """A word HyperCoreSubstrateLib would revert on: raw, flagged, no fields."""
    return SubstrateInfo(
        raw_hex=f"0x{hex_str}",
        type_label=label,
        is_error=True,
        extra={"error": "invalid HyperCore substrate"},
    )


# Market ID → decoder function.  Markets not listed here get raw hex output.
_SUBSTRATE_DECODERS: dict[MarketId, Callable[[str], SubstrateInfo]] = {}


def _register_markets(
    market_ids: list[int], decoder: Callable[[str], SubstrateInfo]
) -> None:
    for mid in market_ids:
        _SUBSTRATE_DECODERS[MarketId(mid)] = decoder


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
        19,  # MORPHO_FLASH_LOAN — flash-loanable tokens (isSubstrateAsAssetGranted)
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
        44,  # SPARK_LEND — reuses AaveV3SupplyFuse (isSubstrateAsAssetGranted)
        46,  # NAPIER — isSubstrateAsAssetGranted, plain asset addresses
        *range(100_001, 100_021),  # ERC4626_0001 .. ERC4626_0020
    ],
    _decode_plain_address,
)
# Substrate-as-asset markets registered from their grant checks, decoded
# strictly (a word with dirty upper bits grants nothing):
# META_MORPHO_0001 is deployed as Erc4626SupplyFuse (the MetaMorpho vault),
# MERKL as MerklClaimWrapperFuse (the reward tokens it forwards) and
# ASSETS_BALANCE_VALIDATION as PlasmaVaultBalanceAssetsValidationFuse.
_register_markets(
    [
        200_001,
        IporFusionMarkets.MERKL,
        IporFusionMarkets.ASSETS_BALANCE_VALIDATION,
    ],
    _decode_strict_plain_address,
)
# Morpho markets — raw bytes32 market ids. MORPHO_FLASH_LOAN (19) is NOT one:
# MorphoFlashLoanFuse gates the loan token via isSubstrateAsAssetGranted, and
# every live market-19 substrate is the zero-padded address form (registry
# census 2026-09-03: 111 of 111 rows on Ethereum, Base and Arbitrum)
_register_markets([14, 22, 41], _decode_morpho)
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
# Velodrome Superchain (VelodromeSuperchainLib.sol): the same type<<160 Gauge /
# Pool layout, decoded strictly
_register_markets(
    [31],
    lambda h: _decode_strict_type_lshift160(h, {0: "UNDEFINED", 1: "Gauge", 2: "Pool"}),
)
# Agua Global Carry (AguaSubstrateLib.sol): type<<160 | address
_register_markets(
    [51],
    lambda h: _decode_strict_type_lshift160(
        h, {0: "UNDEFINED", 1: "VAULT", 2: "ASSET"}
    ),
)
# Term Finance (TermFinanceSubstrateLib.sol): SERVICER / COLLATERAL_TOKEN
_register_markets([52], _decode_term_finance)
# Aave V4 (id 49 per IporFusionMarkets.sol) — typed Reserve substrates per
# AaveV4SubstrateLib.sol; an earlier draft enum (Asset/Spoke, address in the
# low bytes) shipped here and decoded live substrates to garbage addresses
_register_markets([49], _decode_aave_v4)
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
# Universal Token Swapper — UniversalTokenSwapperSubstrateLib.sol, shared by
# UNIVERSAL_TOKEN_SWAPPER (12) and UNIVERSAL_TOKEN_SWAPPER_V2 (1202): the V2
# market granted exactly this layout live (weETH Enhanced Yield, Ethereum
# 0x2775e695…, 10 rows) and decoded as no_decoder while only 12 was registered
_register_markets(
    [12, 1202],
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
# External State (typed: asset / target+selector / custodian / balance account
# / four uint248 guards).
_register_markets([50], _decode_external_state)
# Uniswap V4 (id 53 per IporFusionMarkets.sol): PoolId or pool currency.
_register_markets([53], _decode_uniswap_v4)
# Crosschain (54): EXECUTOR / REMOTE_VAULT typed substrates per
# CrosschainSubstrateLib.sol.
_register_markets([54], _decode_crosschain)
# HyperCore (55): typed substrates per HyperCoreSubstrateLib.sol, verified
# against the 12 words granted on the first HyperEVM test vault (see
# tests/fixtures/hypercore_hip3_flow.json; that run used id 54 before the
# renumbering). Numeric 54 is the crosschain market, registered above.
_register_markets([55], _decode_hypercore)


# Markets left without a decoder on purpose, each with the reason; they decode
# as no_decoder(NAME). tests/test_substrates.py holds every IporFusionMarkets
# member to a decoder or an entry here, so a new market cannot go unnoticed.
_UNREGISTERED_ON_PURPOSE: dict[MarketId, str] = {
    IporFusionMarkets.LITE_PSM: "IporFusionMarkets.sol declares no substrates "
    "(a ZeroBalanceFuse market)",
    IporFusionMarkets.SPOL_UNSTAKE: "SPOLUnstakeFuse is not in the public contracts "
    "repository, so its grant check cannot be verified",
    **{
        MarketId(mid): "no deployment or layout verified in the address registry; "
        "META_MORPHO_0001 is an Erc4626SupplyFuse, but a later one is not assumed"
        for mid in range(200_002, 200_011)
    },
    IporFusionMarkets.EXCHANGE_RATE_VALIDATOR: "packed hook and validator words "
    "(ExchangeRateValidatorConfigLib), not addresses; not mirrored here yet",
    IporFusionMarkets.ZERO_BALANCE_MARKET: "a capability market: no fuse defines a "
    "substrate layout for it",
}


def _build_market_lookup() -> dict[int, str]:
    lookup: dict[int, str] = {}
    for name in dir(IporFusionMarkets):
        if name.startswith("_"):
            continue
        val = getattr(IporFusionMarkets, name)
        if isinstance(val, int):
            lookup[val] = name
    return lookup


_MARKET_LOOKUP: dict[int, str] = _build_market_lookup()


def market_name(market_id: MarketId) -> str:
    return _MARKET_LOOKUP.get(market_id, "UNKNOWN")


_UINT256_MAX = 2**256 - 1


def format_market_label(market_id: MarketId) -> str:
    """Render a market id as ``NAME (id)`` for display.

    Special-cases the ``uint256.max`` sentinel used by burn-fee fuses
    (ZERO_BALANCE_MARKET) — printing the full 78-digit number is noisy.
    """
    if market_id == _UINT256_MAX:
        name = market_name(market_id)
        return f"{name} (uint256.max)" if name != "UNKNOWN" else "uint256.max"
    label = market_name(market_id)
    return f"{label} ({market_id})" if label != "UNKNOWN" else str(market_id)


def decode_substrate(
    raw: bytes | str, market_id: MarketId | None = None
) -> SubstrateInfo:
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

"""HyperCore (market 55) read side.

Three reads a keeper or an operator needs on a HyperCore vault and no fuse
exposes:

- the HyperCore read precompiles (``HyperCoreLib`` calls them from the
  fuses): plain :class:`Call`s against the same fixed addresses with raw
  ABI-encoded input and no selector, so they batch through
  :class:`~ipor_fusion.core.multicall.Multicall3` like any other read;
- the vault's pending-action state through ``HyperCorePendingReader``, a
  stateless helper the vault delegatecalls through ``UniversalReader.read``
  because the ERC-7201 slot lives in the vault's own storage;
- the NAV identity ``HyperCoreValuationLib.liveValueWad`` computes inside
  ``HyperCoreBalanceFuse.balanceOf``: the granted spot tokens at the oracle
  price plus the signed ``accountValue`` of the native perp dex and of every
  HIP-3 dex the ``PerpDexIds`` config enables.

Every read runs at ``ctx.default_block``; pin it to compare
:func:`read_hypercore_nav` with :meth:`PlasmaVault.balance_fuse_value` at one
block. Precompile reads need a live HyperEVM node: they fail inside
``eth_simulateV1`` and have no historical Core state.

Status: preview, not production. The contracts behind it are under active development, the mainnet deployments are a proof of concept and IPOR Labs canaries, and interfaces may change between minor versions; see ``ipor_fusion.about.PREVIEW_FEATURES``.
"""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from enum import IntEnum
from typing import Any, TypeVar, cast

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.core.oracle import PriceOracleMiddleware
from ipor_fusion.core.plasma_vault import PlasmaVault
from ipor_fusion.fuses.hypercore import (
    NATIVE_PERP_DEX,
    SUPPORTED_HIP3_DEXES,
    SettlementMode,
    perp_dex_of,
    read_index_of,
)
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.substrates import decode_substrate
from ipor_fusion.types import MarketId, Price

T = TypeVar("T")

#: New HyperCore vaults use market 55; the abandoned pilot still keys 54.
HYPERCORE_MARKET = MarketId(IporFusionMarkets.HYPERCORE)
#: The only chain with the HyperCore precompiles.
HYPEREVM_CHAIN_ID = 999
#: Known ``HyperCorePendingReader`` deployments per chain. The reader is a
#: stateless helper and any deployment serves any vault. The market-55 reader
#: is pinned in ``tests/fixtures/hypercore_market55.json`` until ``ipor-abi``
#: publishes it.
HYPERCORE_PENDING_READERS: dict[int, ChecksumAddress] = {
    HYPEREVM_CHAIN_ID: Web3.to_checksum_address(
        "0x83F54bc9eA4Dd19cb579Daa70368562C7a7534C4"
    ),
}
WAD_DECIMALS = 18
#: ``accountMarginSummary`` reports USD with 6 decimals.
USD6_DECIMALS = 6
_INT256_MAX = (1 << 255) - 1


def _precompile(index: int) -> ChecksumAddress:
    return Web3.to_checksum_address(f"0x{index:040x}")


SPOT_BALANCE_PRECOMPILE = _precompile(0x801)
WITHDRAWABLE_PRECOMPILE = _precompile(0x803)
MARK_PX_PRECOMPILE = _precompile(0x806)
ORACLE_PX_PRECOMPILE = _precompile(0x807)
L1_BLOCK_NUMBER_PRECOMPILE = _precompile(0x809)
PERP_ASSET_INFO_PRECOMPILE = _precompile(0x80A)
TOKEN_INFO_PRECOMPILE = _precompile(0x80C)
ACCOUNT_MARGIN_SUMMARY_PRECOMPILE = _precompile(0x80F)
CORE_USER_EXISTS_PRECOMPILE = _precompile(0x810)
#: ``position2``: the position read that addresses HIP-3 read indexes above
#: 65535; the legacy ``0x800`` cannot, so the fuses never use it.
POSITION_PRECOMPILE = _precompile(0x813)

#: The order ``HyperCoreValuationLib`` sums the enabled HIP-3 dexes in.
HIP3_VALUATION_ORDER = (1, 6, 8, 9, 10)


def _validate_uint(value: int, bits: int, name: str) -> None:
    if not 0 <= value < (1 << bits):
        raise ValueError(f"{name} must fit in uint{bits}, got {value}")


@dataclass(frozen=True, slots=True)
class HyperCoreSpotBalance:
    """``spotBalance`` precompile result, all in Core wei of the token."""

    total: int
    hold: int
    entry_ntl: int


@dataclass(frozen=True, slots=True)
class HyperCoreAccountMarginSummary:
    """``accountMarginSummary`` precompile result, USD with 6 decimals;
    ``account_value`` and ``raw_usd`` are signed."""

    account_value: int
    margin_used: int
    ntl_pos: int
    raw_usd: int


@dataclass(frozen=True, slots=True)
class HyperCoreTokenInfo:
    """``tokenInfo`` precompile result."""

    name: str
    spots: tuple[int, ...]
    deployer_trading_fee_share: int
    deployer: ChecksumAddress
    evm_contract: ChecksumAddress
    sz_decimals: int
    wei_decimals: int
    evm_extra_wei_decimals: int


@dataclass(frozen=True, slots=True)
class HyperCorePerpAssetInfo:
    """``perpAssetInfo`` precompile result."""

    coin: str
    margin_table_id: int
    sz_decimals: int
    max_leverage: int
    only_isolated: bool


@dataclass(frozen=True, slots=True)
class HyperCorePosition:
    """``position`` precompile result; ``szi`` and ``isolated_raw_usd`` are
    signed."""

    szi: int
    entry_ntl: int
    isolated_raw_usd: int
    leverage: int
    is_isolated: bool


def _spot_balance(values: tuple) -> HyperCoreSpotBalance:
    return HyperCoreSpotBalance(*values)


def _margin_summary(values: tuple) -> HyperCoreAccountMarginSummary:
    return HyperCoreAccountMarginSummary(*values)


def _token_info(values: tuple) -> HyperCoreTokenInfo:
    name, spots, fee_share, deployer, evm_contract, sz, wei, extra = values
    return HyperCoreTokenInfo(
        name=name,
        spots=tuple(spots),
        deployer_trading_fee_share=fee_share,
        deployer=Web3.to_checksum_address(deployer),
        evm_contract=Web3.to_checksum_address(evm_contract),
        sz_decimals=sz,
        wei_decimals=wei,
        evm_extra_wei_decimals=extra,
    )


def _perp_asset_info(values: tuple) -> HyperCorePerpAssetInfo:
    return HyperCorePerpAssetInfo(*values)


def _position(values: tuple) -> HyperCorePosition:
    return HyperCorePosition(*values)


class HyperCoreReader:
    """The HyperCore read precompiles as :class:`Call`s.

    Mirrors ``HyperCoreLib``'s reads: a precompile takes its arguments as raw
    ABI-encoded words (no selector) and answers one ABI-encoded value. The
    perp reads take the *read index* of a market
    (:func:`ipor_fusion.read_index_of`: the action asset without the HIP-3
    offset), never the action asset itself.
    """

    def __init__(self, ctx: Web3Context | None = None):
        self._ctx = ctx

    def _read(
        self,
        precompile: ChecksumAddress,
        input_types: Sequence[str],
        values: Sequence[Any],
        output_types: list[str],
        decoder: Callable[..., T] | None = None,
    ) -> Call[T]:
        return Call(
            to=precompile,
            data=encode(list(input_types), list(values)) if input_types else b"",
            output_types=output_types,
            decoder=decoder,
            ctx=self._ctx,
        )

    def core_user_exists(self, user: ChecksumAddress) -> Call[bool]:
        """Whether ``user`` has a Core account (a vault gets one with its
        first EVM -> Core deposit; NAV is 0 before that)."""
        return self._read(
            CORE_USER_EXISTS_PRECOMPILE, ["address"], [user], ["bool"], bool
        )

    def spot_balance(
        self, user: ChecksumAddress, token_index: int
    ) -> Call[HyperCoreSpotBalance]:
        _validate_uint(token_index, 64, "token_index")
        return self._read(
            SPOT_BALANCE_PRECOMPILE,
            ["address", "uint64"],
            [user, token_index],
            ["uint64", "uint64", "uint64"],
            _spot_balance,
        )

    def account_margin_summary(
        self, dex: int, user: ChecksumAddress
    ) -> Call[HyperCoreAccountMarginSummary]:
        """Equity of ``user`` on perp ``dex`` (0 native, otherwise a HIP-3 dex
        index): margin, PnL, funding and fees already netted."""
        _validate_uint(dex, 32, "dex")
        return self._read(
            ACCOUNT_MARGIN_SUMMARY_PRECOMPILE,
            ["uint32", "address"],
            [dex, user],
            ["int64", "uint64", "uint64", "int64"],
            _margin_summary,
        )

    def token_info(self, token_index: int) -> Call[HyperCoreTokenInfo]:
        _validate_uint(token_index, 64, "token_index")
        return self._read(
            TOKEN_INFO_PRECOMPILE,
            ["uint64"],
            [token_index],
            ["(string,uint64[],uint64,address,address,uint8,uint8,int8)"],
            _token_info,
        )

    def perp_asset_info(self, read_index: int) -> Call[HyperCorePerpAssetInfo]:
        _validate_uint(read_index, 32, "read_index")
        return self._read(
            PERP_ASSET_INFO_PRECOMPILE,
            ["uint32"],
            [read_index],
            ["(string,uint32,uint8,uint8,bool)"],
            _perp_asset_info,
        )

    def position(
        self, user: ChecksumAddress, read_index: int
    ) -> Call[HyperCorePosition]:
        _validate_uint(read_index, 32, "read_index")
        return self._read(
            POSITION_PRECOMPILE,
            ["address", "uint32"],
            [user, read_index],
            ["int64", "uint64", "int64", "uint32", "bool"],
            _position,
        )

    def withdrawable(self, user: ChecksumAddress) -> Call[int]:
        """USD (6 decimals) ``user`` could withdraw from the native perp dex."""
        return self._read(WITHDRAWABLE_PRECOMPILE, ["address"], [user], ["uint64"])

    def oracle_px(self, read_index: int) -> Call[int]:
        _validate_uint(read_index, 32, "read_index")
        return self._read(ORACLE_PX_PRECOMPILE, ["uint32"], [read_index], ["uint64"])

    def mark_px(self, read_index: int) -> Call[int]:
        _validate_uint(read_index, 32, "read_index")
        return self._read(MARK_PX_PRECOMPILE, ["uint32"], [read_index], ["uint64"])

    def l1_block_number(self) -> Call[int]:
        return self._read(L1_BLOCK_NUMBER_PRECOMPILE, [], [], ["uint64"])


class HyperCoreActionClass(IntEnum):
    """``HyperCorePendingLib.ActionClass``."""

    NONE = 0
    TRANSFER = 1
    ORDER = 2


class HyperCoreReportedResult(IntEnum):
    """``HyperCorePendingLib`` reported result of a REPORTED-mode order."""

    NONE = 0
    EXECUTED = 1
    REJECTED = 2


@dataclass(frozen=True, slots=True)
class HyperCorePendingState:
    """``HyperCorePendingReader.pendingState()``: the raw ``PendingAction``
    struct and the predicates derived from it.

    Only ``pending`` and ``settled`` are authoritative: a TIMING-settled action
    leaves the raw fields in place until the next enqueue, so the struct alone
    cannot tell "pending" from "settled, not yet cleared".
    """

    pending_until: int
    enqueued_l1_block: int
    enqueued_evm_block: int
    action_class: HyperCoreActionClass
    settlement_mode: SettlementMode | None
    reported_result: HyperCoreReportedResult
    result_l1_block: int
    action_id: int
    payload_hash: bytes
    refreshing: bool
    cached_value_wad: int
    action_nonce: int
    pending: bool
    settled: bool
    current_l1_block: int
    current_timestamp: int
    l1_block_available: bool


_PENDING_ACTION_TYPE = "(uint64,uint64,uint64,uint8,uint8,uint8,uint64,uint24,bytes32,bool,uint256,uint256)"
PENDING_STATE_TYPE = f"({_PENDING_ACTION_TYPE},bool,bool,bool,uint64,uint64,bool)"
_PENDING_STATE_SELECTOR = function_signature_to_4byte_selector("pendingState()")
_IS_PENDING_SELECTOR = function_signature_to_4byte_selector("isPending()")


def _decode_pending_state(values: tuple) -> HyperCorePendingState:
    state, pending, settled, refreshing, l1_block, timestamp, l1_available = values
    (
        pending_until,
        enqueued_l1_block,
        enqueued_evm_block,
        action_class,
        settlement_mode,
        reported_result,
        result_l1_block,
        action_id,
        payload_hash,
        state_refreshing,
        cached_value_wad,
        action_nonce,
    ) = state
    return HyperCorePendingState(
        pending_until=pending_until,
        enqueued_l1_block=enqueued_l1_block,
        enqueued_evm_block=enqueued_evm_block,
        action_class=HyperCoreActionClass(action_class),
        settlement_mode=SettlementMode(settlement_mode) if settlement_mode else None,
        reported_result=HyperCoreReportedResult(reported_result),
        result_l1_block=result_l1_block,
        action_id=action_id,
        payload_hash=bytes(payload_hash),
        refreshing=bool(refreshing or state_refreshing),
        cached_value_wad=cached_value_wad,
        action_nonce=action_nonce,
        pending=bool(pending),
        settled=bool(settled),
        current_l1_block=l1_block,
        current_timestamp=timestamp,
        l1_block_available=bool(l1_available),
    )


class HyperCorePendingReader:
    """``HyperCorePendingReader`` through the vault's ``UniversalReader``.

    ``reader`` is the deployed helper (one per chain, never called directly);
    the vault delegatecalls it so it sees the vault's pending slot. Keepers
    gate every action on :meth:`is_pending`: the pending-action pre-hook
    reverts ``execute`` and ``updateMarketsBalances`` while it is true.
    """

    def __init__(self, vault: PlasmaVault, reader: ChecksumAddress):
        self._vault = vault
        self._reader = reader

    def pending_state(self) -> Call[HyperCorePendingState]:
        return self._vault.read_as(
            self._reader,
            _PENDING_STATE_SELECTOR,
            output_types=[PENDING_STATE_TYPE],
            decoder=_decode_pending_state,
        )

    def is_pending(self) -> Call[bool]:
        return self._vault.read_as(
            self._reader, _IS_PENDING_SELECTOR, output_types=["bool"], decoder=bool
        )


def convert_to_wad_int(value: int, decimals: int) -> int:
    """``IporMath.convertToWadInt``: rescale a signed ``decimals``-decimal
    integer to 18 decimals, rounding a division half up (ties toward
    +infinity: 2.5 -> 3, -2.5 -> -2), as the contract does."""
    if value == 0 or decimals == WAD_DECIMALS:
        return value
    if decimals < WAD_DECIMALS:
        return value * 10 ** (WAD_DECIMALS - decimals)
    divisor = 10 ** (decimals - WAD_DECIMALS)
    quotient, remainder = divmod(abs(value), divisor)
    if value < 0:
        return -(quotient + (2 * remainder > divisor))
    return quotient + (2 * remainder >= divisor)


@dataclass(frozen=True, slots=True)
class HyperCoreSpotLeg:
    """One granted spot token in the NAV: ``total`` Core wei priced through
    the substrate's EVM asset; ``value_wad`` is 0 when ``total`` is."""

    token_index: int
    evm_asset: ChecksumAddress
    total: int
    hold: int
    wei_decimals: int | None
    price: Price | None
    value_wad: int


@dataclass(frozen=True, slots=True)
class HyperCorePerpLeg:
    """One perp dex in the NAV: its signed ``accountValue`` (USD, 6 decimals)
    rescaled to wad."""

    dex: int
    account_value: int
    value_wad: int


@dataclass(frozen=True, slots=True)
class HyperCoreNav:
    """``HyperCoreValuationLib.liveValueWad`` reproduced leg by leg.

    ``value_wad`` is the signed sum the library computes; the balance fuse
    reverts (``HyperCoreNegativeBalance``) when it is negative, so
    :attr:`balance_wad` raises in that case.
    """

    core_user_exists: bool
    perp_dex_bitmap: int
    spot: tuple[HyperCoreSpotLeg, ...]
    native_perp: HyperCorePerpLeg | None
    hip3: tuple[HyperCorePerpLeg, ...]
    value_wad: int

    @property
    def balance_wad(self) -> int:
        """What ``HyperCoreBalanceFuse.balanceOf()`` returns."""
        if self.value_wad < 0:
            raise ValueError(f"HyperCore NAV is negative: {self.value_wad}")
        return self.value_wad


def _supported_hip3_mask() -> int:
    mask = 0
    for dex in SUPPORTED_HIP3_DEXES:
        mask |= 1 << dex
    return mask


def _market_layout(
    substrates: Sequence[bytes],
) -> tuple[list[tuple[int, ChecksumAddress]], int]:
    """The spot tokens (first grant per token index wins, as in
    ``getSpotTokens``) and the validated ``PerpDexIds`` bitmap. Always the
    HyperCore layout, whatever id the vault keys the market under."""
    tokens: list[tuple[int, ChecksumAddress]] = []
    seen: set[int] = set()
    bitmap = 0
    for raw in substrates:
        info = decode_substrate(raw, HYPERCORE_MARKET)
        if info.type_label == "SPOT_TOKEN" and not info.is_error:
            index = int(info.extra["token_index"])
            if index in seen:
                continue
            seen.add(index)
            tokens.append((index, Web3.to_checksum_address(info.address)))
        elif info.type_label == "CONFIG" and info.extra.get("key") == "PerpDexIds":
            bitmap = int(info.extra["value"])
    if bitmap & ~_supported_hip3_mask():
        raise ValueError(f"unsupported HIP-3 perp dex bitmap {bitmap:#x}")
    return tokens, bitmap


def _spot_leg(
    token_index: int,
    evm_asset: ChecksumAddress,
    balance: HyperCoreSpotBalance | None,
    token: HyperCoreTokenInfo | None,
    price: Price | None,
) -> HyperCoreSpotLeg:
    if balance is None:
        raise ValueError(f"spotBalance precompile failed for token {token_index}")
    if balance.total == 0:
        return HyperCoreSpotLeg(
            token_index,
            evm_asset,
            0,
            balance.hold,
            token.wei_decimals if token else None,
            price,
            0,
        )
    if token is None:
        raise ValueError(f"tokenInfo precompile failed for token {token_index}")
    if price is None or price.amount == 0:
        raise ValueError(f"the oracle prices no {evm_asset} (token {token_index})")
    if price.amount > _INT256_MAX // balance.total:
        raise ValueError(f"price overflow for token {token_index}")
    value = convert_to_wad_int(
        balance.total * price.amount, token.wei_decimals + price.decimals
    )
    return HyperCoreSpotLeg(
        token_index,
        evm_asset,
        balance.total,
        balance.hold,
        token.wei_decimals,
        price,
        value,
    )


def _perp_leg(
    dex: int, summary: HyperCoreAccountMarginSummary | None
) -> HyperCorePerpLeg:
    if summary is None:
        raise ValueError(f"accountMarginSummary precompile failed for dex {dex}")
    return HyperCorePerpLeg(
        dex,
        summary.account_value,
        convert_to_wad_int(summary.account_value, USD6_DECIMALS),
    )


def read_hypercore_nav(
    ctx: Web3Context,
    vault_address: ChecksumAddress,
    market_id: MarketId = HYPERCORE_MARKET,
) -> HyperCoreNav:
    """Reproduce ``HyperCoreValuationLib.liveValueWad(market_id)`` for the
    vault at ``ctx.default_block``, in two Multicall3 batches. ``market_id``
    is the id the vault keys its HyperCore market under; the grants are
    decoded with the HyperCore layout either way.

    Mirrors the library's order and short-circuits: the dex bitmap is
    validated first, a vault without a Core account values at 0, a spot token
    with no balance is neither priced nor looked up, and the conditions the
    library reverts on (no price, overflow, a failed precompile) raise
    ``ValueError``.
    """
    vault = PlasmaVault(ctx, vault_address)
    reader = HyperCoreReader(ctx)
    multicall = Multicall3(ctx)
    substrates, oracle_address, exists, native = multicall.aggregate(
        cast(
            list[Call[Any]],
            [
                vault.get_market_substrates(market_id),
                vault.get_price_oracle_middleware_address(),
                reader.core_user_exists(vault_address),
                reader.account_margin_summary(NATIVE_PERP_DEX, vault_address),
            ],
        )
    )
    tokens, bitmap = _market_layout(substrates)
    if not exists:
        return HyperCoreNav(False, bitmap, (), None, (), 0)
    oracle = PriceOracleMiddleware(ctx, oracle_address)
    dexes = [dex for dex in HIP3_VALUATION_ORDER if bitmap & (1 << dex)]
    calls: list[Call[Any]] = []
    for index, evm_asset in tokens:
        calls += [
            reader.spot_balance(vault_address, index),
            reader.token_info(index),
            oracle.get_asset_price(evm_asset),
        ]
    calls += [reader.account_margin_summary(dex, vault_address) for dex in dexes]
    results = multicall.try_aggregate(calls)
    spot = tuple(
        _spot_leg(index, evm_asset, *results[3 * i : 3 * i + 3])
        for i, (index, evm_asset) in enumerate(tokens)
    )
    native_leg = _perp_leg(NATIVE_PERP_DEX, native)
    hip3 = tuple(
        _perp_leg(dex, summary)
        for dex, summary in zip(dexes, results[3 * len(tokens) :], strict=True)
    )
    value = sum(leg.value_wad for leg in spot) + native_leg.value_wad
    value += sum(leg.value_wad for leg in hip3)
    return HyperCoreNav(True, bitmap, spot, native_leg, hip3, value)


@dataclass(frozen=True, slots=True)
class HyperCorePerpMarket:
    """One granted ``PerpMarket`` substrate with its HIP-3 coordinates and the
    coin name the ``perpAssetInfo`` precompile reports (``None`` when the read
    failed, e.g. a delisted market)."""

    asset: int
    dex: int
    read_index: int
    coin: str | None
    max_notional_usd6: int
    reduce_only_required: bool


@dataclass(frozen=True, slots=True)
class HyperCoreVaultState:
    """Everything ``vault info`` shows for a HyperCore market: the NAV legs,
    the balance fuse's own figure, the pending-action state and the granted
    perp markets."""

    market_id: MarketId
    balance_fuse: ChecksumAddress
    nav: HyperCoreNav
    balance_fuse_value_wad: int | None
    pending: HyperCorePendingState | None
    perp_markets: tuple[HyperCorePerpMarket, ...]

    @property
    def nav_matches_balance_fuse(self) -> bool | None:
        if self.balance_fuse_value_wad is None:
            return None
        return self.nav.value_wad == self.balance_fuse_value_wad


def _granted_perp_markets(substrates: Sequence[bytes]) -> list[tuple[int, int, bool]]:
    markets: list[tuple[int, int, bool]] = []
    for raw in substrates:
        info = decode_substrate(raw, HYPERCORE_MARKET)
        if info.type_label != "PERP_MARKET" or info.is_error:
            continue
        markets.append(
            (
                int(info.extra["asset"]),
                int(info.extra["max_notional_usd6"]),
                info.extra["reduce_only_required"] == "true",
            )
        )
    return markets


def read_hypercore_vault_state(
    ctx: Web3Context,
    vault_address: ChecksumAddress,
    balance_fuse: ChecksumAddress,
    market_id: MarketId = HYPERCORE_MARKET,
    *,
    pending_reader: ChecksumAddress | None = None,
) -> HyperCoreVaultState:
    """:func:`read_hypercore_nav` plus the balance fuse's ``balanceOf()`` in
    the vault's context, the pending state (when a reader is known for the
    chain or given) and the granted perp markets with their coin names, all
    at ``ctx.default_block``."""
    nav = read_hypercore_nav(ctx, vault_address, market_id)
    vault = PlasmaVault(ctx, vault_address)
    reader = HyperCoreReader(ctx)
    pending_reader = pending_reader or HYPERCORE_PENDING_READERS.get(ctx.chain_id)
    granted = _granted_perp_markets(vault.get_market_substrates(market_id).call())
    calls: list[Call[Any]] = [vault.balance_fuse_value(balance_fuse)]
    if pending_reader is not None:
        calls.append(HyperCorePendingReader(vault, pending_reader).pending_state())
    calls += [reader.perp_asset_info(read_index_of(asset)) for asset, _, _ in granted]
    results = Multicall3(ctx).try_aggregate(calls)
    fuse_value = results[0]
    pending = results[1] if pending_reader is not None else None
    infos = results[len(calls) - len(granted) :]
    perp_markets = tuple(
        HyperCorePerpMarket(
            asset=asset,
            dex=perp_dex_of(asset),
            read_index=read_index_of(asset),
            coin=info.coin if info is not None else None,
            max_notional_usd6=cap,
            reduce_only_required=reduce_only,
        )
        for (asset, cap, reduce_only), info in zip(granted, infos, strict=True)
    )
    return HyperCoreVaultState(
        market_id=market_id,
        balance_fuse=balance_fuse,
        nav=nav,
        balance_fuse_value_wad=fuse_value,
        pending=pending,
        perp_markets=perp_markets,
    )

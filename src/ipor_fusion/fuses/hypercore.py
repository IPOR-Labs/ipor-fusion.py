"""HyperCore fuses (market 55): a HyperEVM PlasmaVault trading on Hyperliquid
Core through delegatecalled fuses. (The first HyperEVM test vault ran on
market 54, since reassigned to the crosschain market.)

The vault is the Core account: the fuses read the HyperCore precompiles and
queue CoreWriter actions from the vault's own address, so no external position
manager or API wallet ever holds its assets. Each ``enter`` is one CoreWriter
action: an EVM -> Core deposit of a granted spot token (Circle USDC goes through
the CoreDepositWallet), a USD class transfer between spot and the native perp
dex (action 7), ``spotSend`` (6) and ``sendAsset`` (13), a limit order (1), a
cancel (10 by oid, 11 by cloid) or a builder-fee approval (12). HyperCore
executes the queued action in a later L1 block, so the EVM receipt is not a
Core execution receipt, and only one action may be pending per vault at a
time; ``execute`` and the balance refresh revert while one is.

Identifiers (HyperCoreLib.sol): the spot "dex" is :data:`SPOT_DEX` and the
native perp dex is 0. A HIP-3 builder-deployed market is addressed by its
action asset ``100_000 + dex * 10_000 + index_in_dex`` in orders, cancels and
the PerpMarket substrate; the read precompiles take ``dex * 10_000 +
index_in_dex`` instead (:func:`read_index_of`). Amounts are Core integers:
``limit_px`` and ``sz`` carry 8 implied decimals, ``usd6`` 6, and
``amount_wei`` is Core wei (8 decimals for USDC).

The settlement fuse is not encoded here: only the vault's settlement reporter
contract may call it, from its own address.

ABI (one struct argument per method)::

    HyperCoreDepositFuse.enter((uint64 tokenIndex, uint256 amount))
    HyperCoreMarginFuse.enter((uint64 usd6, bool toPerp))
    HyperCoreSendFuse.enterSpotSend((address destination, uint64 tokenIndex,
                                     uint64 amountWei))
    HyperCoreSendFuse.enterSendAsset((address destination, uint32 sourceDex,
                                      uint32 destinationDex, uint64 tokenIndex,
                                      uint64 amountWei))
    HyperCoreOrderFuse.enter((uint32 asset, bool isBuy, uint64 limitPx,
                              uint64 sz, bool reduceOnly, uint8 tif,
                              uint128 cloid))
    HyperCoreCancelFuse.enterCancelByOid((uint32 asset, uint64 oid))
    HyperCoreCancelFuse.enterCancelByCloid((uint32 asset, uint128 cloid))
    HyperCoreBuilderFeeFuse.enter((address builder, uint64 maxFeeRateDecibps))
"""

from __future__ import annotations

from enum import IntEnum

from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import (
    Fuse,
    FuseAction,
    _substrate_address_bytes,
    _validate_not_zero_address,
)
from ipor_fusion.types import Amount

#: ``HyperCoreLib.SPOT_DEX``: the spot account in ``sendAsset`` routes.
SPOT_DEX = 0xFFFFFFFF
#: ``HyperCoreLib.NATIVE_PERP_DEX``: Hyperliquid's own perp dex.
NATIVE_PERP_DEX = 0
#: Core token index of USDC.
USDC_TOKEN_INDEX = 0
#: Core -> EVM route for token 0: a spot -> spot ``sendAsset`` to this address
#: credits the sender's EVM balance. Never the target of an ERC-20 transfer.
USDC_SYSTEM_ADDRESS: ChecksumAddress = ChecksumAddress(
    "0x2000000000000000000000000000000000000000"
)
#: HIP-3 perp dexes the fuses admit on HyperEVM mainnet (HyperCoreLib
#: ``SUPPORTED_HIP3_DEX_MASK``: xyz 1, abcd 6, para 8, mkts 9, io 10); on any
#: other chain the set is empty.
SUPPORTED_HIP3_DEXES = frozenset({1, 6, 8, 9, 10})
_NATIVE_PERP_ASSET_LIMIT = 10_000
_HIP3_ASSET_OFFSET = 100_000
_HIP3_DEX_MULTIPLIER = 10_000
_HIP3_FIRST_ASSET = 110_000  # dex 1, index 0
_OUTCOME_FIRST_ASSET = 100_000_000  # first id of the outcome family


class TimeInForce(IntEnum):
    """``tif`` of a limit order (HyperCoreLib.TIF_*)."""

    ALO = 1
    GTC = 2
    IOC = 3


class SettlementMode(IntEnum):
    """``Config{SettlementMode}``: how a pending action is cleared."""

    TIMING = 1
    REPORTED = 2


class HyperCoreConfigKey(IntEnum):
    """``HyperCoreSubstrateLib.ConfigKey`` (the Undefined member is invalid)."""

    WINDOW_TRANSFER_SECONDS = 1
    WINDOW_ORDER_SECONDS = 2
    MAX_USD_CLASS_TRANSFER_USD6 = 3
    PERP_DEX_IDS = 4
    SETTLEMENT_MODE = 5
    SPOT_SEND_BRIDGE_ENABLED = 6


def hip3_action_asset(dex: int, index_in_dex: int) -> int:
    """The CoreWriter asset of market ``index_in_dex`` on perp dex ``dex``:
    the index itself on the native dex, ``100_000 + dex * 10_000 + index``
    on a HIP-3 dex. Rejects anything outside the two families the fuses
    accept (``index_in_dex`` below 10 000, HIP-3 assets below 100 000 000);
    whether ``dex`` is admitted on this chain is :func:`is_supported_perp_dex`."""
    _validate_uint(dex, 32, "dex")
    if not 0 <= index_in_dex < _HIP3_DEX_MULTIPLIER:
        raise ValueError(
            f"index_in_dex must be below {_HIP3_DEX_MULTIPLIER}, got {index_in_dex}"
        )
    if dex == NATIVE_PERP_DEX:
        return index_in_dex
    asset = _HIP3_ASSET_OFFSET + dex * _HIP3_DEX_MULTIPLIER + index_in_dex
    if asset >= _OUTCOME_FIRST_ASSET:
        raise ValueError(f"dex {dex} is outside the HIP-3 asset range")
    return asset


def is_native_perp_asset(asset: int) -> bool:
    """``HyperCoreLib.isNativePerpAsset``: an action asset below 10 000."""
    _validate_uint(asset, 32, "asset")
    return asset < _NATIVE_PERP_ASSET_LIMIT


def is_hip3_asset(asset: int) -> bool:
    """``HyperCoreLib.isHip3Asset``: ``110_000 <= asset < 100_000_000``. Assets
    between the families (10 000 .. 109 999) and the outcome family above are
    neither, and the perp fuses reject them."""
    _validate_uint(asset, 32, "asset")
    return _HIP3_FIRST_ASSET <= asset < _OUTCOME_FIRST_ASSET


def is_supported_perp_dex(dex: int) -> bool:
    """``HyperCoreLib.isSupportedPerpDex`` on HyperEVM mainnet: the native dex
    or one of :data:`SUPPORTED_HIP3_DEXES`."""
    _validate_uint(dex, 32, "dex")
    return dex == NATIVE_PERP_DEX or dex in SUPPORTED_HIP3_DEXES


def perp_dex_of(asset: int) -> int:
    """``HyperCoreLib.perpDexOf``: 0 for a native asset, the dex index for a
    HIP-3 asset; raises for any other value, as the fuses revert."""
    if is_native_perp_asset(asset):
        return NATIVE_PERP_DEX
    if is_hip3_asset(asset):
        return (asset - _HIP3_ASSET_OFFSET) // _HIP3_DEX_MULTIPLIER
    raise ValueError(f"asset {asset} is neither a native nor a HIP-3 perp asset")


def read_index_of(asset: int) -> int:
    """``HyperCoreLib.readIndexOf``: the index the read precompiles take --
    native assets unchanged, HIP-3 assets without the 100 000 offset; raises
    for any other value."""
    if is_native_perp_asset(asset):
        return asset
    if is_hip3_asset(asset):
        return asset - _HIP3_ASSET_OFFSET
    raise ValueError(f"asset {asset} is neither a native nor a HIP-3 perp asset")


def _validate_uint(value: int, bits: int, name: str) -> None:
    if not 0 <= value < (1 << bits):
        raise ValueError(f"{name} must fit uint{bits}, got {value}")


def _validate_positive_uint(value: int, bits: int, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero, got {value}")
    _validate_uint(value, bits, name)


class HyperCoreDepositFuse(Fuse):
    """EVM -> Core deposit of a granted spot token (CoreWriter-free: the
    token's verified route, the CoreDepositWallet for Circle USDC)."""

    def enter(self, *, token_index: int, amount: Amount) -> FuseAction:
        """Deposit up to ``amount`` (ERC-20 units) of Core token
        ``token_index``; the fuse caps it at the vault's balance."""
        _validate_uint(token_index, 64, "token_index")
        self._validate_amount(amount, "amount")
        return self._action_raw("enter((uint64,uint256))", [[token_index, amount]])


class HyperCoreMarginFuse(Fuse):
    """USD class transfer (action 7) between spot and the native perp dex."""

    def enter(self, *, usd6: int, to_perp: bool) -> FuseAction:
        """Move ``usd6`` (USD, 6 decimals) spot -> native perp when ``to_perp``,
        back otherwise. HIP-3 dexes are reached with ``sendAsset`` instead."""
        _validate_positive_uint(usd6, 64, "usd6")
        return self._action_raw("enter((uint64,bool))", [[usd6, to_perp]])


class HyperCoreSendFuse(Fuse):
    """``spotSend`` (action 6) and ``sendAsset`` (action 13)."""

    def spot_send(
        self, *, destination: ChecksumAddress, token_index: int, amount_wei: int
    ) -> FuseAction:
        """Send ``amount_wei`` of ``token_index`` from spot to a granted
        destination; a token system address additionally needs
        ``Config{SpotSendBridgeEnabled}``."""
        _validate_not_zero_address(destination, "destination")
        _validate_uint(token_index, 64, "token_index")
        _validate_positive_uint(amount_wei, 64, "amount_wei")
        return self._action_raw(
            "enterSpotSend((address,uint64,uint64))",
            [[destination, token_index, amount_wei]],
        )

    def send_asset(
        self,
        *,
        destination: ChecksumAddress,
        source_dex: int,
        destination_dex: int,
        token_index: int,
        amount_wei: int,
    ) -> FuseAction:
        """Move ``amount_wei`` of ``token_index`` between ``source_dex`` and
        ``destination_dex`` (:data:`SPOT_DEX`, 0 or a configured HIP-3 dex).
        Any leg touching a HIP-3 dex must be a self-send of USDC; the
        spot -> spot route to :data:`USDC_SYSTEM_ADDRESS` bridges Core -> EVM."""
        _validate_not_zero_address(destination, "destination")
        _validate_uint(source_dex, 32, "source_dex")
        _validate_uint(destination_dex, 32, "destination_dex")
        _validate_uint(token_index, 64, "token_index")
        _validate_positive_uint(amount_wei, 64, "amount_wei")
        return self._action_raw(
            "enterSendAsset((address,uint32,uint32,uint64,uint64))",
            [[destination, source_dex, destination_dex, token_index, amount_wei]],
        )


class HyperCoreOrderFuse(Fuse):
    """Limit order (action 1) on a granted PerpMarket."""

    def enter(
        self,
        *,
        asset: int,
        is_buy: bool,
        limit_px: int,
        sz: int,
        tif: TimeInForce,
        cloid: int,
        reduce_only: bool = False,
    ) -> FuseAction:
        """Place ``sz`` (8 decimals) of ``asset`` at ``limit_px`` (8 decimals).
        The fuse enforces the market's notional cap and lot size and, for
        HIP-3 markets, that this is the vault's only PerpMarket grant."""
        _validate_uint(asset, 32, "asset")
        _validate_positive_uint(limit_px, 64, "limit_px")
        _validate_positive_uint(sz, 64, "sz")
        _validate_uint(cloid, 128, "cloid")
        tif = TimeInForce(tif)
        return self._action_raw(
            "enter((uint32,bool,uint64,uint64,bool,uint8,uint128))",
            [[asset, is_buy, limit_px, sz, reduce_only, int(tif), cloid]],
        )


class HyperCoreCancelFuse(Fuse):
    """Cancel by oid (action 10) or by cloid (action 11)."""

    def cancel_by_oid(self, *, asset: int, oid: int) -> FuseAction:
        _validate_uint(asset, 32, "asset")
        _validate_positive_uint(oid, 64, "oid")
        return self._action_raw("enterCancelByOid((uint32,uint64))", [[asset, oid]])

    def cancel_by_cloid(self, *, asset: int, cloid: int) -> FuseAction:
        _validate_uint(asset, 32, "asset")
        _validate_positive_uint(cloid, 128, "cloid")
        return self._action_raw(
            "enterCancelByCloid((uint32,uint128))", [[asset, cloid]]
        )


class HyperCoreBuilderFeeFuse(Fuse):
    """Builder-fee approval (action 12) for a granted Builder."""

    def enter(
        self, *, builder: ChecksumAddress, max_fee_rate_decibps: int
    ) -> FuseAction:
        _validate_not_zero_address(builder, "builder")
        _validate_uint(max_fee_rate_decibps, 64, "max_fee_rate_decibps")
        return self._action_raw(
            "enter((address,uint64))", [[builder, max_fee_rate_decibps]]
        )


def _word(substrate_type: int, data: int) -> bytes:
    return ((substrate_type << 248) | data).to_bytes(32, "big")


def _address_word(address: ChecksumAddress, name: str) -> int:
    _validate_not_zero_address(address, name)
    return int.from_bytes(_substrate_address_bytes(address), "big")


class HyperCoreSubstrates:
    """Typed bytes32 substrate encoders for the HyperCore market (55).

    Mirrors ``HyperCoreSubstrateLib.sol``: ``bytes32(uint256(type) << 248 |
    data)``. The Atomist grants a SpotToken for every Core token the vault may
    hold (an unlisted token is not in NAV), a PerpMarket per tradable market
    (at most one when any of them is HIP-3), Destinations for ``sendAsset`` /
    ``spotSend`` (the vault itself and, for the Core -> EVM route, the token's
    system address), a SendCap per token, Builders, and the Config keys.
    :func:`ipor_fusion.decode_substrate` inverts these.
    """

    @staticmethod
    def spot_token(token_index: int, evm_asset: ChecksumAddress) -> bytes:
        """Core token ``token_index`` priced through its EVM ERC-20 (for
        Circle USDC the wallet's ``token()``, not the CoreDepositWallet)."""
        _validate_uint(token_index, 64, "token_index")
        return _word(1, (token_index << 160) | _address_word(evm_asset, "evm_asset"))

    @staticmethod
    def perp_market(
        asset: int, max_notional_usd6: int, *, reduce_only_required: bool = False
    ) -> bytes:
        """Action asset ``asset`` with a per-order notional cap in USD (6
        decimals); ``reduce_only_required`` admits only position-reducing orders."""
        _validate_uint(asset, 32, "asset")
        _validate_uint(max_notional_usd6, 88, "max_notional_usd6")
        data = (asset << 216) | (max_notional_usd6 << 128)
        if reduce_only_required:
            data |= 1 << 120
        return _word(2, data)

    @staticmethod
    def destination(destination: ChecksumAddress) -> bytes:
        return _word(3, _address_word(destination, "destination"))

    @staticmethod
    def builder(builder: ChecksumAddress, max_fee_rate_decibps: int) -> bytes:
        _validate_uint(max_fee_rate_decibps, 64, "max_fee_rate_decibps")
        return _word(
            4, (max_fee_rate_decibps << 160) | _address_word(builder, "builder")
        )

    @staticmethod
    def config(key: HyperCoreConfigKey, value: int) -> bytes:
        """One Config key; ``PERP_DEX_IDS`` is a bitmap (bit i enables perp
        dex i, bit 0 stays clear), ``SETTLEMENT_MODE`` a :class:`SettlementMode`."""
        key = HyperCoreConfigKey(key)
        _validate_uint(value, 240, "value")
        return _word(5, (int(key) << 240) | value)

    @staticmethod
    def send_cap(token_index: int, max_wei: int) -> bytes:
        """Per-transfer cap for ``token_index`` in Core wei."""
        _validate_uint(token_index, 64, "token_index")
        _validate_uint(max_wei, 128, "max_wei")
        return _word(6, (token_index << 128) | max_wei)

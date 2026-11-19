"""Readers for Lido Lend (a Morpho Blue 1.0 fork with a per-market controller).

Preview: the Fusion family is built for markets 58 (Lido Lend) and 59 (its
flash loan) and is deployed nowhere yet; the core below is the Hoodi testnet
one. Inside a vault the core address is the ``LIDO_LEND()`` immutable of the
vault's Lido balance fuse (:class:`LidoLendFuseReader`); the map is for
standalone reads.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_typing import ChecksumAddress
from web3 import Web3
from web3.types import Timestamp

from ipor_fusion.chains import CHAIN_NAMES
from ipor_fusion.core.context import Web3Context
from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.core.multicall import Multicall3
from ipor_fusion.errors import MorphoMarketNotFoundError, UnsupportedChainError
from ipor_fusion.readers.morpho import (
    MorphoMarket,
    MorphoPositionBreakdown,
    MorphoReader,
    _position_breakdown,
)
from ipor_fusion.types import ZERO_ADDRESS, Amount, ChainId, MorphoBlueMarketId, Shares

_addr = Web3.to_checksum_address

# The Lido Lend core (Morpho Blue 1.0 fork) per chain; Hoodi is the only
# deployment the Fusion family targets so far.
LIDO_LEND_CORE_ADDRESSES: dict[ChainId, ChecksumAddress] = {
    ChainId(560048): _addr("0x78d3262E82A35d290507bCF9B4834735DdA6b854"),  # Hoodi
}


def lido_lend_core_address(chain_id: ChainId) -> ChecksumAddress:
    """Lido Lend core contract address for ``chain_id``.

    Raises :class:`UnsupportedChainError` when no deployment is known.
    """
    address = LIDO_LEND_CORE_ADDRESSES.get(chain_id)
    if address is not None:
        return address
    known = ", ".join(
        f"{cid} ({CHAIN_NAMES.get(cid, 'unnamed')})"
        for cid in sorted(LIDO_LEND_CORE_ADDRESSES)
    )
    name = CHAIN_NAMES.get(chain_id)
    label = f"{chain_id} ({name})" if name else str(chain_id)
    raise UnsupportedChainError(
        f"Lido Lend core address unknown for chain {label}; known chains: {known}"
    )


@dataclass(slots=True)
class LidoLendBorrowerPosition:
    """A borrower's controller-side position (``positionOf``).

    ``settlement_ltv`` is a WAD fraction, ``0`` when no personal target is set.
    """

    active: Amount
    activating: Amount
    dust: Amount
    unlock_time: Timestamp
    flag_count: int
    banned: bool
    debt_assets: Amount
    settlement_ltv: int
    is_next_to_settle: bool
    personal_slice_assets: Amount
    queue_root_slice_assets: Amount


@dataclass(slots=True)
class LidoLendCollateralActivationConfig:
    """The controller's collateral-activation parameters."""

    min_activation_delay: int
    max_activation_delay: int
    max_delay_inflow: int
    min_deposit_amount: Amount
    activating_lock_duration: int
    min_activation_amount: Amount
    max_guardians_quorum_delay: int
    delay_per_strike: int
    delay_per_suspect: int


@dataclass(slots=True)
class LidoLendSettlementSlice:
    """A settlement cut: supply shares paid, borrow shares canceled and left."""

    supply_shares: Shares
    borrow_shares: Shares
    remaining_borrow_shares: Shares


@dataclass(slots=True)
class LidoLendSettlementQuote:
    """The cuts a borrower may be eligible for, each shrunk to the caller's budget."""

    personal: LidoLendSettlementSlice
    queue_root: LidoLendSettlementSlice


@dataclass(slots=True)
class LidoLendNextSettlement:
    """The settlement queue root and its cut; ``borrower`` is the zero address
    when the queue is empty."""

    borrower: ChecksumAddress
    slice: LidoLendSettlementSlice


@dataclass(slots=True)
class LidoLendSettlementConfig:
    """Sizing policy of the queue-root settlement cut."""

    slice_floor_bps: int
    slice_floor_assets: Amount
    full_cut_below_collateral: Amount
    ltv_step_bps: int


@dataclass(slots=True)
class LidoLendSettlementLtv:
    """A borrower's personal post-cut target LTV (WAD) and its slice in assets."""

    ltv: int
    slice_assets: Amount


@dataclass(slots=True)
class LidoLendPositionState:
    """A user's position in one Lido Lend market: the core breakdown plus the
    controller state.

    The controller fields are ``None`` when the core names no controller for
    the market (``controller`` is the zero address).
    """

    breakdown: MorphoPositionBreakdown
    controller: ChecksumAddress
    settlement_enabled: bool
    borrower: LidoLendBorrowerPosition | None
    paused: bool | None
    frozen: bool | None
    bad_debt: bool | None
    instant_activation: bool | None


_SLICE_TYPE = "(uint256,uint256,uint256)"
_SLICE_TUPLE = tuple[int, int, int]
_POSITION_TUPLE = tuple[int, int, int, int, int, bool, int, int, bool, int, int]
_ACTIVATION_TUPLE = tuple[int, int, int, int, int, int, int, int, int]


def _slice_decoder(value: _SLICE_TUPLE) -> LidoLendSettlementSlice:
    supply_shares, borrow_shares, remaining = value
    return LidoLendSettlementSlice(
        supply_shares=Shares(supply_shares),
        borrow_shares=Shares(borrow_shares),
        remaining_borrow_shares=Shares(remaining),
    )


def _borrower_position_decoder(value: _POSITION_TUPLE) -> LidoLendBorrowerPosition:
    (
        active,
        activating,
        dust,
        unlock_time,
        flag_count,
        banned,
        debt_assets,
        settlement_ltv,
        is_next_to_settle,
        personal_slice,
        queue_root_slice,
    ) = value
    return LidoLendBorrowerPosition(
        active=Amount(active),
        activating=Amount(activating),
        dust=Amount(dust),
        unlock_time=Timestamp(unlock_time),
        flag_count=flag_count,
        banned=banned,
        debt_assets=Amount(debt_assets),
        settlement_ltv=settlement_ltv,
        is_next_to_settle=is_next_to_settle,
        personal_slice_assets=Amount(personal_slice),
        queue_root_slice_assets=Amount(queue_root_slice),
    )


def _activation_config_decoder(
    value: _ACTIVATION_TUPLE,
) -> LidoLendCollateralActivationConfig:
    (
        min_delay,
        max_delay,
        max_inflow,
        min_deposit,
        lock_duration,
        min_activation,
        quorum_delay,
        per_strike,
        per_suspect,
    ) = value
    return LidoLendCollateralActivationConfig(
        min_activation_delay=min_delay,
        max_activation_delay=max_delay,
        max_delay_inflow=max_inflow,
        min_deposit_amount=Amount(min_deposit),
        activating_lock_duration=lock_duration,
        min_activation_amount=Amount(min_activation),
        max_guardians_quorum_delay=quorum_delay,
        delay_per_strike=per_strike,
        delay_per_suspect=per_suspect,
    )


def _next_settlement_decoder(
    value: tuple[str, _SLICE_TUPLE],
) -> LidoLendNextSettlement:
    borrower, slice_value = value
    return LidoLendNextSettlement(
        borrower=Web3.to_checksum_address(borrower),
        slice=_slice_decoder(slice_value),
    )


def _settlement_quote_decoder(
    value: tuple[_SLICE_TUPLE, _SLICE_TUPLE],
) -> LidoLendSettlementQuote:
    personal, queue_root = value
    return LidoLendSettlementQuote(
        personal=_slice_decoder(personal), queue_root=_slice_decoder(queue_root)
    )


def _settlement_config_decoder(
    value: tuple[int, int, int, int],
) -> LidoLendSettlementConfig:
    floor_bps, floor_assets, full_cut_below, step_bps = value
    return LidoLendSettlementConfig(
        slice_floor_bps=floor_bps,
        slice_floor_assets=Amount(floor_assets),
        full_cut_below_collateral=Amount(full_cut_below),
        ltv_step_bps=step_bps,
    )


def _settlement_ltv_decoder(value: tuple[int, int]) -> LidoLendSettlementLtv:
    ltv, slice_assets = value
    return LidoLendSettlementLtv(ltv=ltv, slice_assets=Amount(slice_assets))


class LidoLendFuseReader(ContractWrapper):
    """Reader for the immutables of an IPOR Fusion Lido Lend fuse.

    Every Lido fuse, the balance fuse included, binds the core it works
    against in ``LIDO_LEND``.
    """

    def lido_lend(self) -> Call[ChecksumAddress]:
        return self._view(
            "LIDO_LEND()", output_types=["address"], decoder=Web3.to_checksum_address
        )


class LidoLendMarketController(ContractWrapper):
    """View-only reader for a Lido Lend market controller.

    The controller address of a market comes from
    :meth:`LidoLendReader.market_controller`. Setters are not wrapped.
    """

    def position_of(self, account: ChecksumAddress) -> Call[LidoLendBorrowerPosition]:
        return self._view(
            "positionOf(address)",
            account,
            output_types=[
                "(uint256,uint256,uint256,uint256,uint256,bool,uint256,uint256,"
                "bool,uint256,uint256)"
            ],
            decoder=_borrower_position_decoder,
        )

    def collateral_activation_config(self) -> Call[LidoLendCollateralActivationConfig]:
        return self._view(
            "collateralActivationConfig()",
            output_types=[
                "(uint40,uint40,uint128,uint128,uint40,uint128,uint40,uint40,uint40)"
            ],
            decoder=_activation_config_decoder,
        )

    def next_settlement_borrower(
        self, budget_supply_shares: Shares
    ) -> Call[LidoLendNextSettlement]:
        return self._view(
            "nextSettlementBorrower(uint256)",
            budget_supply_shares,
            output_types=["address", _SLICE_TYPE],
            decoder=_next_settlement_decoder,
        )

    def settlement_for(
        self, borrower: ChecksumAddress, budget_supply_shares: Shares
    ) -> Call[LidoLendSettlementQuote]:
        return self._view(
            "settlementFor(address,uint256)",
            borrower,
            budget_supply_shares,
            output_types=[f"({_SLICE_TYPE},{_SLICE_TYPE})"],
            decoder=_settlement_quote_decoder,
        )

    def settlement_config(self) -> Call[LidoLendSettlementConfig]:
        return self._view(
            "settlementConfig()",
            output_types=["(uint16,uint96,uint96,uint16)"],
            decoder=_settlement_config_decoder,
        )

    def settlement_ltv(self, borrower: ChecksumAddress) -> Call[LidoLendSettlementLtv]:
        return self._view(
            "settlementLtv(address)",
            borrower,
            output_types=["uint256", "uint256"],
            decoder=_settlement_ltv_decoder,
        )

    def has_bad_debt(self) -> Call[bool]:
        return self._view("hasBadDebt()", output_types=["bool"])

    def has_instant_activation(self, account: ChecksumAddress) -> Call[bool]:
        return self._view(
            "hasInstantActivation(address)", account, output_types=["bool"]
        )

    def is_paused(self) -> Call[bool]:
        return self._view("isPaused()", output_types=["bool"])

    def is_frozen(self) -> Call[bool]:
        return self._view("isFrozen()", output_types=["bool"])

    def get_resume_since_timestamp(self) -> Call[Timestamp]:
        return self._view(
            "getResumeSinceTimestamp()", output_types=["uint256"], decoder=Timestamp
        )

    def supply_cap(self) -> Call[Amount]:
        return self._view("supplyCap()", output_types=["uint256"], decoder=Amount)

    def max_deposit(self) -> Call[Amount]:
        return self._view("maxDeposit()", output_types=["uint256"], decoder=Amount)

    def max_withdrawable_assets(self, account: ChecksumAddress) -> Call[Amount]:
        return self._view(
            "maxWithdrawableAssets(address)",
            account,
            output_types=["uint256"],
            decoder=Amount,
        )

    def settlement_borrowers_count(self) -> Call[int]:
        return self._view("settlementBorrowersCount()", output_types=["uint256"])


class LidoLendReader(MorphoReader):
    """Reader for the Lido Lend core: the Morpho Blue reads plus the
    controller lookup."""

    def market_controller(self, market_id: MorphoBlueMarketId) -> Call[ChecksumAddress]:
        return self._view(
            "marketController(bytes32)",
            bytes.fromhex(market_id.removeprefix("0x")),
            output_types=["address"],
            decoder=Web3.to_checksum_address,
        )

    def is_settlement_enabled(self, market_id: MorphoBlueMarketId) -> Call[bool]:
        return self._view(
            "isSettlementEnabled(bytes32)",
            bytes.fromhex(market_id.removeprefix("0x")),
            output_types=["bool"],
        )

    def read_position_state(
        self, ctx: Web3Context, market_id: MorphoBlueMarketId, user: ChecksumAddress
    ) -> LidoLendPositionState:
        """Read the user's position in one market: core state first, then the
        controller the core names (two Multicall3 round trips).

        Raises :class:`MorphoMarketNotFoundError` for a market never created
        on this core. With no controller the controller fields are ``None``.
        """
        core = Multicall3(ctx).batch()
        pos_read = core.add(self.position(market_id, user))
        market_read = core.add(self.market(market_id))
        params_read = core.add(self.market_params(market_id))
        controller_read = core.add(self.market_controller(market_id))
        enabled_read = core.add(self.is_settlement_enabled(market_id))
        core.run()
        self._require_market(ctx, market_id, market_read.value)
        breakdown = _position_breakdown(
            market_id, pos_read.value, market_read.value, params_read.value
        )
        controller = controller_read.value
        state = LidoLendPositionState(
            breakdown=breakdown,
            controller=controller,
            settlement_enabled=enabled_read.value,
            borrower=None,
            paused=None,
            frozen=None,
            bad_debt=None,
            instant_activation=None,
        )
        if controller != ZERO_ADDRESS:
            _read_controller_state(
                ctx, LidoLendMarketController(controller), user, state
            )
        return state

    def _require_market(
        self, ctx: Web3Context, market_id: MorphoBlueMarketId, market: MorphoMarket
    ) -> None:
        if market.last_update == 0:
            raise MorphoMarketNotFoundError(
                f"Lido Lend market {market_id} does not exist on chain "
                f"{ctx.chain_id} (core at {self.address})"
            )


def _read_controller_state(
    ctx: Web3Context,
    controller: LidoLendMarketController,
    user: ChecksumAddress,
    state: LidoLendPositionState,
) -> None:
    batch = Multicall3(ctx).batch()
    position = batch.add(controller.position_of(user))
    paused = batch.add(controller.is_paused())
    frozen = batch.add(controller.is_frozen())
    bad_debt = batch.add(controller.has_bad_debt())
    instant = batch.add(controller.has_instant_activation(user))
    batch.run()
    state.borrower = position.value
    state.paused = paused.value
    state.frozen = frozen.value
    state.bad_debt = bad_debt.value
    state.instant_activation = instant.value

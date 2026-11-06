"""Lido Lend (market 58) fuse encoders and substrate helpers. Preview.

Lido Lend is a Morpho Blue fork behind a per-market controller. Its fuses are
a family of their own: the supply, collateral, borrow and flash loan fuses
share the selectors of the Morpho fuses, the settle fuse is new. The market id
is the Morpho Blue one, ``keccak256(abi.encode(MarketParams))``. Borrowing
needs two steps (activate the collateral, then borrow); it is one step only if
the controller grants the vault instant activation, which may never happen.
The market has no instant withdraw.
"""

from eth_abi import encode
from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import (
    ZERO_ADDRESS,
    Fuse,
    FuseAction,
    _encode_address_substrate,
)
from ipor_fusion.types import MAX_UINT256, Amount, MorphoBlueMarketId, Shares

_ENTER_PAIR = "enter((bytes32,uint256))"
_EXIT_PAIR = "exit((bytes32,uint256))"
_ENTER_TRIPLE = "enter((bytes32,uint256,uint256))"
_EXIT_TRIPLE = "exit((bytes32,uint256,uint256))"

_COLLATERAL_TAG = 0x01
_BPS = 10_000
_WAD = 10**18


def _market_bytes(market_id: MorphoBlueMarketId) -> bytes:
    return bytes.fromhex(market_id.removeprefix("0x"))


def is_collateral_word(word: bytes) -> bool:
    """Whether a 32-byte substrate word has the collateral shape of
    ``LidoLendSubstrateLib``: type byte ``0x01`` followed by 11 zero bytes and a
    token address. Every other word is a market id."""
    return len(word) == 32 and word[0] == _COLLATERAL_TAG and not any(word[1:12])


def _validate_market_id(market_id: MorphoBlueMarketId) -> bytes:
    """A market id that looks like a collateral word is refused by the fuses
    (``isMarketGranted`` rejects it as an unsupported market)."""
    raw = _market_bytes(market_id)
    if len(raw) != 32:
        raise ValueError(f"market_id must be 32 bytes, got {len(raw)}")
    if is_collateral_word(raw):
        raise ValueError(
            "market_id has the shape of a collateral word "
            "(0x01, 11 zero bytes, address) and is never a Lido Lend market"
        )
    return raw


def _validate_positive_int(value: int, name: str) -> None:
    if value <= 0:
        raise ValueError(f"{name} must be greater than zero, got {value}")


class LidoLendSupplyFuse(Fuse):
    """Fuse for supplying and withdrawing the loan token of a Lido Lend market
    (shares the selectors of ``MorphoSupplyFuse``). A withdrawal is trimmed to
    the vault's position and to the market's free liquidity; when either is
    zero the fuse makes no call (also in pause). With liquidity available, a
    paused market reverts with ``ResumedExpected``."""

    SIGNATURES = (_ENTER_PAIR, _EXIT_PAIR)

    def supply(self, *, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        raw = _validate_market_id(market_id)
        self._validate_amount(amount, "amount")
        return self._action_raw(_ENTER_PAIR, [[raw, amount]])

    def withdraw(self, *, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        raw = _validate_market_id(market_id)
        self._validate_amount(amount, "amount")
        return self._action_raw(_EXIT_PAIR, [[raw, amount]])

    def withdraw_all(self, *, market_id: MorphoBlueMarketId) -> FuseAction:
        """Withdraw the whole position (``amount = type(uint256).max``)."""
        raw = _validate_market_id(market_id)
        return self._action_raw(_EXIT_PAIR, [[raw, MAX_UINT256]])


class LidoLendCollateralFuse(Fuse):
    """Fuse for supplying and withdrawing collateral of a Lido Lend market
    (shares the selectors of ``MorphoCollateralFuse``). Supplying collateral
    needs the market word and the collateral word of the collateral token;
    withdrawing needs only the market word, so collateral can be withdrawn
    even after its collateral word is revoked."""

    SIGNATURES = (_ENTER_PAIR, _EXIT_PAIR)

    def supply_collateral(
        self, *, market_id: MorphoBlueMarketId, amount: Amount
    ) -> FuseAction:
        raw = _validate_market_id(market_id)
        self._validate_amount(amount, "amount")
        return self._action_raw(_ENTER_PAIR, [[raw, amount]])

    def withdraw_collateral(
        self, *, market_id: MorphoBlueMarketId, amount: Amount
    ) -> FuseAction:
        raw = _validate_market_id(market_id)
        self._validate_amount(amount, "amount")
        return self._action_raw(_EXIT_PAIR, [[raw, amount]])

    def withdraw_all_collateral(self, *, market_id: MorphoBlueMarketId) -> FuseAction:
        """Withdraw the whole collateral (``amount = type(uint256).max``; the
        fuse clamps it to the collateral of the vault's position). It fails
        while debt is open or an activating collateral lot is locked."""
        raw = _validate_market_id(market_id)
        return self._action_raw(_EXIT_PAIR, [[raw, MAX_UINT256]])


class LidoLendBorrowFuse(Fuse):
    """Fuse for borrowing and repaying on a Lido Lend market (shares the
    selectors of ``MorphoBorrowFuse``). The core takes exactly one of
    ``assets`` and ``shares``; the SDK sets the other to zero. A borrow needs
    the collateral to be activated first (a separate step); one step only if the
    controller grants the vault instant activation, which may never happen."""

    SIGNATURES = (_ENTER_TRIPLE, _EXIT_TRIPLE)

    def borrow(self, *, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        raw = _validate_market_id(market_id)
        self._validate_amount(amount, "amount")
        # shares = 0: borrow by assets
        return self._action_raw(_ENTER_TRIPLE, [[raw, amount, 0]])

    def borrow_shares(
        self, *, market_id: MorphoBlueMarketId, shares: Shares
    ) -> FuseAction:
        raw = _validate_market_id(market_id)
        _validate_positive_int(shares, "shares")
        # assets = 0: borrow by shares
        return self._action_raw(_ENTER_TRIPLE, [[raw, 0, shares]])

    def repay(self, *, market_id: MorphoBlueMarketId, amount: Amount) -> FuseAction:
        raw = _validate_market_id(market_id)
        self._validate_amount(amount, "amount")
        # shares = 0: repay by assets
        return self._action_raw(_EXIT_TRIPLE, [[raw, amount, 0]])

    def repay_shares(
        self, *, market_id: MorphoBlueMarketId, shares: Shares
    ) -> FuseAction:
        raw = _validate_market_id(market_id)
        _validate_positive_int(shares, "shares")
        # assets = 0: repay by shares (shares take precedence in the fuse)
        return self._action_raw(_EXIT_TRIPLE, [[raw, 0, shares]])

    def repay_all(self, *, market_id: MorphoBlueMarketId) -> FuseAction:
        """Repay the whole debt: ``assets = type(uint256).max`` (any amount at
        or above the debt repays by the debt's shares), ``shares = 0``."""
        raw = _validate_market_id(market_id)
        return self._action_raw(_EXIT_TRIPLE, [[raw, MAX_UINT256, 0]])


class LidoLendFlashLoanFuse(Fuse):
    """Fuse for flash loans through the Lido Lend core (shares the selector of
    ``MorphoFlashLoanFuse``). The core calls back ``onMorphoFlashLoan`` on the
    vault, so the callback handler must accept the core as sender. The loan
    token must be granted on market 59."""

    SIGNATURES = ("enter((address,uint256,bytes))",)

    def flash_loan(
        self, *, asset: ChecksumAddress, amount: Amount, actions: list[FuseAction]
    ) -> FuseAction:
        self._validate_address(asset, "asset")
        self._validate_amount(amount, "amount")
        bytes_data = [[action.fuse, action.data] for action in actions]
        encoded_actions = encode(["(address,bytes)[]"], [bytes_data])
        return self._action_raw(
            "enter((address,uint256,bytes))",
            [[asset, amount, encoded_actions]],
        )


class LidoLendSettleFuse(Fuse):
    """Fuse for exiting the vault's illiquid supply in a Lido Lend market
    through settlement: the vault burns supply shares to cancel borrowers'
    debt and receives their collateral. The fuse makes no call while the
    controller reports bad debt.

    ``min_collateral_per_supply_asset_wad`` is the rate guard, in raw
    collateral-token units per 1e18 raw loan-token units of burned supply.
    ``0`` disables the guard and is the only way to disable it; pass it only
    explicitly. The guard compares against the burned supply rounded up while
    the seized collateral is rounded down, so a settle at exact parity can fail
    by rounding alone: use a non-zero tolerance. Compute the minimum from the
    vault's own price-middleware prices with
    :func:`min_collateral_per_supply_asset_wad`, never from the Lido oracle.
    Judge the outcome by return values and balance deltas: a
    settle without progress returns zeros and emits no event.
    """

    SIGNATURES = ("enter((bytes32,address,uint256,uint256,uint256))",)

    def settle_queue_root(
        self,
        *,
        market_id: MorphoBlueMarketId,
        supply_shares_budget: Shares,
        max_iterations: int,
        min_collateral_per_supply_asset_wad: int,
    ) -> FuseAction:
        """Settle the borrowers at the head of the settlement queue
        (``borrower = 0``), up to ``max_iterations`` cuts."""
        _validate_positive_int(max_iterations, "max_iterations")
        return self._settle(
            market_id,
            ZERO_ADDRESS,  # borrower = 0: queue-root mode
            supply_shares_budget,
            max_iterations,
            min_collateral_per_supply_asset_wad,
        )

    def settle_borrower(
        self,
        *,
        market_id: MorphoBlueMarketId,
        borrower: ChecksumAddress,
        supply_shares_budget: Shares,
        min_collateral_per_supply_asset_wad: int,
    ) -> FuseAction:
        """Settle one named borrower (personal mode)."""
        self._validate_address(borrower, "borrower")
        return self._settle(
            market_id,
            borrower,
            supply_shares_budget,
            # maxIterations = 1: personal mode is one cut, 0 is a no-op
            1,
            min_collateral_per_supply_asset_wad,
        )

    def _settle(
        self,
        market_id: MorphoBlueMarketId,
        borrower: ChecksumAddress,
        supply_shares_budget: Shares,
        max_iterations: int,
        min_collateral_per_supply_asset_wad: int,
    ) -> FuseAction:
        raw = _validate_market_id(market_id)
        _validate_positive_int(supply_shares_budget, "supply_shares_budget")
        self._validate_non_negative(
            min_collateral_per_supply_asset_wad, "min_collateral_per_supply_asset_wad"
        )
        return self._action_raw(
            "enter((bytes32,address,uint256,uint256,uint256))",
            [
                [
                    raw,
                    borrower,
                    supply_shares_budget,
                    max_iterations,
                    min_collateral_per_supply_asset_wad,
                ]
            ],
        )


def min_collateral_per_supply_asset_wad(
    *,
    loan_price: int,
    collateral_price: int,
    loan_decimals: int,
    collateral_decimals: int,
    tolerance_bps: int,
) -> int:
    """The settle rate guard in the fuse's unit: raw collateral-token units per
    1e18 raw loan-token units, floor of
    ``1e18 * p_loan * 10^dec_coll / (p_coll * 10^dec_loan) * (1 - tolerance)``.

    ``loan_price`` and ``collateral_price`` are the vault's own prices of one
    whole token (for example ``Price.amount`` from the price oracle middleware
    for both tokens), in the same scale; the scale cancels. ``*_decimals`` are
    the tokens' raw decimals. ``tolerance_bps`` is required: the core seizes
    rounded down and the fuse compares against the burned supply rounded up, so
    a minimum at exact parity (``0``) can revert on rounding alone. Use a
    non-zero tolerance, for example 50 bps. Never derive the prices from the Lido
    oracle: the core seizes at that rate, so a minimum from it never trips in a depeg.

    Integer arithmetic only. A result that rounds down to zero is refused,
    because zero disables the guard in the fuse; pass ``0`` to ``settle_*``
    explicitly to disable it.
    """
    if loan_price <= 0 or collateral_price <= 0:
        raise ValueError("prices must be greater than zero")
    if loan_decimals < 0 or collateral_decimals < 0:
        raise ValueError("decimals must not be negative")
    if not 0 <= tolerance_bps < _BPS:
        raise ValueError(f"tolerance_bps must be in [0, {_BPS}), got {tolerance_bps}")
    parity = (
        _WAD
        * loan_price
        * 10**collateral_decimals
        // (collateral_price * 10**loan_decimals)
    )
    result = parity * (_BPS - tolerance_bps) // _BPS
    if result == 0:
        raise ValueError(
            "the minimum rounds down to zero, which would disable the guard; "
            "pass 0 explicitly to disable it"
        )
    return result


class LidoLendSubstrates:
    """Typed bytes32 substrate encoders for the Lido Lend market (58).

    Mirrors ``LidoLendSubstrateLib.sol``: a market word is the market id
    itself; a collateral word is ``0x01``, 11 zero bytes and the token address
    (``encodeCollateral``). :func:`ipor_fusion.decode_substrate` inverts both.
    The flash loan market (59) takes plain token addresses.
    """

    @staticmethod
    def market(market_id: MorphoBlueMarketId) -> bytes:
        """The word that grants a market for supply, borrow and settle."""
        return _validate_market_id(market_id)

    @staticmethod
    def collateral(token: ChecksumAddress) -> bytes:
        """The word that grants ``token`` as collateral."""
        return _encode_address_substrate(_COLLATERAL_TAG, token, "token")

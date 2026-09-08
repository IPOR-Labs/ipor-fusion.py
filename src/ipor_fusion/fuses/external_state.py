"""External-state operation fuse (market 50).

Moves off-vault capital through the per-vault ExternalStateExecutor. ``enter``
transfers ``amount`` of ``asset`` from the PlasmaVault to the executor, records
it against ``balance_account``, and runs ``actions`` from the executor context;
``exit`` inverts it (actions first, then the asset is pulled back to the vault).
The classic use is the margin leg of a delta-neutral strategy: an action calls
``USDC.transfer(<venue deposit address>, amount)`` to credit the vault's off-chain
trading account (e.g. Hyperliquid Core). Funding a venue this way means granting
the asset itself as a TARGET, which is unbounded -- see
``ExternalStateSubstrates.target``.

ABI (ExternalStateOperationFuse.sol), same shape for both, semantics invert::

    enter((address asset, uint256 amount, address balanceAccount,
           (address target, bytes data)[] actions))
    exit(( ... ))
"""

from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import (
    Fuse,
    FuseAction,
    _encode_address_substrate,
    _encode_uint248_substrate,
    _substrate_address_bytes,
    _validate_not_zero_address,
    _validate_selector,
)
from ipor_fusion.types import Amount

#: One external call run from the executor context: (target, calldata).
ExternalStateAction = tuple[ChecksumAddress, bytes]


class ExternalStateOperationFuse(Fuse):
    """Fuse for moving off-vault capital via the per-vault ExternalStateExecutor."""

    _TUPLE = "(address,uint256,address,(address,bytes)[])"

    def enter(
        self,
        *,
        asset: ChecksumAddress,
        amount: Amount,
        balance_account: ChecksumAddress,
        actions: list[ExternalStateAction],
    ) -> FuseAction:
        """Transfer ``amount`` of ``asset`` vault->executor, record it against
        ``balance_account``, and run ``actions`` from the executor context.

        ``amount`` may be zero for an actions-only enter; ``asset`` may then be
        the zero address.
        """
        return self._build("enter", asset, amount, balance_account, actions)

    def exit(
        self,
        *,
        asset: ChecksumAddress,
        amount: Amount,
        balance_account: ChecksumAddress,
        actions: list[ExternalStateAction],
    ) -> FuseAction:
        """Run ``actions``, decrement the tracked ``balance_account``, and pull
        ``amount`` of ``asset`` back from the executor to the vault."""
        return self._build("exit", asset, amount, balance_account, actions)

    def _build(
        self,
        method: str,
        asset: ChecksumAddress,
        amount: Amount,
        balance_account: ChecksumAddress,
        actions: list[ExternalStateAction],
    ) -> FuseAction:
        self._validate_non_negative(amount, "amount")
        self._validate_address(balance_account, "balance_account")
        # asset only moves when amount > 0; an actions-only call may omit it.
        if amount > 0:
            self._validate_address(asset, "asset")
        for index, (target, _) in enumerate(actions):
            self._validate_address(target, f"actions[{index}].target")
        encoded_actions = [[target, data] for target, data in actions]
        return self._action_raw(
            f"{method}({self._TUPLE})",
            [[asset, amount, balance_account, encoded_actions]],
        )


class ExternalStateSubstrates:
    """Typed bytes32 substrate encoders for the external-state market (50).

    Mirrors ``ExternalStateSubstrateLib.sol``: each substrate is
    ``bytes32(uint256(type) << 248 | payload)`` -- a one-byte type tag in the
    high byte, then a 31-byte payload whose layout depends on the type.

    The fuse checks these grants directly against the vault substrate set,
    reverting with ``ExternalStateUnsupportedSubstrate`` when one is missing:
    TARGET for every action, ASSET and BALANCE_ACCOUNT only when ``amount``
    is non-zero (an actions-only call moves nothing to account for). The executor
    keeps its own cache on top of that, refreshed by
    ``ExternalStateExecutor.syncSubstrates()``: a newly granted custodian or
    balance account stays unusable until that runs. Revocation is asymmetric --
    a revoked balance account is rejected immediately, since propose/confirm
    check the vault before the cache, while a revoked custodian remains
    authorized on the executor until the next sync.
    """

    _ASSET = 1
    _TARGET = 2
    _CUSTODIAN = 3
    _BALANCE_ACCOUNT = 4
    _STALENESS_MAX = 5
    _BIG_CHANGE_BPS = 6
    _DUST_THRESHOLD = 7
    _MIN_UPDATE_INTERVAL = 8

    @staticmethod
    def _address_substrate(tag: int, address: ChecksumAddress, name: str) -> bytes:
        """Type byte, 11 zero bytes, then the 20-byte address."""
        _validate_not_zero_address(address, name)
        return _encode_address_substrate(tag, address)

    @classmethod
    def asset(cls, asset: ChecksumAddress) -> bytes:
        """Allow ``asset`` to move between the vault and the executor and to be
        accounted for on enter/exit."""
        return cls._address_substrate(cls._ASSET, asset, "asset")

    @classmethod
    def custodian(cls, custodian: ChecksumAddress) -> bytes:
        """Allow ``custodian`` to propose and confirm off-venue balances. A
        proposal and its confirmation must come from two different custodians,
        so grant at least two."""
        return cls._address_substrate(cls._CUSTODIAN, custodian, "custodian")

    @classmethod
    def balance_account(cls, balance_account: ChecksumAddress) -> bytes:
        """Allow ``balance_account`` as the bucket the executor books enter/exit
        amounts against -- the same address the custodians report values for."""
        return cls._address_substrate(
            cls._BALANCE_ACCOUNT, balance_account, "balance_account"
        )

    @classmethod
    def target(cls, target: ChecksumAddress, selector: bytes) -> bytes:
        """Allow the executor to call ``target`` with ``selector`` (a 4-byte
        function selector), e.g. ``transfer`` on the asset to forward funds to a
        venue deposit address.

        Granting a selector on an asset the vault holds is what venue funding
        requires, and it is unbounded: a substrate pins the contract and the
        selector but has no destination field, so an action may send the
        executor's entire balance anywhere. The market's value is a custodian
        attestation rather than an on-chain balance, so such a transfer does not
        move NAV until a custodian reports a different figure. Treat this grant
        as a reason the alpha key must be least-privilege and never shared with
        the owner/atomist. The fuse's trust-assumptions section in the
        ipor-fusion contracts repo covers this and the other properties the
        chain does not enforce.

        Layout: type byte, 7 zero bytes, the 4-byte selector, the 20-byte
        target -- the selector sits *above* the address, unlike the
        async-action market's TARGET substrate.
        """
        _validate_selector(selector)
        _validate_not_zero_address(target, "target")
        return (
            bytes([cls._TARGET])
            + b"\x00" * 7
            + selector
            + _substrate_address_bytes(target)
        )

    @staticmethod
    def _validate_mandatory_singleton(value: int, name: str) -> None:
        """STALENESS_MAX and BIG_CHANGE_BPS are mandatory singletons: the
        executor's cache rebuild reads a zero value as unset and reverts with
        ``ExternalStateMandatorySingletonMissing``, so a zero grant can never be
        part of a working configuration. Negative values fall through to the
        uint248 range check.
        """
        if value == 0:
            raise ValueError(f"{name} must not be zero")

    @classmethod
    def staleness_max(cls, seconds: int) -> bytes:
        """Maximum age of the confirmed balance, in seconds, before user
        operations are blocked. Must be non-zero -- the executor treats a zero
        as a missing grant and refuses to sync its substrate cache."""
        cls._validate_mandatory_singleton(seconds, "staleness max")
        return _encode_uint248_substrate(cls._STALENESS_MAX, seconds, "staleness max")

    @classmethod
    def big_change_bps(cls, bps: int) -> bytes:
        """Balance change, in basis points, above which the balance fuse
        triggers a pause. Must be non-zero, on the same rule as
        ``staleness_max``."""
        cls._validate_mandatory_singleton(bps, "big change bps")
        return _encode_uint248_substrate(cls._BIG_CHANGE_BPS, bps, "big change bps")

    @classmethod
    def dust_threshold(cls, percent: int) -> bytes:
        """Dust-check scaling, as a percent of one token unit (100 = one whole
        token)."""
        return _encode_uint248_substrate(cls._DUST_THRESHOLD, percent, "dust threshold")

    @classmethod
    def min_update_interval(cls, seconds: int) -> bytes:
        """Minimum delay, in seconds, between two confirmed balance updates."""
        return _encode_uint248_substrate(
            cls._MIN_UPDATE_INTERVAL, seconds, "min update interval"
        )

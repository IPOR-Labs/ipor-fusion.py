"""The fuse encoder base: `Fuse`, `StakeFuse` and the shared validators and
substrate packers.

It lives in ``core`` so encoders outside ``ipor_fusion.fuses`` can build on it
without importing that package, whose ``__init__`` loads every encoder.
``ipor_fusion.fuses.base`` re-exports all of it.
"""

from abc import ABC
from collections.abc import Sequence
from types import MemberDescriptorType
from typing import ClassVar

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.core.address import _validate_not_zero_address
from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.core.fuse_action import FuseAction
from ipor_fusion.types import MAX_UINT256, Amount, FuseAddress


class Fuse(ABC):  # noqa: B024  # ABC marks intent; no shared abstract method
    """Abstract base class for all protocol fuse adapters.

    A fuse is a value: two fuses are equal (and hash alike) when they are of the
    same class and carry the same constructor state (the fuse address, plus e.g.
    a `StakeFuse`'s staking contract), so a subclass that stores more state is
    compared on it without overriding anything. That state must be hashable.

    ``SIGNATURES`` lists the Solidity functions a class's own methods encode;
    `signatures` adds the inherited ones. The decoder reads them to recognize an
    action; the test suite fails any call an encoder builds under test that its
    class does not declare.
    """

    SIGNATURES: ClassVar[tuple[str, ...]] = ()

    def __init__(self, address: FuseAddress):
        _validate_not_zero_address(address, "fuse address")
        self._address = address

    @property
    def address(self) -> FuseAddress:
        return self._address

    @classmethod
    def signatures(cls) -> tuple[str, ...]:
        """Every Solidity function this class encodes, inherited ones included."""
        seen: dict[str, None] = {}
        for klass in reversed(cls.__mro__):
            for signature in vars(klass).get("SIGNATURES", ()):
                seen[signature] = None
        return tuple(seen)

    @staticmethod
    def _validate_amount(value: Amount, name: str) -> None:
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero, got {value}")

    @staticmethod
    def _validate_address(value: str, name: str) -> None:
        _validate_not_zero_address(value, name)

    @staticmethod
    def _validate_non_empty_list(value: Sequence[object], name: str) -> None:
        if not value:
            raise ValueError(f"{name} must not be empty")

    @staticmethod
    def _validate_non_negative(value: int, name: str) -> None:
        if value < 0:
            raise ValueError(f"{name} must not be negative, got {value}")

    def _value_state(self) -> tuple[tuple[str, object], ...]:
        """Every attribute the instance stores, in its ``__dict__`` or in a
        subclass's ``__slots__``, sorted by name."""
        state = dict(vars(self))
        # Slot storage is a member descriptor in the declaring class's own
        # namespace (already name-mangled). Reading each one directly, keyed by
        # its class, keeps a base slot shadowed by a subclass attribute apart.
        for klass in type(self).__mro__:
            for name, member in vars(klass).items():
                if not isinstance(member, MemberDescriptorType):
                    continue
                try:
                    value = member.__get__(self, klass)
                except AttributeError:
                    continue
                state[f"{klass.__module__}.{klass.__qualname__}.{name}"] = value
        return tuple(sorted(state.items()))

    def __eq__(self, other: object) -> bool:
        return (
            isinstance(other, Fuse)
            and type(self) is type(other)
            and self._value_state() == other._value_state()
        )

    def __hash__(self) -> int:
        return hash((type(self), *self._value_state()))

    def _action_raw(self, signature: str, values: Sequence[object]) -> FuseAction:
        selector = function_signature_to_4byte_selector(signature)
        abi_types = _parse_param_types(signature)
        data = selector + encode(abi_types, values)
        return FuseAction(fuse=self._address, data=data)


class StakeFuse(Fuse):
    """Base fuse for stake/unstake operations on a staking contract."""

    SIGNATURES = (
        "enter((uint256,address))",
        "exit((uint256,address))",
    )

    def __init__(self, fuse_address: FuseAddress, staking_address: ChecksumAddress):
        super().__init__(fuse_address)
        self._staking_address = staking_address

    def stake(self) -> FuseAction:
        return self._action_raw(
            "enter((uint256,address))",
            [[MAX_UINT256, self._staking_address]],
        )

    def unstake(self, amount: Amount) -> FuseAction:
        self._validate_amount(amount, "amount")
        return self._action_raw(
            "exit((uint256,address))",
            [[amount, self._staking_address]],
        )


def _substrate_address_bytes(address: ChecksumAddress) -> bytes:
    """The 20 raw bytes of a checksum address, for packing into a substrate."""
    payload = bytes.fromhex(address.removeprefix("0x"))
    if len(payload) != 20:
        raise ValueError(f"not a 20-byte address: {address}")
    return payload


def _encode_address_substrate(tag: int, address: ChecksumAddress, name: str) -> bytes:
    """A bytes32 substrate: a one-byte type ``tag``, 11 zero bytes, then the
    20-byte ``address``, which must not be the zero address. ``name`` labels it
    in the error. Markets that permit a zero address do not share this layout
    and pack their own.
    """
    _validate_not_zero_address(address, name)
    return bytes([tag]) + b"\x00" * 11 + _substrate_address_bytes(address)


def _validate_selector(selector: bytes) -> None:
    """A substrate-packed function selector is exactly 4 bytes."""
    if len(selector) != 4:
        raise ValueError(f"selector must be 4 bytes, got {len(selector)}")


def _encode_uint248_substrate(tag: int, value: int, name: str) -> bytes:
    """A bytes32 substrate: a one-byte type ``tag`` then a uint248 ``value``
    (e.g. a WAD fraction). ``name`` labels ``value`` in the range error."""
    if not 0 <= value < (1 << 248):
        raise ValueError(f"{name} out of range: {value}")
    return bytes([tag]) + value.to_bytes(31, "big")

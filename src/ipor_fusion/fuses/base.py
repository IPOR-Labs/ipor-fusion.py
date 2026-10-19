from abc import ABC

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.core.address import _validate_not_zero_address
from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.core.fuse_action import FuseAction
from ipor_fusion.types import MAX_UINT256, Amount, FuseAddress
from ipor_fusion.types import ZERO_ADDRESS as ZERO_ADDRESS


class Fuse(ABC):  # noqa: B024  # ABC marks intent; no shared abstract method
    """Abstract base class for all protocol fuse adapters."""

    def __init__(self, address: FuseAddress):
        _validate_not_zero_address(address, "fuse address")
        self._address = address

    @property
    def address(self) -> FuseAddress:
        return self._address

    @staticmethod
    def _validate_amount(value: Amount, name: str) -> None:
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero, got {value}")

    @staticmethod
    def _validate_address(value: str, name: str) -> None:
        _validate_not_zero_address(value, name)

    @staticmethod
    def _validate_non_empty_list(value: list, name: str) -> None:
        if not value:
            raise ValueError(f"{name} must not be empty")

    @staticmethod
    def _validate_non_negative(value: int, name: str) -> None:
        if value < 0:
            raise ValueError(f"{name} must not be negative, got {value}")

    def __eq__(self, other: object) -> bool:
        return type(self) is type(other) and self._address == other._address  # type: ignore[attr-defined]

    def __hash__(self) -> int:
        return hash((type(self), self._address))

    def _action_raw(self, signature: str, values: list) -> FuseAction:
        selector = function_signature_to_4byte_selector(signature)
        abi_types = _parse_param_types(signature)
        data = selector + encode(abi_types, values)
        return FuseAction(fuse=self._address, data=data)


class StakeFuse(Fuse):
    """Base fuse for stake/unstake operations on a staking contract."""

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

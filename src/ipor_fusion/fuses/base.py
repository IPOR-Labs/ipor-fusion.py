from abc import ABC
from dataclasses import dataclass
from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.types import Amount, TokenId, MAX_UINT256

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass(frozen=True)
class FuseAction:
    """Immutable calldata payload targeting a specific fuse contract."""

    fuse: ChecksumAddress
    data: bytes

    def encode(self) -> bytes:
        return encode(["address", "bytes"], [self.fuse, self.data])

    def __str__(self) -> str:
        return f"FuseAction(fuse={self.fuse}, data=0x{self.data.hex()[:16]}...)"

    def __repr__(self) -> str:
        return self.__str__()

    @staticmethod
    def encode_execute_payload(actions: list["FuseAction"], signature: str) -> bytes:
        bytes_data = [[action.fuse, action.data] for action in actions]
        encoded = encode(["(address,bytes)[]"], [bytes_data])
        return function_signature_to_4byte_selector(signature) + encoded


class Fuse(ABC):
    """Abstract base class for all protocol fuse adapters."""

    def __init__(self, address: ChecksumAddress):
        if not address or address == ZERO_ADDRESS:
            raise ValueError("Fuse address is required and must not be zero address")
        self._address = address

    @property
    def address(self) -> ChecksumAddress:
        return self._address

    @staticmethod
    def _validate_amount(value: Amount, name: str) -> None:
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero, got {value}")

    @staticmethod
    def _validate_address(value: str, name: str) -> None:
        if not value or value == ZERO_ADDRESS:
            raise ValueError(f"{name} must not be zero address")

    @staticmethod
    def _validate_non_empty_list(value: list, name: str) -> None:
        if not value:
            raise ValueError(f"{name} must not be empty")

    @staticmethod
    def _validate_token_id(value: TokenId, name: str) -> None:
        if value < 0:
            raise ValueError(f"{name} must not be negative, got {value}")

    def _action_raw(self, signature: str, values: list) -> FuseAction:
        selector = function_signature_to_4byte_selector(signature)
        abi_types = _parse_param_types(signature)
        data = selector + encode(abi_types, values)
        return FuseAction(fuse=self._address, data=data)


class StakeFuse(Fuse):
    """Base fuse for stake/unstake operations on a staking contract."""

    def __init__(self, fuse_address: ChecksumAddress, staking_address: ChecksumAddress):
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

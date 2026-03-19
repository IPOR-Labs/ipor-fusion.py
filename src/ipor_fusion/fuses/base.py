from abc import ABC
from dataclasses import dataclass
from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.types import MAX_UINT256

ZERO_ADDRESS = "0x0000000000000000000000000000000000000000"


@dataclass(frozen=True)
class FuseAction:
    fuse: ChecksumAddress
    data: bytes

    def encode(self) -> bytes:
        return encode(["address", "bytes"], [self.fuse, self.data])

    def __str__(self) -> str:
        return f"FuseAction(fuse={self.fuse}, data=0x{self.data.hex()[:16]}...)"

    def __repr__(self) -> str:
        return self.__str__()


class Fuse(ABC):
    def __init__(self, address: ChecksumAddress):
        if not address:
            raise ValueError("Fuse address is required")
        self._address = address

    @property
    def address(self) -> ChecksumAddress:
        return self._address

    @staticmethod
    def _validate_amount(value: int, name: str) -> None:
        if value <= 0:
            raise ValueError(f"{name} must be greater than zero, got {value}")

    @staticmethod
    def _validate_address(value: str, name: str) -> None:
        if not value or value == ZERO_ADDRESS:
            raise ValueError(f"{name} must not be zero address")

    def _action_raw(
        self, signature: str, abi_types: list[str], values: list
    ) -> FuseAction:
        selector = function_signature_to_4byte_selector(signature)
        data = selector + encode(abi_types, values)
        return FuseAction(fuse=self._address, data=data)


class StakeFuse(Fuse):
    def __init__(self, fuse_address: ChecksumAddress, staking_address: ChecksumAddress):
        super().__init__(fuse_address)
        self._staking_address = staking_address

    def stake(self) -> FuseAction:
        return self._action_raw(
            "enter((uint256,address))",
            ["uint256", "address"],
            [MAX_UINT256, self._staking_address],
        )

    def unstake(self, amount: int) -> FuseAction:
        self._validate_amount(amount, "amount")
        return self._action_raw(
            "exit((uint256,address))",
            ["uint256", "address"],
            [amount, self._staking_address],
        )

from abc import ABC
from dataclasses import dataclass
from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector


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

    def _action_raw(
        self, signature: str, abi_types: list[str], values: list
    ) -> FuseAction:
        selector = function_signature_to_4byte_selector(signature)
        data = selector + encode(abi_types, values)
        return FuseAction(fuse=self._address, data=data)

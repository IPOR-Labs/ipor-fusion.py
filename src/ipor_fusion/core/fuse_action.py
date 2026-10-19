from dataclasses import dataclass

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector


@dataclass(frozen=True, slots=True)
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

from typing import List

from eth_abi import encode


class FuseAction:
    def __init__(self, fuse: str, data: bytes):
        self.fuse = fuse
        self.data = data

    def encode(self) -> List:
        """
        Convert the structure to a format suitable for ABI encoding.
        """
        return encode(["address", "bytes"], [self.fuse, self.data])

    def __str__(self) -> str:
        return f"FuseActionDynamicStruct(fuse={self.fuse}, data={self.data.hex()})"

    def __repr__(self) -> str:
        return self.__str__()

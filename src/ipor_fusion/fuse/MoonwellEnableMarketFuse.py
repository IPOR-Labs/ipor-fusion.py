from typing import List

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class MoonwellBorrowFuse:
    PROTOCOL_ID = "moonwell"

    def __init__(self, fuse_address: str):
        if not fuse_address:
            raise ValueError("fuseAddress is required")
        self.fuse_address = fuse_address

    def enable(self, m_tokens: List[str]) -> FuseAction:
        fuse_enter_data = MoonwellBorrowFuseEnterData(m_tokens)
        return FuseAction(self.fuse_address, fuse_enter_data.function_call())

    def disable(self, m_tokens: List[str]) -> FuseAction:
        fuse_exit_data = MoonwellBorrowFuseExitData(m_tokens)
        return FuseAction(self.fuse_address, fuse_exit_data.function_call())


class MoonwellBorrowFuseEnterData:
    def __init__(self, m_tokens: List[str]):
        self._m_tokens = m_tokens

    def encode(self) -> bytes:
        return encode(["address[]"], [self._m_tokens])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((address[]))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class MoonwellBorrowFuseExitData:
    def __init__(self, m_tokens: List[str]):
        self._m_tokens = m_tokens

    def encode(self) -> bytes:
        return encode(["address[]"], [self._m_tokens])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("exit((address[]))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()

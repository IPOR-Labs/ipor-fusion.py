from typing import List

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class UniswapV3CollectFuseEnterData:
    def __init__(self, token_ids: List[int]):
        self.token_ids = token_ids

    def encode(self) -> bytes:
        return encode(["(uint256[])"], [[self.token_ids]])

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((uint256[]))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()


class UniswapV3CollectFuse:
    PROTOCOL_ID = "uniswap-v3"

    def __init__(self, uniswap_v3_collect_fuse_address: ChecksumAddress):
        self.uniswap_v3_collect_fuse_address = self._require_non_null(
            uniswap_v3_collect_fuse_address,
            "uniswap_v3_collect_fuse_address is required",
        )

    def collect(self, token_ids: List[int]) -> FuseAction:
        data = UniswapV3CollectFuseEnterData(token_ids)
        return FuseAction(self.uniswap_v3_collect_fuse_address, data.function_call())

    @staticmethod
    def _require_non_null(value, message):
        if value is None:
            raise ValueError(message)
        return value

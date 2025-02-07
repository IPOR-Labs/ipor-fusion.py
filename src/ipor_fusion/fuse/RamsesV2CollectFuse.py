from typing import List

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class RamsesV2CollectFuseEnterData:

    _args_signature = "(uint256[])"
    _function_signature = f"enter({_args_signature})"
    _function_selector = function_signature_to_4byte_selector(_function_signature)

    def __init__(self, token_ids: List[int]):
        self.token_ids = token_ids

    def encode(self) -> bytes:
        return encode([self._args_signature], [[self.token_ids]])

    def function_call(self) -> bytes:
        return self._function_selector + self.encode()


class RamsesV2CollectFuse:
    PROTOCOL_ID = "ramses-v2"

    def __init__(self, ramses_v2_collect_fuse_address: ChecksumAddress):
        self.ramses_v2_collect_fuse_address = self._require_non_null(
            ramses_v2_collect_fuse_address,
            "ramses_v2_collect_fuse_address is required",
        )

    def collect(self, token_ids: List[int]) -> FuseAction:
        data = RamsesV2CollectFuseEnterData(token_ids)
        return FuseAction(self.ramses_v2_collect_fuse_address, data.function_call())

    @staticmethod
    def _require_non_null(value, message):
        if value is None:
            raise ValueError(message)
        return value

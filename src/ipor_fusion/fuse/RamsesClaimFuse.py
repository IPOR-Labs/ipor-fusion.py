from typing import List

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class RamsesClaimFuseData:
    _args_signature = "uint256[],address[][]"
    _function_signature = f"claim({_args_signature})"
    _function_selector = function_signature_to_4byte_selector(_function_signature)
    _arg_types = ["uint256[]", "address[][]"]

    def __init__(self, token_ids: List[int], token_rewards: List[List[str]]):
        self.token_ids = token_ids
        self.token_rewards = token_rewards

    def encode(self) -> bytes:
        return encode(self._arg_types, [self.token_ids, self.token_rewards])

    def function_call(self) -> bytes:
        return self._function_selector + self.encode()


class RamsesClaimFuse:

    def __init__(self, ramses_claim_fuse_address: ChecksumAddress):
        self._ramses_claim_fuse_address = ramses_claim_fuse_address

    def claim(self, token_ids: List[int], token_rewards: List[List[str]]) -> FuseAction:
        data = RamsesClaimFuseData(token_ids, token_rewards)
        return FuseAction(self._ramses_claim_fuse_address, data.function_call())

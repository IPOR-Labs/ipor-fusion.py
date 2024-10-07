from typing import List

from eth_utils import function_signature_to_4byte_selector

from ipor_fusion_sdk.fuse.FuseAction import FuseAction


class RamsesClaimFuse:
    _args_signature = "(uint256[], address[][])"
    _function_signature = f"claim({_args_signature})"
    _function_selector = function_signature_to_4byte_selector(_function_signature)

    def __init__(self, ramses_claim_fuse_address: str):
        self._ramses_claim_fuse_address = ramses_claim_fuse_address

    def claim(self, token_ids: List[int], token_rewards: List[List[str]]) -> FuseAction:
        pass

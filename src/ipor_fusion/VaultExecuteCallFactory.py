from typing import List

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction


class VaultExecuteCallFactory:

    def create_execute_call_from_action(self, action: FuseAction) -> bytes:
        return self.create_execute_call_from_actions([action])

    @staticmethod
    def create_execute_call_from_actions(actions: List[FuseAction]) -> bytes:
        bytes_data = []
        for action in actions:
            bytes_data.append([action.fuse, action.data])
        bytes_ = "(address,bytes)[]"
        encoded_arguments = encode([bytes_], [bytes_data])
        return (
            function_signature_to_4byte_selector("execute((address,bytes)[])")
            + encoded_arguments
        )

    @staticmethod
    def create_claim_rewards_call(claims: List[FuseAction]) -> bytes:
        bytes_data = []
        for action in claims:
            bytes_data.append([action.fuse, action.data])
        bytes_ = "(address,bytes)[]"
        encoded_arguments = encode([bytes_], [bytes_data])
        return (
            function_signature_to_4byte_selector("claimRewards((address,bytes)[])")
            + encoded_arguments
        )

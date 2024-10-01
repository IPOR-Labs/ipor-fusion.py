from typing import List

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion_sdk.fuse.FuseAction import FuseAction


class VaultExecuteCallFactory:
    EXECUTE_FUNCTION_NAME = "execute"

    def create_execute_call_from_action(self, action: FuseAction) -> bytes:
        return self.create_execute_call_from_actions([action])

    def create_execute_call_from_actions(self, actions: List[FuseAction]) -> bytes:
        bytes_data = []
        for action in actions:
            bytes_data.append([action.fuse, action.data])
        encoded_arguments = encode(["(address,bytes)[]"], [bytes_data])
        return self.create_raw_function_call(encoded_arguments)

    def create_raw_function_call(self, encoded_arguments):
        return self.execute_function_call_encoded_sig() + encoded_arguments

    @staticmethod
    def execute_function_call_encoded_sig():
        return function_signature_to_4byte_selector("execute((address,bytes)[])")

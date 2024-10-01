from typing import List, Set

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion_sdk.fuse import Fuse
from ipor_fusion_sdk.fuse.FuseActionDynamicStruct import FuseActionDynamicStruct
from ipor_fusion_sdk.operation.BaseOperation import BaseOperation
from ipor_fusion_sdk.operation.Supply import Supply
from ipor_fusion_sdk.operation.Withdraw import Withdraw


class VaultExecuteCallFactory:
    EXECUTE_FUNCTION_NAME = "execute"
    CLAIM_REWARDS_FUNCTION_NAME = "claimRewards"

    def __init__(self, fuses: Set[Fuse]):
        if fuses is None:
            raise ValueError("fuses is required")
        self.fuses = set(fuses)

    def create_execute_call(self, operations: List[BaseOperation]) -> bytes:
        if operations is None:
            raise ValueError("operations is required")
        if not operations:
            raise ValueError("operations is empty")

        actions = []
        for operation in operations:
            actions.extend(self.create_action_data(operation))

        bytes_data = []

        for action in actions:
            bytes_data.append([action.fuse, action.data])

        encoded_arguments = encode(["(address,bytes)[]"], [bytes_data])

        return self.create_raw_function_call(encoded_arguments)

    def create_execute_call_from_action(self, action: FuseActionDynamicStruct) -> bytes:
        return self.create_execute_call_from_actions([action])

    def create_execute_call_from_actions(
        self, actions: List[FuseActionDynamicStruct]
    ) -> bytes:
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

    def create_action_data(
        self, operation: BaseOperation
    ) -> List[FuseActionDynamicStruct]:
        fuse = self._find_supported_fuse(operation)
        if isinstance(operation, Supply):
            return self._create_supply_action(fuse, operation)
        if isinstance(operation, Withdraw):
            return self._create_withdraw_action(fuse, operation)
        raise NotImplementedError(f"Unsupported operation: {type(operation).__name__}")

    def _find_supported_fuse(self, operation: BaseOperation) -> Fuse:
        fuse = next((f for f in self.fuses if f.supports(operation.market_id())), None)
        if fuse is None:
            raise ValueError(f"Unsupported marketId: {operation.market_id()}")
        return fuse

    @staticmethod
    def _create_supply_action(
        fuse: Fuse, operation: Supply
    ) -> List[FuseActionDynamicStruct]:
        return fuse.create_fuse_enter_action(operation.market_id(), operation.amount())

    @staticmethod
    def _create_withdraw_action(
        fuse: Fuse, operation: Withdraw
    ) -> List[FuseActionDynamicStruct]:
        return fuse.create_fuse_exit_action(operation.market_id(), operation.amount())

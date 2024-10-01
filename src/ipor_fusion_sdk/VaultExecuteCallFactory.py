from typing import List, Set

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3.contract.contract import ContractFunction

from ipor_fusion_sdk.fuse import Fuse
from ipor_fusion_sdk.fuse.FuseActionDynamicStruct import FuseActionDynamicStruct
from ipor_fusion_sdk.operation.BaseOperation import BaseOperation
from ipor_fusion_sdk.operation.Claim import Claim
from ipor_fusion_sdk.operation.ClosePosition import ClosePosition
from ipor_fusion_sdk.operation.Collect import Collect
from ipor_fusion_sdk.operation.DecreasePosition import DecreasePosition
from ipor_fusion_sdk.operation.IncreasePosition import IncreasePosition
from ipor_fusion_sdk.operation.NewPosition import NewPosition
from ipor_fusion_sdk.operation.Supply import Supply
from ipor_fusion_sdk.operation.Swap import Swap
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

    def create_raw_function_call(self, encoded_arguments):
        return self.execute_function_call_encoded_sig() + encoded_arguments

    @staticmethod
    def execute_function_call_encoded_sig():
        return function_signature_to_4byte_selector("execute((address,bytes)[])")

    def create_claim_rewards_call(self, claims: List[Claim]) -> ContractFunction:
        raise NotImplementedError("Not implemented")

    def create_action_data(
        self, operation: BaseOperation
    ) -> List[FuseActionDynamicStruct]:
        fuse = self._find_supported_fuse(operation)
        if isinstance(operation, Supply):
            return self._create_supply_action(fuse, operation)
        if isinstance(operation, Withdraw):
            return self._create_withdraw_action(fuse, operation)
        if isinstance(operation, Swap):
            return self._create_swap_action(fuse, operation)
        if isinstance(operation, NewPosition):
            return self._create_new_position_action(fuse, operation)
        if isinstance(operation, ClosePosition):
            return self._create_close_position_action(fuse, operation)
        if isinstance(operation, DecreasePosition):
            return self._create_decrease_position_action(fuse, operation)
        if isinstance(operation, IncreasePosition):
            return self._create_increase_position_action(fuse, operation)
        if isinstance(operation, Collect):
            return self._create_collect_action(fuse, operation)
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

    @staticmethod
    def _create_swap_action(
        fuse: Fuse, operation: Swap
    ) -> List[FuseActionDynamicStruct]:
        return fuse.create_fuse_swap_action(
            operation.token_in_address(),
            operation.token_out_address(),
            operation.fee(),
            operation.token_in_amount(),
            operation.min_out_amount(),
        )

    @staticmethod
    def _create_new_position_action(
        fuse: Fuse, operation: NewPosition
    ) -> List[FuseActionDynamicStruct]:
        return fuse.create_fuse_new_position_action(
            operation.token0(),
            operation.token1(),
            operation.fee(),
            operation.tick_lower(),
            operation.tick_upper(),
            operation.amount0_desired(),
            operation.amount1_desired(),
            operation.amount0_min(),
            operation.amount1_min(),
            operation.deadline(),
        )

    @staticmethod
    def _create_close_position_action(
        fuse: Fuse, operation: ClosePosition
    ) -> List[FuseActionDynamicStruct]:
        return fuse.create_fuse_close_position_action(operation.token_ids())

    @staticmethod
    def _create_decrease_position_action(
        fuse: Fuse, operation: DecreasePosition
    ) -> List[FuseActionDynamicStruct]:
        return fuse.create_fuse_exit_action(
            token_id=operation.token_id(),
            liquidity=operation.liquidity(),
            amount0_min=operation.amount0_min(),
            amount1_min=operation.amount1_min(),
            deadline=operation.deadline(),
        )

    @staticmethod
    def _create_increase_position_action(
        fuse: Fuse, operation: IncreasePosition
    ) -> List[FuseActionDynamicStruct]:
        return fuse.create_fuse_enter_action(
            token0=operation.token0(),
            token1=operation.token1(),
            token_id=operation.token_id(),
            amount0_desired=operation.amount0_desired(),
            amount1_desired=operation.amount1_desired(),
            amount0_min=operation.amount0_min(),
            amount1_min=operation.amount1_min(),
            deadline=operation.deadline(),
        )

    @staticmethod
    def _create_collect_action(
        fuse: Fuse, operation: Collect
    ) -> List[FuseActionDynamicStruct]:
        return fuse.create_fuse_enter_action(token_ids=operation.token_ids())

    def create_claim_action_data(self, claim: Claim) -> List:
        fuse = next((f for f in self.fuses if f.supports(claim.market_id())), None)
        if fuse is None:
            raise ValueError(f"Unsupported marketId: {claim.market_id()}")
        return fuse.create_fuse_claim_action(claim.market_id())

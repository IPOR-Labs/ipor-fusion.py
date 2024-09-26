from typing import List, Set

from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.contract.contract import ContractFunction

from ipor_fusion_sdk.fuse import Fuse
from ipor_fusion_sdk.fuse.FuseActionDynamicStruct import FuseActionDynamicStruct
from ipor_fusion_sdk.operation.Claim import Claim
from ipor_fusion_sdk.operation.ClosePosition import ClosePosition
from ipor_fusion_sdk.operation.Collect import Collect
from ipor_fusion_sdk.operation.DecreasePosition import DecreasePosition
from ipor_fusion_sdk.operation.NewPosition import NewPosition
from ipor_fusion_sdk.operation.Operation import Operation
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

    def create_execute_call(self, operations: List[Operation]) -> bytes:
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
        if claims is None:
            raise ValueError("claims is required")
        if not claims:
            raise ValueError("claims is empty")

        actions = []
        for claim in claims:
            actions.extend(self.create_claim_action_data(claim))

        input_data = Web3.to_hex(text=str(actions))
        return ContractFunction(self.CLAIM_REWARDS_FUNCTION_NAME, [input_data], [])

    def create_action_data(self, operation: Operation) -> List[FuseActionDynamicStruct]:
        fuse = next((f for f in self.fuses if f.supports(operation.market_id())), None)
        for f in self.fuses:
            print(f.supports(operation.market_id()))

        if fuse is None:
            raise ValueError(f"Unsupported marketId: {operation.market_id()}")

        if isinstance(operation, Supply):
            return fuse.create_fuse_enter_action(
                operation.market_id(), operation.amount()
            )
        if isinstance(operation, Withdraw):
            return fuse.create_fuse_exit_action(
                operation.market_id(), operation.amount()
            )
        if isinstance(operation, Swap):
            return fuse.create_fuse_swap_action(
                operation.token_in_address(),
                operation.token_out_address(),
                operation.fee(),
                operation.token_in_amount(),
                operation.min_out_amount(),
            )
        if isinstance(operation, NewPosition):
            return fuse.create_fuse_enter_action(
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
        if isinstance(operation, ClosePosition):
            return fuse.create_fuse_exit_action(
                operation.token_ids(),
            )
        if isinstance(operation, DecreasePosition):
            return fuse.create_fuse_exit_action(
                token_id=operation.token_id(),
                liquidity=operation.liquidity(),
                amount0_min=operation.amount0_min(),
                amount1_min=operation.amount1_min(),
                deadline=operation.deadline(),
            )
        if isinstance(operation, Collect):
            return fuse.create_fuse_enter_action(token_ids=operation.token_ids())

        raise NotImplementedError(f"Unsupported operation: {type(operation).__name__}")

    def create_claim_action_data(self, claim: Claim) -> List:
        fuse = next((f for f in self.fuses if f.supports(claim.market_id())), None)
        if fuse is None:
            raise ValueError(f"Unsupported marketId: {claim.market_id()}")
        return fuse.create_fuse_claim_action(claim.market_id())

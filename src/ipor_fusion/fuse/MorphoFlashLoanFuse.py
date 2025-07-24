from typing import List

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.types import Amount


class MorphoFlashLoanFuse:
    def __init__(self, fuse_address: ChecksumAddress):
        self._fuse_address = fuse_address

    def flash_loan(
        self, asset: ChecksumAddress, amount: Amount, actions: List[FuseAction]
    ) -> FuseAction:
        if self._fuse_address is None:
            raise ValueError("fuseAddress is required")
        bytes_data = []
        for action in actions:
            bytes_data.append([action.fuse, action.data])
        bytes_ = "(address,bytes)[]"
        encoded_actions = encode([bytes_], [bytes_data])
        morpho_flash_loan_fuse_enter_data = MorphoFlashLoanFuseEnterData(
            asset, amount, encoded_actions
        )
        return FuseAction(
            self._fuse_address, morpho_flash_loan_fuse_enter_data.function_call()
        )


class MorphoFlashLoanFuseEnterData:
    def __init__(
        self,
        token: ChecksumAddress,
        token_amount: Amount,
        callback_fuse_actions_data: bytes,
    ):
        self.token = token
        self.token_amount = token_amount
        self.callback_fuse_actions_data = callback_fuse_actions_data

    def encode(self) -> bytes:
        return encode(
            ["(address,uint256,bytes)"],
            [[self.token, self.token_amount, self.callback_fuse_actions_data]],
        )

    @staticmethod
    def function_selector() -> bytes:
        return function_signature_to_4byte_selector("enter((address,uint256,bytes))")

    def function_call(self) -> bytes:
        return self.function_selector() + self.encode()

from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.TransactionExecutor import TransactionExecutor


class UniswapV3UniversalRouter:

    def __init__(
        self, transaction_executor: TransactionExecutor, universal_router_address: str
    ):
        self._transaction_executor = transaction_executor
        self._universal_router_address = universal_router_address

    def swap(self, token_in, path, amount_in):
        function_selector_0 = function_signature_to_4byte_selector(
            "transfer(address,uint256)"
        )
        function_args_0 = encode(
            ["address", "uint256"],
            [self._universal_router_address, amount_in],
        )
        function_call_0 = function_selector_0 + function_args_0
        self._transaction_executor.execute(token_in, function_call_0)
        path = encode_packed(
            self.generate_types_by_length(len(path)),
            path,
        )
        inputs = [
            encode(
                ["address", "uint256", "uint256", "bytes", "bool"],
                [
                    "0x0000000000000000000000000000000000000001",
                    amount_in,
                    0,
                    path,
                    False,
                ],
            )
        ]
        function_selector_1 = function_signature_to_4byte_selector(
            "execute(bytes,bytes[])"
        )
        function_args_1 = encode(
            ["bytes", "bytes[]"],
            [encode_packed(["bytes1"], [bytes.fromhex("00")]), inputs],
        )
        function_call_1 = function_selector_1 + function_args_1
        self._transaction_executor.execute(
            self._universal_router_address, function_call_1
        )

    @staticmethod
    def generate_types_by_length(length):
        types = []
        for i in range(length):
            if i % 2 == 0:
                types.append("address")
            else:
                types.append("uint24")
        return types

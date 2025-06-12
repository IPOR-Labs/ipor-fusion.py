from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.PlasmaSystem import PlasmaSystem
from ipor_fusion.helpers import Addresses


class UnsupportedPathException(BaseException):
    """Exception raised when requesting an unsupported trading pair."""


class AerodromeSwapHelper:

    @staticmethod
    def get_aerodrome_path(token_in, token_out):
        """
        Generate the encoded path for Aerodrome DEX swaps.

        Aerodrome uses encoded paths that specify:
        - Input token address
        - Fee tier (1 = 0.01% fee)
        - Output token address

        Currently supports only WETH <-> WStETH pairs.
        """
        # Support WETH -> WStETH swap (buying WStETH with WETH)
        if token_in == Addresses.BASE_WETH and token_out == Addresses.BASE_WSTETH:
            return encode_packed(
                ["address", "uint24", "address"],
                [token_in, 1, token_out],  # Fee tier 1 = 0.01%
            )

        # Support WStETH -> WETH swap (selling WStETH for WETH)
        if token_in == Addresses.BASE_WSTETH and token_out == Addresses.BASE_WETH:
            return encode_packed(
                ["address", "uint24", "address"],
                [token_in, 1, token_out],  # Fee tier 1 = 0.01%
            )

        # Reject unsupported token pairs
        raise UnsupportedPathException(f"token_in={token_in}, token_out={token_out}")

    @staticmethod
    def create_aerodrome_swap_exact_input(
        system: PlasmaSystem,
        token_in: ChecksumAddress,
        token_out: ChecksumAddress,
        amount_in: int,
        min_amount_out: int,
        deadline: int,
    ):
        """
        Create a swap action using Aerodrome DEX on Base network.

        Aerodrome is a major DEX on Base providing deep liquidity for token swaps.
        This function constructs the necessary calls to:
        1. Approve the router to spend input tokens
        2. Execute the swap with specified parameters

        Args:
            system: Plasma system interface
            token_in: Address of token to sell
            token_out: Address of token to buy
            amount_in: Exact amount of input tokens to swap
            min_amount_out: Minimum acceptable output (slippage protection)
            deadline: Unix timestamp when swap expires
        """
        # Aerodrome protocol addresses on Base network
        AERODROME_ROUTER_ADDRESS = Web3.to_checksum_address(
            0xBE6D8F0D05CC4BE24D5167A3EF062215BE6D18A5
        )  # Aerodrome swap router contract
        EXECUTOR = Web3.to_checksum_address(
            0x591435C065FCE9713C8B112FCBF5AF98B8975CB3
        )  # Transaction executor address

        # Define target contracts for the swap operation
        targets = [token_in, AERODROME_ROUTER_ADDRESS]

        # CALL 1: Approve router to spend input tokens
        function_selector_0 = function_signature_to_4byte_selector(
            "approve(address,uint256)"
        )
        function_args_0 = encode(
            ["address", "uint256"], [AERODROME_ROUTER_ADDRESS, amount_in]
        )
        function_call_0 = function_selector_0 + function_args_0

        # Construct the swap path for Aerodrome's routing
        # Path encodes: tokenIn -> fee tier -> tokenOut
        path = AerodromeSwapHelper.get_aerodrome_path(token_in, token_out)

        # CALL 2: Execute the actual swap
        function_selector_1 = function_signature_to_4byte_selector(
            "exactInput((bytes,address,uint256,uint256,uint256))"
        )
        function_args_1 = encode(
            ["(bytes,address,uint256,uint256,uint256)"],
            [[path, EXECUTOR, deadline, amount_in, min_amount_out]],
        )
        function_call_1 = function_selector_1 + function_args_1

        # Combine both calls into a single swap action
        data = [function_call_0, function_call_1]

        # Return Universal Token Swapper action that can be executed by Plasma Vault
        return system.universal().swap(
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            targets=targets,
            data=data,
        )

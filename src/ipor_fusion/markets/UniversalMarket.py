from typing import List

from eth_typing import ChecksumAddress

from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.fuse.UniversalTokenSwapperFuse import UniversalTokenSwapperFuse


class UniversalMarket:

    def __init__(
        self,
        chain_id: int,
        transaction_executor: TransactionExecutor,
        universal_token_swapper_fuse_address: ChecksumAddress,
    ):
        if transaction_executor is None:
            raise ValueError("transaction_executor is required")

        self._chain_id = chain_id
        self._transaction_executor = transaction_executor
        self._universal_token_swapper_fuse = UniversalTokenSwapperFuse(
            universal_token_swapper_fuse_address
        )

    def swap(
        self,
        token_in: str,
        token_out: str,
        amount_in: int,
        targets: List[str],
        data: List[bytes],
    ) -> FuseAction:
        if self._universal_token_swapper_fuse is None:
            raise UnsupportedFuseError(
                "UniversalTokenSwapperFuse is not supported by PlasmaVault"
            )

        return self._universal_token_swapper_fuse.swap(
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            targets=targets,
            data=data,
        )

from typing import List

from web3 import Web3

from ipor_fusion.ERC20 import ERC20
from ipor_fusion.FuseMapper import FuseMapper
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.fuse.UniversalTokenSwapperFuse import UniversalTokenSwapperFuse


class UniversalMarket:

    def __init__(
        self,
        chain_id: int,
        fuses: List[str],
        transaction_executor: TransactionExecutor,
        plasma_vault: PlasmaVault,
    ):
        self._chain_id = chain_id
        self._any_fuse_supported = False
        self._transaction_executor = transaction_executor
        self._plasma_vault = plasma_vault
        for fuse in fuses:
            checksum_fuse = Web3.to_checksum_address(fuse)
            if checksum_fuse in FuseMapper.map(
                chain_id=chain_id, fuse_name="UniversalTokenSwapperFuse"
            ):
                self._universal_token_swapper_fuse = UniversalTokenSwapperFuse(
                    checksum_fuse
                )
                self._any_fuse_supported = True

    def is_market_supported(self) -> bool:
        return self._any_fuse_supported

    def swap(
        self,
        token_in: str,
        token_out: str,
        amount_in: int,
        targets: List[str],
        data: List[bytes],
    ) -> FuseAction:
        if not hasattr(self, "_universal_token_swapper_fuse"):
            raise UnsupportedFuseError(
                "UniversalTokenSwapperFuse is not supported by PlasmaVault"
            )

        balance = ERC20(
            transaction_executor=self._transaction_executor, asset_address=token_in
        ).balance_of(self._plasma_vault.address())
        if amount_in > balance:
            raise ValueError(f"Insufficient balance of {token_in} in plasma vault")

        return self._universal_token_swapper_fuse.swap(
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            targets=targets,
            data=data,
        )

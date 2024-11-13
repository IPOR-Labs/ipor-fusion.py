from typing import List

from web3 import Web3

from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.fuse.UniswapV3SwapFuse import UniswapV3SwapFuse


class UniswapV3Market:
    UNISWAP_V3_SWAP_FUSE = Web3.to_checksum_address(
        "0x84C5aB008C66d664681698A9E4536D942B916F89"
    )

    def __init__(self, fuses: List[str]):
        for fuse in fuses:
            checksum_fuse = Web3.to_checksum_address(fuse)
            if checksum_fuse == self.UNISWAP_V3_SWAP_FUSE:
                self._uniswap_v3_swap_fuse = UniswapV3SwapFuse(checksum_fuse)

    def swap(
        self,
        token_in_address: str,
        token_out_address: str,
        fee: int,
        token_in_amount: int,
        min_out_amount: int,
    ) -> FuseAction:
        # Check if _uniswap_v3_swap_fuse is set
        if not hasattr(self, "_uniswap_v3_swap_fuse"):
            raise UnsupportedFuseError(
                "UniswapV3SwapFuse is not supported by PlasmaVault"
            )

        return self._uniswap_v3_swap_fuse.swap(
            token_in_address=token_in_address,
            token_out_address=token_out_address,
            fee=fee,
            token_in_amount=token_in_amount,
            min_out_amount=min_out_amount,
        )

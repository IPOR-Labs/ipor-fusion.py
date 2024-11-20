from typing import List

from web3 import Web3

from ipor_fusion.error.UnsupportedFuseError import UnsupportedFuseError
from ipor_fusion.fuse.FuseAction import FuseAction
from ipor_fusion.fuse.UniversalTokenSwapperFuse import UniversalTokenSwapperFuse


class UniversalMarket:
    UNIVERSAL_TOKEN_SWAPPER_FUSE = Web3.to_checksum_address(
        "0xB052b0D983E493B4D40DeC75A03D21b70b83c2ca"
    )

    def __init__(self, fuses: List[str]):
        self._any_fuse_supported = False
        for fuse in fuses:
            checksum_fuse = Web3.to_checksum_address(fuse)
            if checksum_fuse == self.UNIVERSAL_TOKEN_SWAPPER_FUSE:
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

        return self._universal_token_swapper_fuse.swap(
            token_in=token_in,
            token_out=token_out,
            amount_in=amount_in,
            targets=targets,
            data=data,
        )

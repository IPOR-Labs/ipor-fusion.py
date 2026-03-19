from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import FuseAction
from ipor_fusion.fuses.universal import UniversalTokenSwapperFuse
from ipor_fusion.markets.base import SwapProtocol


class UniversalMarket(SwapProtocol):
    def __init__(self, swap_fuse: ChecksumAddress):
        self._swap_fuse = UniversalTokenSwapperFuse(swap_fuse)

    def swap(
        self,
        token_in: ChecksumAddress,
        token_out: ChecksumAddress,
        amount_in: int,
        min_amount_out: int = 0,
        targets: list[ChecksumAddress] = None,
        data: list[bytes] = None,
        **kwargs,
    ) -> FuseAction:
        if targets is None:
            targets = []
        if data is None:
            data = []
        return self._swap_fuse.swap(token_in, token_out, amount_in, targets, data)

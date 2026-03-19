from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector

from ipor_fusion.fuses.base import Fuse, FuseAction


class UniversalTokenSwapperFuse(Fuse):
    def swap(
        self,
        token_in: ChecksumAddress,
        token_out: ChecksumAddress,
        amount_in: int,
        targets: list[ChecksumAddress],
        data: list[bytes],
    ) -> FuseAction:
        encoded = encode(
            ["(address,address,uint256,(address[],bytes[]))"],
            [[token_in, token_out, amount_in, [targets, data]]],
        )
        selector = function_signature_to_4byte_selector(
            "enter((address,address,uint256,(address[],bytes[])))"
        )
        return FuseAction(fuse=self._address, data=selector + encoded)

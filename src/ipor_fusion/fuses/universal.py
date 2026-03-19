from eth_typing import ChecksumAddress

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
        self._validate_address(token_in, "token_in")
        self._validate_address(token_out, "token_out")
        self._validate_amount(amount_in, "amount_in")
        return self._action_raw(
            "enter((address,address,uint256,(address[],bytes[])))",
            ["(address,address,uint256,(address[],bytes[]))"],
            [[token_in, token_out, amount_in, [targets, data]]],
        )

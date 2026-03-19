from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import Fuse, FuseAction
from ipor_fusion.types import Amount


class UniversalTokenSwapperFuse(Fuse):
    def swap(
        self,
        token_in: ChecksumAddress,
        token_out: ChecksumAddress,
        amount_in: Amount,
        targets: list[ChecksumAddress],
        data: list[bytes],
    ) -> FuseAction:
        self._validate_address(token_in, "token_in")
        self._validate_address(token_out, "token_out")
        self._validate_amount(amount_in, "amount_in")
        if len(targets) != len(data):
            raise ValueError(
                f"targets and data must have the same length, got {len(targets)} and {len(data)}"
            )
        return self._action_raw(
            "enter((address,address,uint256,(address[],bytes[])))",
            [[token_in, token_out, amount_in, [targets, data]]],
        )

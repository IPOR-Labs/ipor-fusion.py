from eth_typing import ChecksumAddress

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.types import Amount


class CompoundV3Reader(ContractWrapper):
    """Reader for Compound V3 (Comet) on-chain state."""

    def balance_of(self, account: ChecksumAddress) -> Call[Amount]:
        return self._view(
            "balanceOf(address)", account, output_types=["uint256"], decoder=Amount
        )

    def borrow_balance_of(self, account: ChecksumAddress) -> Call[Amount]:
        return self._view(
            "borrowBalanceOf(address)",
            account,
            output_types=["uint256"],
            decoder=Amount,
        )

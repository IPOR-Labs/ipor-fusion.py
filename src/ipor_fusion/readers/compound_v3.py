from eth_abi import decode
from eth_typing import ChecksumAddress

from ipor_fusion.core.contract import ContractWrapper
from ipor_fusion.types import Amount


class CompoundV3Reader(ContractWrapper):
    """Reader for Compound V3 (Comet) on-chain state."""

    def balance_of(self, account: ChecksumAddress) -> Amount:
        raw = self._call("balanceOf(address)", account)
        (value,) = decode(["uint256"], raw)
        return Amount(value)

    def borrow_balance_of(self, account: ChecksumAddress) -> Amount:
        raw = self._call("borrowBalanceOf(address)", account)
        (value,) = decode(["uint256"], raw)
        return Amount(value)

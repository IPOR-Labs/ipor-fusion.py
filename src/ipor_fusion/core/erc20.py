from eth_abi import decode
from eth_typing import ChecksumAddress
from web3.types import TxReceipt

from ipor_fusion.core.contract import ContractWrapper
from ipor_fusion.types import Amount, Decimals


class ERC20(ContractWrapper):
    """Standard ERC-20 token interface wrapper."""

    def transfer(self, to: ChecksumAddress, amount: Amount) -> TxReceipt:
        return self._send("transfer(address,uint256)", to, amount)

    def approve(self, spender: ChecksumAddress, amount: Amount) -> TxReceipt:
        return self._send("approve(address,uint256)", spender, amount)

    def balance_of(self, account: ChecksumAddress) -> Amount:
        (value,) = decode(["uint256"], self._call("balanceOf(address)", account))
        return value

    def decimals(self) -> Decimals:
        (value,) = decode(["uint256"], self._call("decimals()"))
        return value

    def symbol(self) -> str:
        (value,) = decode(["string"], self._call("symbol()"))
        return value

    def name(self) -> str:
        (value,) = decode(["string"], self._call("name()"))
        return value

    def total_supply(self) -> Amount:
        (value,) = decode(["uint256"], self._call("totalSupply()"))
        return value

    def allowance(self, owner: ChecksumAddress, spender: ChecksumAddress) -> Amount:
        (value,) = decode(
            ["uint256"], self._call("allowance(address,address)", owner, spender)
        )
        return value

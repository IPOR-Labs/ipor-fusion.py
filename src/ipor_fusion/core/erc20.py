from eth_abi import encode, decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.core.context import Web3Context
from ipor_fusion.types import Amount, Decimals


class ERC20Token:
    """Simple ERC20 token reference (address only, no context)."""

    def __init__(self, address: ChecksumAddress):
        self._address = Web3.to_checksum_address(address)

    @property
    def address(self) -> ChecksumAddress:
        return self._address


class ERC20:

    def __init__(self, ctx: Web3Context, address: ChecksumAddress):
        self._ctx = ctx
        self._address = Web3.to_checksum_address(address)

    @property
    def address(self) -> ChecksumAddress:
        return self._address

    def transfer(self, to: ChecksumAddress, amount: Amount) -> TxReceipt:
        sig = function_signature_to_4byte_selector("transfer(address,uint256)")
        data = sig + encode(["address", "uint256"], [to, amount])
        return self._ctx.send(self._address, data)

    def approve(self, spender: ChecksumAddress, amount: Amount) -> TxReceipt:
        sig = function_signature_to_4byte_selector("approve(address,uint256)")
        data = sig + encode(["address", "uint256"], [spender, amount])
        return self._ctx.send(self._address, data)

    def balance_of(self, account: ChecksumAddress) -> Amount:
        sig = function_signature_to_4byte_selector("balanceOf(address)")
        result = self._ctx.call(self._address, sig + encode(["address"], [account]))
        (value,) = decode(["uint256"], result)
        return value

    def decimals(self) -> Decimals:
        sig = function_signature_to_4byte_selector("decimals()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["uint256"], result)
        return value

    def symbol(self) -> str:
        sig = function_signature_to_4byte_selector("symbol()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["string"], result)
        return value

    def name(self) -> str:
        sig = function_signature_to_4byte_selector("name()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["string"], result)
        return value

    def total_supply(self) -> Amount:
        sig = function_signature_to_4byte_selector("totalSupply()")
        result = self._ctx.call(self._address, sig)
        (value,) = decode(["uint256"], result)
        return value

    def allowance(self, owner: ChecksumAddress, spender: ChecksumAddress) -> Amount:
        sig = function_signature_to_4byte_selector("allowance(address,address)")
        result = self._ctx.call(
            self._address, sig + encode(["address", "address"], [owner, spender])
        )
        (value,) = decode(["uint256"], result)
        return value

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.core.context import Web3Context


class ContractWrapper:
    """Base wrapper for encoding and dispatching Solidity function calls."""

    def __init__(self, ctx: Web3Context, address: ChecksumAddress):
        self._ctx = ctx
        self._address = Web3.to_checksum_address(address)

    @property
    def address(self) -> ChecksumAddress:
        return self._address

    def _encode(self, signature: str, *args) -> bytes:
        selector = function_signature_to_4byte_selector(signature)
        types = _parse_param_types(signature)
        return selector + encode(types, list(args)) if types else selector

    def _call(self, signature: str, *args) -> bytes:
        return self._ctx.call(self._address, self._encode(signature, *args))

    def _send(self, signature: str, *args) -> TxReceipt:
        return self._ctx.send(self._address, self._encode(signature, *args))


def _parse_param_types(signature: str) -> list[str]:
    if not (params := signature[signature.index("(") + 1 : signature.rindex(")")]):
        return []
    result = []
    depth = 0
    current: list[str] = []
    for char in params:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            result.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if last := "".join(current).strip():
        result.append(last)
    return result

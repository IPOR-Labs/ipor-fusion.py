"""A contract must not be able to make the SDK send an HTTP request.

EIP-3668 lets a contract revert with `OffchainLookup(urls, ...)`, which web3
follows by default from inside `eth.call`. Any address a caller passes is
therefore a request to a URL chosen by whoever deployed the contract.
"""

from typing import Any
from unittest.mock import patch

import pytest
from eth_abi import encode
from eth_typing import URI
from web3 import Web3
from web3.providers.base import BaseProvider
from web3.types import RPCEndpoint, RPCResponse

from ipor_fusion import NotPlasmaVaultError, Web3Context
from ipor_fusion.core.access import resolve_access_manager
from ipor_fusion.errors import get_revert_reason

ADDRESS = Web3.to_checksum_address("0x" + "11" * 20)
OFFCHAIN_LOOKUP_SELECTOR = bytes.fromhex("556f1830")
LOOKUP_URL = "https://192.0.2.1/lookup"


def _offchain_lookup_revert() -> str:
    args = encode(
        ["address", "string[]", "bytes", "bytes4", "bytes"],
        [ADDRESS, [LOOKUP_URL], b"", b"\xd8\xd7\x86\x9d", b""],
    )
    return "0x" + (OFFCHAIN_LOOKUP_SELECTOR + args).hex()


class _LookupProvider(BaseProvider):
    """A chain where `ADDRESS` has code and reverts every call with a lookup."""

    def make_request(self, method: RPCEndpoint, params: Any) -> RPCResponse:
        if method == "eth_call":
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {
                    "code": 3,
                    "message": "execution reverted",
                    "data": _offchain_lookup_revert(),
                },
            }
        results: dict[str, Any] = {
            "eth_chainId": "0x1",
            "eth_getCode": "0x6000",
            "eth_getTransactionByHash": {
                "blockHash": "0x" + "22" * 32,
                "blockNumber": "0x1",
                "from": ADDRESS,
                "gas": "0x5208",
                "gasPrice": "0x1",
                "hash": "0x" + "33" * 32,
                "input": "0x",
                "nonce": "0x0",
                "to": ADDRESS,
                "transactionIndex": "0x0",
                "value": "0x0",
                "type": "0x0",
                "v": "0x1b",
                "r": "0x1",
                "s": "0x1",
            },
        }
        return {"jsonrpc": "2.0", "id": 1, "result": results[method]}

    def endpoint_uri(self) -> URI:
        return URI("test://lookup")


def _web3() -> Web3:
    return Web3(_LookupProvider())


def test_context_built_on_any_web3_does_not_follow_a_lookup() -> None:
    ctx = Web3Context(web3=_web3(), chain_id=1)

    with patch("requests.Session.request") as request:
        with pytest.raises(NotPlasmaVaultError):
            resolve_access_manager(ctx, ADDRESS)

    request.assert_not_called()


def test_revert_replay_does_not_follow_a_lookup() -> None:
    receipt: Any = {"blockNumber": 1}

    with patch("requests.Session.request") as request:
        get_revert_reason(_web3(), bytes.fromhex("33" * 32), receipt)

    request.assert_not_called()

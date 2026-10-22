"""A JSON-RPC provider over a fixed chain: every request a `Web3` makes goes
through web3's own request and result formatting, so the SDK code above it
runs unpatched.

`FakeRpc(chain_id, block, timestamp, handler, storage)` answers ``eth_call``
with ``handler(to, calldata)`` (Multicall3 ``aggregate3`` batches are unpacked
by `_multicall.multicall_aware`; a handler raising `ContractLogicError` is a
revert), ``eth_getStorageAt`` from ``storage`` (zero elsewhere),
``eth_getCode`` with code everywhere, ``eth_getLogs`` with no logs, and the
block queries with one fixed block. Any other method fails the test.
"""

from __future__ import annotations

from collections.abc import Callable, Mapping

from _multicall import multicall_aware
from web3 import Web3
from web3.exceptions import ContractLogicError
from web3.providers import BaseProvider
from web3.types import RPCEndpoint, RPCResponse

Handler = Callable[[str, bytes], bytes]


class FakeRpc(BaseProvider):
    def __init__(
        self,
        *,
        chain_id: int,
        block: int,
        timestamp: int,
        handler: Handler,
        storage: Mapping[tuple[str, int], bytes] | None = None,
    ) -> None:
        super().__init__()
        self._chain_id = chain_id
        self._block = block
        self._timestamp = timestamp
        self._call = multicall_aware(handler)
        self._storage = {
            (Web3.to_checksum_address(address), slot): value
            for (address, slot), value in (storage or {}).items()
        }
        self.methods: list[str] = []

    def is_connected(self, show_traceback: bool = False) -> bool:
        return True

    def make_request(self, method: RPCEndpoint, params: object) -> RPCResponse:
        self.methods.append(method)
        assert isinstance(params, list | tuple)
        try:
            result = self._answer(method, list(params))
        except ContractLogicError as exc:
            return {
                "jsonrpc": "2.0",
                "id": 1,
                "error": {"code": 3, "message": f"execution reverted: {exc}"},
            }
        return {"jsonrpc": "2.0", "id": 1, "result": result}

    def _answer(self, method: str, params: list[object]) -> object:  # noqa: C901
        if method == "eth_chainId":
            return hex(self._chain_id)
        if method == "eth_blockNumber":
            return hex(self._block)
        if method in ("eth_getBlockByNumber", "eth_getBlockByHash"):
            return self._block_object()
        if method == "eth_call":
            (tx, _block, *_) = params
            assert isinstance(tx, dict)
            data = bytes.fromhex(str(tx.get("data") or tx.get("input"))[2:])
            return "0x" + bytes(self._call(str(tx["to"]), data)).hex()
        if method == "eth_getStorageAt":
            address, slot, _block = params
            key = (Web3.to_checksum_address(str(address)), int(str(slot), 16))
            return "0x" + self._storage.get(key, bytes(32)).rjust(32, b"\0").hex()
        if method == "eth_getCode":
            return "0x6080"
        if method == "eth_getLogs":
            return []
        raise AssertionError(f"unexpected RPC method {method} {params}")

    def _block_object(self) -> dict[str, object]:
        return {
            "number": hex(self._block),
            "hash": "0x" + "ab" * 32,
            "parentHash": "0x" + "aa" * 32,
            "timestamp": hex(self._timestamp),
            "transactions": [],
            "gasLimit": hex(30_000_000),
            "gasUsed": "0x0",
            "baseFeePerGas": "0x1",
            "miner": "0x" + "00" * 20,
            "difficulty": "0x0",
            "extraData": "0x",
            "logsBloom": "0x" + "00" * 256,
            "nonce": "0x" + "00" * 8,
            "receiptsRoot": "0x" + "00" * 32,
            "sha3Uncles": "0x" + "00" * 32,
            "size": "0x0",
            "stateRoot": "0x" + "00" * 32,
            "transactionsRoot": "0x" + "00" * 32,
            "uncles": [],
        }

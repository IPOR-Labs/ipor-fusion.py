"""Exact code identity of the reconstructed, test-only Arbitrum/HyperEVM pilot.

The manifest records byte equivalence, not the deployer's original git checkout.
Its shortened governance/recovery constants must never become production defaults.
"""

import json
from pathlib import Path
from typing import Any

from eth_abi import decode, encode
from web3 import Web3

PILOT_BUILD = json.loads(
    (Path(__file__).parent / "fixtures" / "ccip_pilot_build.json").read_text()
)


def assert_pilot_code(web3: Web3, chain_id: int, block: int) -> None:
    chain = PILOT_BUILD["chains"][str(chain_id)]
    factory = Web3.to_checksum_address(PILOT_BUILD["factory"]["address"])
    _assert_runtime(web3, factory, block, chain)
    for library in PILOT_BUILD["libraries"]:
        _assert_runtime(web3, library["address"], block, library)
    for kind, store in PILOT_BUILD["creation_stores"].items():
        _assert_creation_store(web3, block, kind, store)


def _assert_runtime(
    web3: Web3, address: str, block: int, expected: dict[str, Any]
) -> None:
    code = web3.eth.get_code(Web3.to_checksum_address(address), block_identifier=block)
    assert len(code) == expected.get("runtime_size", expected.get("size")), (
        f"pilot runtime size changed at {address}; review the build before re-pinning"
    )
    assert Web3.keccak(code) == bytes.fromhex(expected["runtime_keccak"][2:]), (
        f"pilot runtime hash changed at {address}; review the build before re-pinning"
    )


def _assert_creation_store(
    web3: Web3, block: int, kind: str, store: dict[str, Any]
) -> None:
    factory = Web3.to_checksum_address(PILOT_BUILD["factory"]["address"])
    packed = int.from_bytes(
        web3.eth.get_storage_at(factory, store["length_slot"], block_identifier=block),
        "big",
    )
    size = (packed >> (8 * store["length_offset"])) & (2**32 - 1)
    assert size == store["size"], f"{kind} stored creation-code length changed"
    chunks = []
    for address, slot in zip(store["addresses"], store["address_slots"], strict=True):
        pointer = web3.eth.get_storage_at(factory, slot, block_identifier=block)
        assert pointer == bytes(12) + bytes.fromhex(address[2:]), (
            f"{kind} creation-code store address changed"
        )
        code = web3.eth.get_code(
            Web3.to_checksum_address(address), block_identifier=block
        )
        assert code[:1] == b"\x00", f"{kind} code store lacks STOP prefix"
        chunks.append(code[1:])
    creation = b"".join(chunks)
    assert len(creation) == size
    assert Web3.keccak(creation) == bytes.fromhex(store["creation_keccak"][2:]), (
        f"{kind} stored creation-code hash changed; review the build before re-pinning"
    )


def assert_pilot_deployment(web3: Web3, chain_id: int) -> None:
    chain = PILOT_BUILD["chains"][str(chain_id)]
    tx = web3.eth.get_transaction(chain["deployment_transaction"])
    receipt = web3.eth.get_transaction_receipt(chain["deployment_transaction"])
    assert receipt["status"] == 1
    assert tx["blockNumber"] == receipt["blockNumber"] == chain["deployment_block"]
    assert tx["from"].lower() == chain["owner"].lower()
    assert tx["to"] == chain["create3_anchor"]
    payload = bytes(tx["input"])
    assert payload[:4] == Web3.keccak(text="deploy(bytes32,bytes)")[:4]
    salt, init_code = decode(["bytes32", "bytes"], payload[4:])
    assert salt == bytes.fromhex(chain["salt"][2:])
    assert init_code[-64:] == encode(
        ["address", "address"], [chain["router"], chain["owner"]]
    )
    creation = init_code[:-64]
    assert len(creation) == PILOT_BUILD["factory"]["creation_size"]
    assert Web3.keccak(creation) == bytes.fromhex(
        PILOT_BUILD["factory"]["creation_keccak"][2:]
    )

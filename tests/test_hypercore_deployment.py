"""Read-only identity checks for the broadcast HyperEVM market-55 contracts."""

import json
import os
from pathlib import Path

import pytest
from web3 import Web3

from ipor_fusion.readers.hypercore import HYPERCORE_PENDING_READERS

FIXTURE = json.loads(
    (Path(__file__).parent / "fixtures" / "hypercore_market55.json").read_text()
)
FUSE_NAMES = frozenset(name for name in FIXTURE["contracts"] if name.endswith("Fuse"))


def test_default_pending_reader_matches_broadcast() -> None:
    expected = FIXTURE["contracts"]["HyperCorePendingReader"]["address"]
    assert HYPERCORE_PENDING_READERS[FIXTURE["chain_id"]] == expected


def _read_address(web3: Web3, target: str, signature: str) -> str:
    result = web3.eth.call(
        {"to": target, "data": Web3.keccak(text=signature)[:4]},
        block_identifier=FIXTURE["snapshot_block"],
    )
    return Web3.to_checksum_address(result[-20:])


@pytest.fixture(scope="module")
def hyper_web3() -> Web3:
    url = os.environ.get("HYPEREVM_PROVIDER_URL")
    if not url:
        pytest.skip("HYPEREVM_PROVIDER_URL not set")
    web3 = Web3(Web3.HTTPProvider(url, request_kwargs={"timeout": 30}))
    if not web3.is_connected():
        pytest.skip("HYPEREVM_PROVIDER_URL is not reachable")
    assert web3.eth.chain_id == FIXTURE["chain_id"]
    return web3


@pytest.mark.parametrize("name", list(FIXTURE["contracts"]))
def test_market55_contract_identity(hyper_web3: Web3, name: str) -> None:
    item = FIXTURE["contracts"][name]
    address = Web3.to_checksum_address(item["address"])
    block = FIXTURE["snapshot_block"]
    assert hyper_web3.eth.get_code(address, block_identifier=block)
    if name not in FUSE_NAMES:
        return
    market_id = hyper_web3.eth.call(
        {"to": address, "data": Web3.keccak(text="MARKET_ID()")[:4]},
        block_identifier=block,
    )
    version = hyper_web3.eth.call(
        {"to": address, "data": Web3.keccak(text="VERSION()")[:4]},
        block_identifier=block,
    )
    assert int.from_bytes(market_id) == 55
    assert Web3.to_checksum_address(version[-20:]) == address


@pytest.mark.parametrize("name", list(FIXTURE["contracts"]))
def test_market55_creation(hyper_web3: Web3, name: str) -> None:
    item = FIXTURE["contracts"][name]
    tx = hyper_web3.eth.get_transaction(item["creation_tx"])
    receipt = hyper_web3.eth.get_transaction_receipt(item["creation_tx"])
    assert tx["from"] == FIXTURE["deployer"]
    assert receipt["status"] == 1
    assert receipt["blockNumber"] <= FIXTURE["snapshot_block"]
    if item.get("internal_create"):
        reporter = FIXTURE["contracts"]["HyperCoreSettlementReporter"]["address"]
        assert receipt["contractAddress"] == reporter
        actual = hyper_web3.eth.call(
            {"to": reporter, "data": Web3.keccak(text="settlementFuse()")[:4]},
            block_identifier=FIXTURE["snapshot_block"],
        )
        assert Web3.to_checksum_address(actual[-20:]) == item["address"]
    else:
        assert receipt["contractAddress"] == item["address"]


def test_market55_vault_bound_contracts(hyper_web3: Web3) -> None:
    contracts = FIXTURE["contracts"]
    reporter = contracts["HyperCoreSettlementReporter"]["address"]
    settlement_fuse = contracts["HyperCoreSettlementFuse"]["address"]
    pending_hook = contracts["HyperCorePendingActionPreHook"]["address"]
    assert _read_address(hyper_web3, reporter, "vault()") == FIXTURE["bound_vault"]
    assert _read_address(hyper_web3, reporter, "settlementFuse()") == settlement_fuse
    assert (
        _read_address(hyper_web3, settlement_fuse, "VAULT()") == FIXTURE["bound_vault"]
    )
    assert _read_address(hyper_web3, settlement_fuse, "REPORTER()") == reporter
    assert _read_address(hyper_web3, pending_hook, "settlementReporter()") == reporter

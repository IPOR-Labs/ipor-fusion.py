"""Empty simulated blocks keep their overrides. A block with no calls is not
sent to ``eth_simulateV1``, so whatever was set on it (a funded address, a
time shift) folds into the next block that is sent: an address funded before
``next_block`` must still be funded when it first acts."""

from __future__ import annotations

from unittest.mock import MagicMock

from web3 import Web3

from ipor_fusion import VaultSimulator
from ipor_fusion.core.contract import Call

VAULT = Web3.to_checksum_address("0x" + "11" * 20)
ALPHA = Web3.to_checksum_address("0x" + "22" * 20)
PAYER = Web3.to_checksum_address("0x" + "33" * 20)
BASELINE = 1_790_000_000


class RecordingProvider:
    def __init__(self) -> None:
        self.payloads: list = []

    def make_request(self, method, params):
        assert method == "eth_simulateV1"
        self.payloads.append(params)
        calls = [c for block in params[0]["blockStateCalls"] for c in block["calls"]]
        ok = {"status": "0x1", "returnData": "0x", "gasUsed": "0x0", "logs": []}
        return {"result": [{"calls": [dict(ok) for _ in calls]}]}


def _simulator() -> tuple[VaultSimulator, RecordingProvider]:
    web3 = MagicMock()
    web3.provider = RecordingProvider()
    web3.eth.get_block.return_value = {"timestamp": BASELINE}
    return VaultSimulator(web3, vault=VAULT, alpha=ALPHA, block=100), web3.provider


def _read() -> Call:
    return Call(to=VAULT, data=b"\x00" * 4, output_types=["uint256"])


def _sent_blocks(provider: RecordingProvider) -> list[dict]:
    return provider.payloads[0][0]["blockStateCalls"]


def test_overrides_on_an_empty_block_fold_into_the_next_sent_block():
    sim, provider = _simulator()
    sim.with_state_override(PAYER, balance=hex(10**18))
    sim.next_block(time_shift_seconds=60)
    sim.observe("later", _read())
    sim.run()
    (entry,) = _sent_blocks(provider)
    assert entry["stateOverrides"] == {PAYER: {"balance": hex(10**18)}}
    assert entry["blockOverrides"] == {"time": hex(BASELINE + 60)}


def test_the_sent_block_wins_and_addresses_merge():
    sim, provider = _simulator()
    sim.with_state_override(PAYER, balance=hex(1), nonce=hex(7))
    sim.with_block_time_shift(10)
    sim.next_block(time_shift_seconds=60)
    sim.with_state_override(PAYER, balance=hex(2))
    sim.observe("later", _read())
    sim.run()
    (entry,) = _sent_blocks(provider)
    assert entry["stateOverrides"] == {PAYER: {"balance": hex(2), "nonce": hex(7)}}
    assert entry["blockOverrides"] == {"time": hex(BASELINE + 10 + 60)}


def test_a_sent_block_does_not_leak_its_overrides_forward():
    sim, provider = _simulator()
    sim.with_state_override(PAYER, balance=hex(1))
    sim.observe("first", _read())
    sim.next_block()
    sim.observe("second", _read())
    sim.run()
    first, second = _sent_blocks(provider)
    assert first["stateOverrides"] == {PAYER: {"balance": hex(1)}}
    assert "stateOverrides" not in second
    assert "blockOverrides" not in second


TOKEN = Web3.to_checksum_address("0x" + "aa" * 20)
PROBE = 0x1234_5678_9ABC


def _mapping_key(holder: str, slot: int) -> str:
    from eth_abi import encode
    from eth_utils import keccak

    return "0x" + keccak(encode(["address", "uint256"], [holder, slot])).hex()


class FiatTokenProvider(RecordingProvider):
    """A token whose balances live at storage slot 9: ``balanceOf`` answers
    the overridden value only when the stateDiff key is the slot-9 entry."""

    def make_request(self, method, params):
        self.payloads.append(params)
        results = []
        for block in params[0]["blockStateCalls"]:
            diff = block.get("stateOverrides", {}).get(TOKEN, {}).get("stateDiff", {})
            for call in block["calls"]:
                holder = Web3.to_checksum_address("0x" + call["input"][-40:])
                value = int(diff.get(_mapping_key(holder, 9), "0x0"), 16)
                results.append(
                    {
                        "status": "0x1",
                        "returnData": "0x" + value.to_bytes(32, "big").hex(),
                        "gasUsed": "0x0",
                        "logs": [],
                    }
                )
        return {"result": [{"calls": results}]}


def test_with_erc20_balance_overrides_the_mapping_entry_and_keeps_other_fields():
    sim, provider = _simulator()
    sim.with_state_override(TOKEN, balance=hex(1))
    sim.with_erc20_balance(TOKEN, PAYER, 5, slot=9)
    sim.with_erc20_balance(TOKEN, ALPHA, 7, slot=9)
    sim.observe("later", _read())
    sim.run()
    (entry,) = _sent_blocks(provider)
    assert entry["stateOverrides"][TOKEN] == {
        "balance": hex(1),
        "stateDiff": {
            _mapping_key(PAYER, 9): "0x" + (5).to_bytes(32, "big").hex(),
            _mapping_key(ALPHA, 9): "0x" + (7).to_bytes(32, "big").hex(),
        },
    }


def test_erc20_balance_slot_probes_until_balance_of_reflects_the_override():
    from ipor_fusion import erc20_balance_slot

    web3 = MagicMock()
    web3.provider = FiatTokenProvider()
    web3.eth.get_block.return_value = {"timestamp": BASELINE}
    assert erc20_balance_slot(web3, TOKEN, block=100) == 9
    assert len(web3.provider.payloads) == 10  # slots 0..9, one simulate each
    import pytest

    with pytest.raises(ValueError, match="no balances mapping slot"):
        erc20_balance_slot(web3, TOKEN, block=100, max_slot=8)

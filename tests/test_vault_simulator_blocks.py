"""Empty simulated blocks keep their overrides. A block with no calls is not
sent to ``eth_simulateV1``, so whatever was set on it (a funded address, a
time shift) folds into the next block that is sent: an address funded before
``next_block`` must still be funded when it first acts."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
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


def test_run_rejects_missing_call_result():
    sim, provider = _simulator()
    sim.observe("missing", _read())
    provider.make_request = MagicMock(return_value={"result": [{"calls": []}]})

    with pytest.raises(
        RuntimeError, match="eth_simulateV1 returned 0 call results for 1 queued calls"
    ):
        sim.run()


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


STORAGE_A = "0x" + "01" * 32
STORAGE_B = "0x" + "02" * 32
VALUE_A = "0x" + "0a" * 32
VALUE_B = "0x" + "0b" * 32


@pytest.mark.parametrize(
    ("earlier", "later", "expected"),
    [
        (
            {"stateDiff": {STORAGE_A: VALUE_A}},
            {"stateDiff": {STORAGE_B: VALUE_B}},
            {"stateDiff": {STORAGE_A: VALUE_A, STORAGE_B: VALUE_B}},
        ),
        (
            {"state": {STORAGE_A: VALUE_A}},
            {"stateDiff": {STORAGE_B: VALUE_B}},
            {"state": {STORAGE_A: VALUE_A, STORAGE_B: VALUE_B}},
        ),
        (
            {"stateDiff": {STORAGE_A: VALUE_A}},
            {"state": {STORAGE_B: VALUE_B}},
            {"state": {STORAGE_B: VALUE_B}},
        ),
        (
            {"state": {STORAGE_A: VALUE_A}},
            {"state": {STORAGE_B: VALUE_B}},
            {"state": {STORAGE_B: VALUE_B}},
        ),
    ],
)
def test_storage_overrides_compose_when_an_empty_block_folds_forward(
    earlier, later, expected
):
    sim, provider = _simulator()
    sim.with_state_override(TOKEN, **earlier)
    sim.next_block()
    sim.with_state_override(TOKEN, **later)
    sim.observe("later", _read())
    sim.run()
    (entry,) = _sent_blocks(provider)
    assert entry["stateOverrides"][TOKEN] == expected


def test_one_account_override_cannot_mix_state_and_state_diff():
    sim, _ = _simulator()
    with pytest.raises(ValueError, match="cannot contain both state and stateDiff"):
        sim.with_state_override(TOKEN, state={}, stateDiff={})


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
    assert first["blockOverrides"] == {"time": hex(BASELINE + 12)}
    assert second["blockOverrides"] == {"time": hex(BASELINE + 24)}


def test_default_block_times_are_explicit_and_increase_by_twelve_seconds():
    sim, provider = _simulator()
    sim.observe("first", _read())
    sim.next_block()
    sim.observe("second", _read())

    sim.run()

    first, second = _sent_blocks(provider)
    assert first["blockOverrides"]["time"] == hex(BASELINE + 12)
    assert second["blockOverrides"]["time"] == hex(BASELINE + 24)


def test_explicit_shift_after_default_block_uses_modeled_client_time():
    sim, provider = _simulator()
    sim.observe("first", _read())
    sim.next_block(time_shift_seconds=1)
    sim.observe("second", _read())

    sim.run()

    first, second = _sent_blocks(provider)
    assert first["blockOverrides"]["time"] == hex(BASELINE + 12)
    assert second["blockOverrides"]["time"] == hex(BASELINE + 13)


@pytest.mark.parametrize("shift", (0, -1))
def test_next_block_rejects_non_increasing_time(shift):
    sim, _ = _simulator()
    sim.observe("first", _read())

    with pytest.raises(ValueError, match="strictly increasing"):
        sim.next_block(time_shift_seconds=shift)


def test_folded_empty_block_does_not_consume_a_default_time_increment():
    sim, provider = _simulator()
    sim.next_block()
    sim.observe("first", _read())
    sim.next_block()
    sim.observe("second", _read())

    sim.run()

    first, second = _sent_blocks(provider)
    assert first["blockOverrides"]["time"] == hex(BASELINE + 12)
    assert second["blockOverrides"]["time"] == hex(BASELINE + 24)


def test_run_rejects_absolute_time_that_moves_backwards():
    sim, _ = _simulator()
    sim.with_block_time_shift(60).observe("first", _read())
    sim.next_block()
    sim.with_block_time_shift(5).observe("second", _read())

    with pytest.raises(ValueError, match="strictly increasing"):
        sim.run()


def test_current_time_and_block_number_follow_only_sent_blocks():
    sim, _ = _simulator()
    assert sim.current_time == BASELINE
    assert sim.current_block_number == 100
    sim.next_block()
    sim.observe("first", _read())
    assert sim.current_time == BASELINE + 12
    assert sim.current_block_number == 101
    sim.next_block(time_shift_seconds=1)
    sim.observe("second", _read())
    assert sim.current_time == BASELINE + 13
    assert sim.current_block_number == 102


def test_current_block_number_requires_numeric_pin():
    web3 = MagicMock()
    web3.provider = RecordingProvider()
    web3.eth.get_block.return_value = {"timestamp": BASELINE}
    sim = VaultSimulator(web3, vault=VAULT, alpha=ALPHA, block="latest")

    with pytest.raises(ValueError, match="numeric pinned block"):
        _ = sim.current_block_number


def test_deploy_contract_serializes_creation_and_predicts_address():
    sim, provider = _simulator()
    init_code = bytes.fromhex("6001600c60003960016000f300")

    deployed = sim.deploy_contract(
        init_code,
        from_=PAYER,
        nonce=7,
        value=11,
        gas=500_000,
        label="deploy",
    )
    result = sim.run()

    assert deployed == Web3.to_checksum_address(
        "0x729683F8328f2FEA0d38FDCd26fad9fD8A07c876"
    )
    (entry,) = _sent_blocks(provider)
    (call,) = entry["calls"]
    assert call == {
        "input": "0x" + init_code.hex(),
        "from": PAYER,
        "nonce": "0x7",
        "value": "0xb",
        "gas": "0x7a120",
    }
    assert result.calls[0].predicted_address == deployed


def test_deploy_contract_omits_zero_value_and_optional_gas():
    sim, provider = _simulator()
    sim.deploy_contract(b"\x00", from_=PAYER, nonce=0)
    sim.run()
    (entry,) = _sent_blocks(provider)
    (call,) = entry["calls"]
    assert "value" not in call
    assert "gas" not in call


def test_add_call_serializes_an_explicit_gas_cap():
    sim, provider = _simulator()
    call = Call(to=VAULT, data=b"\x12\x34")

    sim.add_call(call, from_=PAYER, label="capped", gas=20_000_000)
    sim.add_call(call, from_=PAYER, label="default")
    sim.run()

    (entry,) = _sent_blocks(provider)
    capped, default = entry["calls"]
    assert capped["gas"] == hex(20_000_000)
    assert "gas" not in default
    with pytest.raises(ValueError, match="gas must be positive"):
        sim.add_call(call, from_=PAYER, gas=0)


@pytest.mark.parametrize(
    ("kwargs", "message"),
    [
        ({"init_code": b""}, "init_code must not be empty"),
        ({"init_code": b"\x00", "nonce": -1}, "nonce must be non-negative"),
        ({"init_code": b"\x00", "value": -1}, "value must be non-negative"),
        ({"init_code": b"\x00", "gas": 0}, "gas must be positive"),
    ],
)
def test_deploy_contract_rejects_invalid_input(kwargs, message):
    sim, _ = _simulator()
    params = {"from_": PAYER, "nonce": 0, **kwargs}
    with pytest.raises(ValueError, match=message):
        sim.deploy_contract(**params)


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


def test_same_block_state_overrides_compose_without_mutating_inputs():
    sim, provider = _simulator()
    initial_state = {STORAGE_A: VALUE_A}
    sim.with_state_override(TOKEN, state=initial_state, balance=hex(1))
    sim.with_erc20_balance(TOKEN, PAYER, 5, slot=9)
    sim.with_state_override(TOKEN, balance=hex(2), code="0x6000")
    assert initial_state == {STORAGE_A: VALUE_A}
    initial_state[STORAGE_B] = VALUE_B
    sim.observe("later", _read())
    sim.run()

    (entry,) = _sent_blocks(provider)
    assert entry["stateOverrides"][TOKEN] == {
        "state": {
            STORAGE_A: VALUE_A,
            _mapping_key(PAYER, 9): "0x" + (5).to_bytes(32, "big").hex(),
        },
        "balance": hex(2),
        "code": "0x6000",
    }
    assert initial_state == {STORAGE_A: VALUE_A, STORAGE_B: VALUE_B}


def test_same_block_state_replaces_an_earlier_state_diff():
    sim, provider = _simulator()
    sim.with_erc20_balance(TOKEN, PAYER, 5, slot=9)
    sim.with_state_override(TOKEN, state={STORAGE_B: VALUE_B})
    sim.observe("later", _read())
    sim.run()

    (entry,) = _sent_blocks(provider)
    assert entry["stateOverrides"][TOKEN] == {"state": {STORAGE_B: VALUE_B}}


def test_erc20_balance_slot_batches_every_candidate_in_one_request():
    from ipor_fusion import erc20_balance_slot

    web3 = MagicMock()
    web3.provider = FiatTokenProvider()
    web3.eth.get_block.return_value = {"timestamp": BASELINE}
    assert erc20_balance_slot(web3, TOKEN, block=100) == 9
    assert len(web3.provider.payloads) == 1
    (entry,) = web3.provider.payloads[0][0]["blockStateCalls"]
    assert len(entry["calls"]) == 33
    assert len(entry["stateOverrides"][TOKEN]["stateDiff"]) == 33
    holders = [call["input"][-40:] for call in entry["calls"]]
    assert len(set(holders)) == 33

    with pytest.raises(ValueError, match="no balances mapping slot"):
        erc20_balance_slot(web3, TOKEN, block=100, max_slot=8)
    assert len(web3.provider.payloads) == 2
    (entry,) = web3.provider.payloads[1][0]["blockStateCalls"]
    assert len(entry["calls"]) == 9

    with pytest.raises(ValueError, match="no balances mapping slot"):
        erc20_balance_slot(web3, TOKEN, block=100, max_slot=-1)
    assert len(web3.provider.payloads) == 2

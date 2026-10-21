"""`VaultSimulator.observe` returns a typed observation that `run()` resolves."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import (
    EmptyCallResultError,
    SimulationError,
    SimulationObservation,
    VaultSimulator,
)
from ipor_fusion.core.contract import Call

TOKEN = Web3.to_checksum_address("0x" + "11" * 20)


def _read() -> Call[str]:
    return Call(
        to=TOKEN, data=b"\x00" * 4, output_types=["uint256"], decoder=lambda v: f"#{v}"
    )


def _simulator(*call_results: dict) -> tuple[VaultSimulator, MagicMock]:
    """A simulator whose provider answers every run with ``call_results``."""
    web3 = MagicMock()
    web3.eth.get_block.return_value = {"timestamp": 1_790_000_000}
    web3.provider.make_request.return_value = {
        "result": [{"calls": [dict(r) for r in call_results]}]
    }
    return VaultSimulator(web3, block=100), web3.provider


def _ok(value: int) -> dict:
    return {
        "status": "0x1",
        "returnData": "0x" + encode(["uint256"], [value]).hex(),
        "gasUsed": "0x10",
        "logs": [],
    }


def test_value_is_the_decoded_read():
    sim, _ = _simulator(_ok(7))
    observation = sim.observe(_read(), label="read")

    assert isinstance(observation, SimulationObservation)
    sim.run()

    assert observation.value == "#7"
    assert observation.label == "read"
    assert observation.result.gas_used == 16


def test_value_before_run_raises():
    sim, _ = _simulator(_ok(7))
    observation = sim.observe(_read(), label="read")

    with pytest.raises(RuntimeError, match="'read' read before VaultSimulator.run"):
        _ = observation.value


def test_each_run_resolves_the_observation_again():
    sim, provider = _simulator(_ok(1))
    observation = sim.observe(_read())
    sim.run()
    provider.make_request.return_value = {"result": [{"calls": [_ok(2)]}]}

    sim.run()

    assert observation.value == "#2"


def test_a_reverted_read_raises_with_its_label_and_reason():
    revert = function_signature_to_4byte_selector("Error(string)") + encode(
        ["string"], ["NotAllowed"]
    )
    sim, _ = _simulator(
        {
            "status": "0x0",
            "returnData": "0x" + revert.hex(),
            "gasUsed": "0x10",
            "logs": [],
            "error": {"code": 3, "message": "execution reverted"},
        }
    )
    observation = sim.observe(_read(), label="read")
    sim.run()

    with pytest.raises(SimulationError, match="'read' failed") as raised:
        _ = observation.value
    assert raised.value.label == "read"
    assert raised.value.revert_reason is not None
    assert "NotAllowed" in raised.value.revert_reason


def test_a_read_without_data_raises_empty_call_result():
    sim, _ = _simulator({"status": "0x1", "returnData": "0x", "logs": []})
    observation = sim.observe(_read())
    sim.run()

    with pytest.raises(EmptyCallResultError, match="'<unlabelled>'"):
        _ = observation.value


def test_observe_requires_a_view_call():
    sim, _ = _simulator()

    with pytest.raises(ValueError, match="output_types"):
        sim.observe(Call(to=TOKEN, data=b"\x00" * 4))


def test_observe_queues_no_label_in_the_payload():
    sim, provider = _simulator(_ok(1))
    sim.observe(_read(), label="read")
    sim.run()

    (payload, _block) = provider.make_request.call_args.args[1]
    assert payload["blockStateCalls"][0]["calls"] == [
        {"input": "0x00000000", "to": TOKEN}
    ]

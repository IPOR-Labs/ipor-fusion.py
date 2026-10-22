"""`VaultSimulator.observe` returns a typed observation that `run()` resolves."""

from __future__ import annotations

import inspect
from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import (
    EmptyCallResultError,
    SimulationError,
    SimulationObservation,
    SimulationRunError,
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

    with pytest.raises(RuntimeError, match="'read' has no result"):
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


def _resolved_pair() -> tuple[
    VaultSimulator, MagicMock, SimulationObservation[str], SimulationObservation[str]
]:
    """Two observations resolved by a first, successful run."""
    sim, provider = _simulator(_ok(1), _ok(2))
    first = sim.observe(_read(), label="first")
    second = sim.observe(_read(), label="second")
    sim.run()
    assert (first.value, second.value) == ("#1", "#2")
    return sim, provider, first, second


def _assert_unresolved(*observations: SimulationObservation[str]) -> None:
    for observation in observations:
        with pytest.raises(RuntimeError, match="has no result"):
            _ = observation.value


def test_a_transport_error_leaves_every_observation_unresolved():
    sim, provider, first, second = _resolved_pair()
    provider.make_request.side_effect = ConnectionError("reset")

    with pytest.raises(ConnectionError):
        sim.run()
    _assert_unresolved(first, second)


def test_a_top_level_rpc_error_leaves_every_observation_unresolved():
    sim, provider, first, second = _resolved_pair()
    provider.make_request.return_value = {"error": {"code": -32000}}

    with pytest.raises(SimulationRunError, match="eth_simulateV1 failed"):
        sim.run()
    _assert_unresolved(first, second)


def test_a_result_count_mismatch_leaves_every_observation_unresolved():
    sim, provider, first, second = _resolved_pair()
    provider.make_request.return_value = {"result": [{"calls": [_ok(3)]}]}

    with pytest.raises(SimulationRunError, match="1 call results for 2 queued"):
        sim.run()
    _assert_unresolved(first, second)


@pytest.mark.parametrize(
    "broken",
    [
        {"status": "0xzz", "returnData": "0x", "gasUsed": "0x0", "logs": []},
        {"status": "0x1", "returnData": 7, "gasUsed": "0x0", "logs": []},
        {"status": "0x1", "returnData": "0x", "gasUsed": "0x0", "logs": [{}]},
        {
            "status": "0x1",
            "returnData": "0x",
            "logs": [{"address": "0x", "topics": [1], "data": "0x"}],
        },
        "not a call result",
        {"returnData": "0x", "gasUsed": "0x0", "logs": []},
        {"status": "0x2", "returnData": "0x", "gasUsed": "0x0", "logs": []},
        {"status": "0x1", "returnData": "0x", "gasUsed": "0x0", "logs": {}},
    ],
)
def test_a_partial_parse_failure_publishes_nothing(broken):
    """The first call parses, the second does not: neither observation may
    hold the new first result next to the old second one."""
    sim, provider, first, second = _resolved_pair()
    provider.make_request.return_value = {"result": [{"calls": [_ok(3), broken]}]}

    with pytest.raises(SimulationRunError, match="malformed response"):
        sim.run()
    _assert_unresolved(first, second)


@pytest.mark.parametrize("result", [None, {"calls": []}, [{"calls": {}}], ["block"]])
def test_a_malformed_result_shape_raises_a_run_error(result):
    sim, provider = _simulator()
    sim.observe(_read())
    provider.make_request.return_value = {"result": result}

    with pytest.raises(SimulationRunError, match="malformed response"):
        sim.run()


def test_a_run_after_a_failed_run_resolves_again():
    sim, provider, first, second = _resolved_pair()
    provider.make_request.return_value = {"error": {"code": -32000}}
    with pytest.raises(SimulationRunError):
        sim.run()

    provider.make_request.return_value = {"result": [{"calls": [_ok(5), _ok(6)]}]}
    sim.run()

    assert (first.value, second.value) == ("#5", "#6")


def test_logs_are_typed_records_keeping_the_node_fields():
    log = {
        "address": "0x" + "ab" * 20,
        "topics": ["0x" + "01" * 32],
        "data": "0x",
        "blockNumber": "0x65",
        "logIndex": "0x0",
        "removed": False,
        "unknownField": "dropped",
    }
    sim, _ = _simulator({**_ok(1), "logs": [log]})
    observation = sim.observe(_read())
    sim.run()

    assert observation.result.logs == [
        {
            "address": "0x" + "ab" * 20,
            "topics": ["0x" + "01" * 32],
            "data": "0x",
            "blockNumber": "0x65",
            "logIndex": "0x0",
            "removed": False,
        }
    ]


@pytest.mark.parametrize(
    "method", ["add_call", "add_transaction", "observe", "deploy_contract"]
)
def test_label_is_keyword_only(method):
    parameter = inspect.signature(getattr(VaultSimulator, method)).parameters["label"]
    assert parameter.kind is inspect.Parameter.KEYWORD_ONLY
    assert parameter.default is None


def test_a_failed_call_without_logs_has_none():
    sim, _ = _simulator({"status": "0x0", "returnData": "0x", "gasUsed": "0x10"})
    observation = sim.observe(_read())
    sim.run()

    assert observation.result.logs == []
    assert observation.result.success is False

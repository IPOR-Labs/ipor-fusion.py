"""Every failed simulated call carries a non-empty ``error``; a successful one none."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3

from ipor_fusion import (
    SimulatedCallResult,
    SimulationError,
    SimulationResult,
    VaultSimulator,
)
from ipor_fusion.core.contract import Call
from ipor_fusion.errors import register_custom_errors

TOKEN = Web3.to_checksum_address("0x" + "11" * 20)

_REASON = function_signature_to_4byte_selector("Error(string)") + encode(
    ["string"], ["NotAllowed"]
)
_CUSTOM_SIGNATURE = "SdkSimulationDenied(uint256)"
_CUSTOM = function_signature_to_4byte_selector(_CUSTOM_SIGNATURE) + encode(
    ["uint256"], [7]
)
_UNREGISTERED = bytes.fromhex("deadbeef")
register_custom_errors([_CUSTOM_SIGNATURE])


def _read() -> Call[int]:
    return Call(to=TOKEN, data=b"\x00" * 4, output_types=["uint256"])


def _failed(return_data: bytes, error: object = None) -> dict:
    raw: dict = {
        "status": "0x0",
        "returnData": "0x" + return_data.hex(),
        "gasUsed": "0x10",
        "logs": [],
    }
    if error is not None:
        raw["error"] = error
    return raw


def _run(raw: dict) -> tuple[SimulationResult, SimulationError]:
    """Run one failed observation; return the run and the error its value raises."""
    web3 = MagicMock()
    web3.provider.make_request.return_value = {"result": [{"calls": [raw]}]}
    sim = VaultSimulator(web3, block=100)
    observation = sim.observe(_read(), label="read")
    result = sim.run()
    with pytest.raises(SimulationError) as raised:
        _ = observation.value
    return result, raised.value


@pytest.mark.parametrize(
    ("raw", "error", "reason"),
    [
        pytest.param(
            _failed(_REASON, {"code": 3, "message": "execution reverted"}),
            "execution reverted: NotAllowed",
            "NotAllowed",
            id="reason-node-message-without-it",
        ),
        pytest.param(
            _failed(_REASON, "execution reverted: NotAllowed"),
            "execution reverted: NotAllowed",
            "NotAllowed",
            id="reason-already-in-node-message",
        ),
        pytest.param(
            _failed(
                b"", {"code": 3, "message": "reverted", "data": "0x" + _REASON.hex()}
            ),
            "reverted: NotAllowed",
            "NotAllowed",
            id="reason-in-error-data",
        ),
        pytest.param(_failed(_REASON), "NotAllowed", "NotAllowed", id="reason-only"),
        pytest.param(
            _failed(_CUSTOM, {"code": 3, "message": "execution reverted"}),
            "execution reverted: SdkSimulationDenied(7)",
            "SdkSimulationDenied(7)",
            id="registered-custom-error",
        ),
        pytest.param(
            _failed(_UNREGISTERED),
            "custom error 0xdeadbeef",
            "custom error 0xdeadbeef",
            id="unregistered-custom-error",
        ),
        pytest.param(
            _failed(b""),
            "reverted without a reason",
            "reverted without a reason",
            id="empty-revert",
        ),
        pytest.param(
            _failed(b"\x01\x02"),
            "reverted without a reason (return data 0x0102)",
            "reverted without a reason (return data 0x0102)",
            id="undecodable-return-data",
        ),
        pytest.param(
            _failed(b"", {"code": -32000, "message": "out of gas"}),
            "out of gas",
            "out of gas",
            id="node-level-error",
        ),
        pytest.param(
            _failed(b"", {"code": -32000, "message": ""}),
            "reverted without a reason",
            "reverted without a reason",
            id="node-error-without-message",
        ),
    ],
)
def test_a_failed_call_always_carries_an_error(
    raw: dict, error: str, reason: str
) -> None:
    result, raised = _run(raw)

    call = result.calls[0]
    assert not call.success
    assert call.error == error
    assert call.revert_reason == reason
    assert result.revert_reason == reason
    assert str(raised) == f"observation 'read' failed: {error}"
    assert raised.revert_reason == reason
    with pytest.raises(SimulationError) as run_error:
        result.raise_for_failure()
    assert str(run_error.value) == f"simulated call 'read' failed: {error}"
    assert run_error.value.revert_reason == reason


def test_a_successful_call_carries_no_error() -> None:
    web3 = MagicMock()
    web3.provider.make_request.return_value = {
        "result": [
            {
                "calls": [
                    {
                        "status": "0x1",
                        "returnData": "0x" + encode(["uint256"], [1]).hex(),
                        "error": "ignored",
                        "logs": [],
                    }
                ]
            }
        ]
    }
    sim = VaultSimulator(web3, block=100)
    sim.observe(_read())

    call = sim.run().calls[0]

    assert call.success
    assert call.error is None
    assert call.revert_reason is None


def _call(
    *, success: bool, error: str | None, data: bytes = b""
) -> SimulatedCallResult:
    return SimulatedCallResult(
        label="c",
        success=success,
        return_data=HexBytes(data),
        gas_used=0,
        error=error,
        logs=[],
    )


def test_construction_completes_a_failed_calls_error() -> None:
    assert _call(success=False, error=None).error == "reverted without a reason"
    assert _call(success=False, error="").error == "reverted without a reason"
    assert (
        _call(success=False, error="execution reverted", data=_REASON).error
        == "execution reverted: NotAllowed"
    )


def test_construction_is_idempotent() -> None:
    for data, error in [
        (_REASON, "execution reverted"),
        (_REASON, None),
        (b"\x01", None),
        (b"", None),
    ]:
        first = _call(success=False, error=error, data=data)
        again = _call(success=False, error=first.error, data=data)
        assert again.error == first.error


def test_construction_rejects_an_error_on_success() -> None:
    with pytest.raises(ValueError, match="carries an error"):
        _call(success=True, error="execution reverted")

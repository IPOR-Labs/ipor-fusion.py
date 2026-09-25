"""The crosschain custom-error registry: every signature parses and is
registered, selectors match the contracts, revert data decodes by name, and
``SimulationResult.raise_for_failure`` reports the first failed call."""

from __future__ import annotations

import re

import pytest
from eth_abi import encode as abi_encode
from eth_utils import keccak
from hexbytes import HexBytes

from ipor_fusion.core.simulation import (
    SimulatedCallResult,
    SimulationResult,
    _decode_revert,
)
from ipor_fusion.crosschain import CROSSCHAIN_ERROR_SIGNATURES
from ipor_fusion.errors import (
    CUSTOM_ERRORS,
    SimulationError,
    _decode_revert_reason,
    decode_custom_error,
)

SIGNATURE = re.compile(r"^[A-Za-z_]\w*\((\w+(,\w+)*)?\)$")
EXECUTOR = "0x1d5c9d44f8d556ec7f557ae992401cc770937e6e"


def _selector(signature: str) -> bytes:
    return keccak(text=signature)[:4]


def _revert(signature: str, *args) -> bytes:
    types = signature[signature.index("(") + 1 : -1]
    payload = abi_encode(types.split(",") if types else [], list(args))
    return _selector(signature) + payload


def test_every_signature_parses_and_is_registered():
    assert len(CROSSCHAIN_ERROR_SIGNATURES) == len(set(CROSSCHAIN_ERROR_SIGNATURES))
    assert list(CROSSCHAIN_ERROR_SIGNATURES) == sorted(CROSSCHAIN_ERROR_SIGNATURES)
    for signature in CROSSCHAIN_ERROR_SIGNATURES:
        assert SIGNATURE.fullmatch(signature), signature
        name, types = CUSTOM_ERRORS[_selector(signature)]
        assert signature == f"{name}({','.join(types)})"


def test_a_name_declared_twice_keeps_both_selectors():
    # Stargate and CCIP declare MinUpdateIntervalNotMet with different widths.
    assert "MinUpdateIntervalNotMet(uint256,uint256,uint256)" in (
        CROSSCHAIN_ERROR_SIGNATURES
    )
    assert "MinUpdateIntervalNotMet(uint64,uint256,uint256)" in (
        CROSSCHAIN_ERROR_SIGNATURES
    )


@pytest.mark.parametrize(
    ("signature", "selector_hex"),
    [
        ("NavSettledStale(uint256)", "54b8d781"),
        (
            "ObservationStale(uint256)",
            keccak(text="ObservationStale(uint256)")[:4].hex(),
        ),
        (
            "ProposalVersionMismatch(uint64,uint64)",
            keccak(text="ProposalVersionMismatch(uint64,uint64)")[:4].hex(),
        ),
        (
            "BigChangeExceeded(uint256,uint256,uint256)",
            keccak(text="BigChangeExceeded(uint256,uint256,uint256)")[:4].hex(),
        ),
    ],
)
def test_selectors_match_the_contracts(signature: str, selector_hex: str):
    selector = bytes.fromhex(selector_hex)
    assert _selector(signature) == selector
    assert CUSTOM_ERRORS[selector][0] == signature.split("(")[0]


@pytest.mark.parametrize(
    ("data", "expected"),
    [
        (_revert("NavSettledStale(uint256)", 8453), "NavSettledStale(8453)"),
        (
            _revert("ProposalVersionMismatch(uint64,uint64)", 6, 7),
            "ProposalVersionMismatch(6, 7)",
        ),
        (
            _revert("BigChangeExceeded(uint256,uint256,uint256)", 13, 0, 1000),
            "BigChangeExceeded(13, 0, 1000)",
        ),
        (_revert("ZeroAmount()"), "ZeroAmount()"),
        (
            _revert("TicketAddressMismatch(address,address)", EXECUTOR, EXECUTOR),
            "TicketAddressMismatch("
            "0x1D5c9d44f8D556EC7F557Ae992401Cc770937E6E, "
            "0x1D5c9d44f8D556EC7F557Ae992401Cc770937E6E)",
        ),
        (
            _revert("CommandNotFound(bytes32)", b"\x07" * 32),
            "CommandNotFound(0x" + "07" * 32 + ")",
        ),
    ],
)
def test_revert_data_decodes_by_name(data: bytes, expected: str):
    assert _decode_revert_reason(data) == expected
    assert _decode_revert(HexBytes(data), "execution reverted") == expected


def test_unregistered_selector_stays_a_bare_selector():
    data = bytes.fromhex("deadbeef") + b"\x00" * 32
    assert decode_custom_error(data[:4], data[4:]) is None
    assert _decode_revert(HexBytes(data), "execution reverted") == (
        "custom error 0xdeadbeef"
    )


def _result(*calls: SimulatedCallResult) -> SimulationResult:
    failed = [c for c in calls if not c.success]
    return SimulationResult(
        success=not failed,
        all_success=not failed,
        revert_reason=None,
        gas_used=0,
        execute_logs=[],
        observations={},
        calls=list(calls),
        failed_calls=failed,
    )


def _call(label: str, *, success: bool, data: bytes = b"") -> SimulatedCallResult:
    return SimulatedCallResult(
        label=label,
        success=success,
        return_data=HexBytes(data),
        gas_used=0,
        error=None if success else "execution reverted",
        logs=[],
        decoded=None,
    )


class TestRaiseForFailure:
    def test_names_the_first_failed_call_with_its_reason(self):
        result = _result(
            _call("ok", success=True),
            _call("nav", success=False, data=_revert("NavSettledStale(uint256)", 8453)),
            _call("later", success=False),
        )
        with pytest.raises(SimulationError, match="'nav'.*NavSettledStale\\(8453\\)"):
            result.raise_for_failure()
        with pytest.raises(SimulationError) as info:
            result.raise_for_failure()
        assert info.value.label == "nav"
        assert info.value.revert_reason == "NavSettledStale(8453)"

    def test_without_revert_data_reports_the_client_error(self):
        result = _result(_call(None, success=False))  # type: ignore[arg-type]
        with pytest.raises(SimulationError, match="<unlabelled>.*execution reverted"):
            result.raise_for_failure()

    def test_no_op_when_every_call_succeeded(self):
        _result(_call("a", success=True), _call("b", success=True)).raise_for_failure()

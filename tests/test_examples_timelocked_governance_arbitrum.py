"""Offline checks for the Arbitrum timelocked-governance example."""

from __future__ import annotations

from types import ModuleType

import pytest
from eth_abi.abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from test_examples_vaults import Loader, _simulated_call, _simulation_result
from web3 import Web3

from ipor_fusion import Roles
from ipor_fusion.core.simulation import SimulatedCallResult

_SET_CAP = function_signature_to_4byte_selector("setTotalSupplyCap(uint256)")
_SCHEDULE = function_signature_to_4byte_selector("schedule(address,bytes,uint48)")
_CANCEL = function_signature_to_4byte_selector("cancel(address,address,bytes)")
_NOT_READY = function_signature_to_4byte_selector("AccessManagerNotReady(bytes32)")
_NOT_SCHEDULED = function_signature_to_4byte_selector(
    "AccessManagerNotScheduled(bytes32)"
)
_UNAUTHORIZED = function_signature_to_4byte_selector(
    "AccessManagerUnauthorizedAccount(address,uint64)"
)
_STRANGER = "0x000000000000000000000000000000000000dEaD"


@pytest.fixture
def mod(load_example: Loader) -> ModuleType:
    return load_example("timelocked_governance_arbitrum.py")


def _call(label: str, revert: bytes | None = None) -> SimulatedCallResult:
    """A succeeding call, or one reverting with ``revert``."""
    if revert is None:
        return _simulated_call(label, success=True)
    return _simulated_call(label, success=False, return_data=revert)


def _not_ready() -> bytes:
    return _NOT_READY + bytes(32)


def _not_scheduled() -> bytes:
    return _NOT_SCHEDULED + bytes(32)


def test_cap_call_sets_the_cap_on_the_vault(mod):
    call = mod.cap_call(mod.NEW_CAP)

    assert call.to == mod.VAULT
    assert call.data[:4] == _SET_CAP
    assert decode(["uint256"], call.data[4:]) == (mod.NEW_CAP,)


def test_schedule_calldata_schedules_the_cap_change_asap(mod):
    data = mod.schedule_calldata()

    assert data[:4] == _SCHEDULE
    target, inner, when = decode(["address", "bytes", "uint48"], data[4:])
    assert Web3.to_checksum_address(target) == mod.VAULT
    assert inner == mod.cap_call(mod.NEW_CAP).data
    assert when == 0


def test_operation_id_hashes_the_atomist_call_on_the_vault(mod):
    target, inner, _ = decode(
        ["address", "bytes", "uint48"], mod.schedule_calldata()[4:]
    )

    expected = Web3.keccak(
        encode(["address", "address", "bytes"], [mod.ATOMIST, target, inner])
    )
    assert mod.operation_id(inner) == expected


def test_cancel_call_cancels_the_atomist_second_change(mod):
    call = mod.cancel_call()

    assert call.to == mod.ACCESS_MANAGER
    assert call.data[:4] == _CANCEL
    caller, target, inner = decode(["address", "address", "bytes"], call.data[4:])
    assert Web3.to_checksum_address(caller) == mod.ATOMIST
    assert Web3.to_checksum_address(target) == mod.VAULT
    assert inner == mod.cap_call(mod.CANCELLED_CAP).data


def test_the_two_caps_differ(mod):
    assert mod.CANCELLED_CAP != mod.NEW_CAP


def test_actors_are_distinct_addresses(mod):
    actors = [mod.VAULT, mod.ACCESS_MANAGER, mod.ATOMIST, mod.GUARDIAN]

    assert len(set(actors)) == len(actors)


def test_expected_failure_passes_on_exactly_the_expected_reverts(mod):
    result = _simulation_result(
        [
            _call("schedule"),
            _call("early_execute", _not_ready()),
            _call("execute"),
            _call("schedule_cancelled"),
            _call("cancel"),
            _call("cancelled_execute", _not_scheduled()),
        ]
    )

    mod._assert_only_expected_failures(result, mod.EXPECTED_FAILURES)


@pytest.mark.parametrize(
    "calls",
    [
        pytest.param(
            [_call("early_execute"), _call("cancelled_execute")], id="nothing-failed"
        ),
        pytest.param(
            [_call("early_execute", _not_ready()), _call("cancelled_execute")],
            id="the-cancelled-call-succeeded",
        ),
        pytest.param(
            [_call("early_execute"), _call("cancelled_execute", _not_scheduled())],
            id="the-early-call-succeeded",
        ),
        pytest.param(
            [
                _call("early_execute", _not_ready()),
                _call("execute", _not_ready()),
                _call("cancelled_execute", _not_scheduled()),
            ],
            id="another-call-failed",
        ),
        pytest.param(
            [
                _call("early_execute", b"\xde\xad\xbe\xef"),
                _call("cancelled_execute", _not_scheduled()),
            ],
            id="undecodable-reason",
        ),
        pytest.param(
            [
                _call(
                    "early_execute",
                    _UNAUTHORIZED
                    + encode(["address", "uint64"], [_STRANGER, Roles.ATOMIST_ROLE]),
                ),
                _call("cancelled_execute", _not_scheduled()),
            ],
            id="another-access-manager-error",
        ),
        pytest.param(
            [
                _call("early_execute", _not_ready()),
                _call("cancelled_execute", _not_ready()),
            ],
            id="cancelled-call-not-ready",
        ),
        pytest.param(
            [
                _call("early_execute", _not_ready()),
                _call("cancelled_execute", b"\xde\xad\xbe\xef"),
            ],
            id="cancelled-call-undecodable-reason",
        ),
        pytest.param(
            [
                _call("early_execute", _not_ready()),
                _call(
                    "cancelled_execute",
                    _UNAUTHORIZED
                    + encode(["address", "uint64"], [_STRANGER, Roles.ATOMIST_ROLE]),
                ),
            ],
            id="cancelled-call-unauthorized",
        ),
    ],
)
def test_expected_failure_raises_otherwise(mod, calls):
    with pytest.raises(AssertionError):
        mod._assert_only_expected_failures(
            _simulation_result(calls), mod.EXPECTED_FAILURES
        )

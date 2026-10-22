"""Shared helpers for VaultSimulator-driven tests."""

from __future__ import annotations

from unittest.mock import MagicMock

from ipor_fusion import SimulationResult, VaultSimulator


def run_with_response(sim: VaultSimulator, result: object) -> SimulationResult:
    """Run ``sim`` (built over a ``MagicMock`` web3) against ``result`` as the
    node's ``eth_simulateV1`` answer."""
    web3 = sim._web3
    assert isinstance(web3, MagicMock)
    web3.eth.get_block.return_value = {"timestamp": 1_790_000_000}
    web3.provider.make_request.return_value = {"result": result}
    return sim.run()


def assert_all_success(result: SimulationResult) -> None:
    """Fail with a readable summary of the first revert if any call failed."""
    if result.all_success:
        return
    failed = [(c.label, c.error) for c in result.failed_calls]
    raise AssertionError(f"calls failed: {failed} (reason={result.revert_reason})")


def simulate_response(**call_result: object) -> list[dict]:
    """One `eth_simulateV1` block wrapping a single call result.

    Just the envelope `VaultSimulator.run` expects as `result`; the caller
    supplies whatever call fields its scenario needs (`status`, `returnData`,
    `gasUsed`, `logs`, `error`)."""
    return [{"calls": [call_result]}]


def address_substrate(addr: str) -> bytes:
    """Pad an EVM address into a 32-byte substrate value (left-zero-padded)."""
    return bytes.fromhex(addr.removeprefix("0x").lower().rjust(64, "0"))

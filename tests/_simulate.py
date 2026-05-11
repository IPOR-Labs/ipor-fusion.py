"""Shared helpers for VaultSimulator-driven tests."""

from __future__ import annotations

from ipor_fusion import SimulationResult


def assert_all_success(result: SimulationResult) -> None:
    """Fail with a readable summary of the first revert if any call failed."""
    if result.all_success:
        return
    failed = [(c.label, c.error) for c in result.failed_calls]
    raise AssertionError(f"calls failed: {failed} (reason={result.revert_reason})")


def address_substrate(addr: str) -> bytes:
    """Pad an EVM address into a 32-byte substrate value (left-zero-padded)."""
    return bytes.fromhex(addr.removeprefix("0x").lower().rjust(64, "0"))

"""Fake ``SimulationResult`` builders: a finished run without a chain."""

from __future__ import annotations

from hexbytes import HexBytes

from ipor_fusion.core.simulation import SimulatedCallResult, SimulationResult


def simulated_call(
    label: str,
    *,
    success: bool,
    return_data: bytes = b"",
    logs: list[dict] | None = None,
) -> SimulatedCallResult:
    """One entry in a fake `SimulationResult`."""
    return SimulatedCallResult(
        label=label,
        success=success,
        return_data=HexBytes(return_data),
        gas_used=0,
        error=None if success else "execution reverted",
        logs=logs if logs is not None else [],
    )


def simulation_result(calls: list[SimulatedCallResult]) -> SimulationResult:
    """A finished `SimulationResult` over `calls`, with `failed_calls` derived."""
    failed = [c for c in calls if not c.success]
    return SimulationResult(
        success=not failed,
        all_success=not failed,
        revert_reason="execution reverted" if failed else None,
        gas_used=0,
        execute_logs=[],
        calls=calls,
        failed_calls=failed,
    )

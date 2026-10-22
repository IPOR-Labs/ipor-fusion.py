"""The records an ``eth_simulateV1`` run produces, and the parsing that
builds them from the node's response."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from typing import Final, NotRequired, Protocol, TypedDict

from eth_abi import decode
from eth_abi.exceptions import DecodingError
from eth_typing import ChecksumAddress
from hexbytes import HexBytes

from ipor_fusion.errors import SimulationError, SimulationRunError, decode_custom_error


class QueuedCall(Protocol):
    """What parsing needs of a queued call."""

    @property
    def label(self) -> str | None: ...

    @property
    def is_execute(self) -> bool: ...

    @property
    def predicted_address(self) -> ChecksumAddress | None: ...


class SimulatedLog(TypedDict):
    """One log a simulated call emitted, as the node returns it: ``0x`` hex
    text throughout and a lowercase ``address``. The identity fields are
    present when the node sends them."""

    address: str
    topics: list[str]
    data: str
    blockNumber: NotRequired[str]
    blockHash: NotRequired[str]
    blockTimestamp: NotRequired[str]
    transactionHash: NotRequired[str]
    transactionIndex: NotRequired[str]
    logIndex: NotRequired[str]
    removed: NotRequired[bool]


_LOG_HEX_FIELDS: Final = (
    "blockNumber",
    "blockHash",
    "blockTimestamp",
    "transactionHash",
    "transactionIndex",
    "logIndex",
)


@dataclass(frozen=True, slots=True)
class SimulatedCallResult:
    """One simulated call. ``error`` is set exactly when ``success`` is False:
    check ``success``, then read ``error`` for a non-empty, human-readable
    failure (the node's message with the decoded revert reason, or
    ``"reverted without a reason"`` plus any undecodable return data).
    Construction enforces it: a failed call's ``error`` is completed from its
    return data, and a successful call given an ``error`` raises
    ``ValueError``. The record is frozen, so the invariant holds afterwards;
    build a changed copy with `dataclasses.replace`."""

    label: str | None
    success: bool
    return_data: HexBytes
    gas_used: int
    error: str | None
    logs: list[SimulatedLog]
    predicted_address: ChecksumAddress | None = None
    # Frozen would add a __hash__ that fails on the logs list; stay unhashable.
    __hash__ = None  # type: ignore[assignment]

    def __post_init__(self) -> None:
        if self.success:
            if self.error is not None:
                raise ValueError(
                    f"successful simulated call {self.label!r} carries an error"
                )
        else:
            # The record is frozen; completing the error is part of building it.
            object.__setattr__(
                self, "error", _failure_message(self.error, self.return_data)
            )

    @property
    def revert_reason(self) -> str | None:
        """This call's decoded revert (a registered custom error by name, an
        ``Error(string)`` text or ``Panic``), else its ``error``; ``None``
        exactly when it succeeded. ``SimulationResult.revert_reason`` may name
        a different call: the first failed execute-style call, else the first
        failed call of any kind."""
        if self.success:
            return None
        return _decode_revert(self.return_data, self.error)


@dataclass(frozen=True, slots=True)
class SimulationResult:
    """One `VaultSimulator.run()`. ``success``, ``gas_used`` and
    ``execute_logs`` cover the execute-style calls only (`execute`,
    `execute_call`, `add_transaction`); ``all_success`` and ``failed_calls``
    cover every call. ``revert_reason`` is that of the first failed
    execute-style call, else of the first failed call of any kind, else
    ``None``."""

    success: bool
    all_success: bool
    revert_reason: str | None
    gas_used: int
    execute_logs: list[SimulatedLog]
    calls: list[SimulatedCallResult] = field(default_factory=list)
    failed_calls: list[SimulatedCallResult] = field(default_factory=list)
    __hash__ = None  # type: ignore[assignment]

    def raise_for_failure(self) -> None:
        """Raise :class:`SimulationError` naming the first failed call (label,
        client error, decoded revert reason); a no-op when every call succeeded."""
        if not self.failed_calls:
            return
        first = self.failed_calls[0]
        label = first.label or "<unlabelled>"
        raise SimulationError(
            f"simulated call {label!r} failed: {first.error}",
            label=first.label,
            revert_reason=first.revert_reason,
        )


def parse_simulate_response(
    sources: Sequence[QueuedCall], result: object
) -> SimulationResult:
    """The run's result, one call result per queued call in queue order, or
    ``SimulationRunError`` when the response does not have that shape."""
    try:
        raw_calls = _response_calls(result)
        if len(raw_calls) != len(sources):
            raise SimulationRunError(
                "eth_simulateV1 returned "
                f"{len(raw_calls)} call results for {len(sources)} queued calls"
            )
        parsed = [
            _parse_call(source, raw)
            for source, raw in zip(sources, raw_calls, strict=True)
        ]
    except (TypeError, ValueError) as exc:
        raise SimulationRunError(
            f"eth_simulateV1 returned a malformed response: {exc}"
        ) from exc

    execute_success = True
    revert_reason: str | None = None
    execute_gas = 0
    execute_logs: list[SimulatedLog] = []
    for source, call_result in zip(sources, parsed, strict=True):
        if source.is_execute:
            execute_success = execute_success and call_result.success
            execute_gas += call_result.gas_used
            execute_logs.extend(call_result.logs)
            if not call_result.success and revert_reason is None:
                revert_reason = call_result.revert_reason

    failed_calls = [c for c in parsed if not c.success]
    if revert_reason is None and failed_calls:
        # No execute call reverted but a setup/observation did — surface it.
        revert_reason = failed_calls[0].revert_reason

    return SimulationResult(
        success=execute_success,
        all_success=not failed_calls,
        revert_reason=revert_reason,
        gas_used=execute_gas,
        execute_logs=execute_logs,
        calls=parsed,
        failed_calls=failed_calls,
    )


def _response_calls(result: object) -> list[Mapping[str, object]]:
    """The per-call results of every block, flattened into queue order."""
    calls: list[Mapping[str, object]] = []
    for block in _array(result, "result"):
        block_calls = _object(block, "block result").get("calls", [])
        calls.extend(
            _object(call, "call result") for call in _array(block_calls, "calls")
        )
    return calls


def _parse_call(source: QueuedCall, raw: Mapping[str, object]) -> SimulatedCallResult:
    success = _quantity(raw.get("status", "0x1"), "status") == 1
    message, return_data = _normalize_call_error(
        raw.get("error"), HexBytes(_text(raw.get("returnData", "0x"), "returnData"))
    )
    return SimulatedCallResult(
        label=source.label,
        success=success,
        return_data=return_data,
        gas_used=_quantity(raw.get("gasUsed", "0x0"), "gasUsed"),
        error=None if success else message,
        logs=[_parse_log(log) for log in _array(raw.get("logs") or [], "logs")],
        predicted_address=source.predicted_address if success else None,
    )


def _parse_log(raw: object) -> SimulatedLog:
    fields = _object(raw, "log")
    topics = _array(fields.get("topics"), "log topics")
    log: SimulatedLog = {
        "address": _text(fields.get("address"), "log address"),
        "topics": [_text(topic, "log topic") for topic in topics],
        "data": _text(fields.get("data"), "log data"),
    }
    for key in _LOG_HEX_FIELDS:
        if (value := fields.get(key)) is not None:
            log[key] = _text(value, f"log {key}")
    if (removed := fields.get("removed")) is not None:
        if not isinstance(removed, bool):
            raise TypeError(f"log removed is {type(removed).__name__}, not a bool")
        log["removed"] = removed
    return log


def _object(value: object, what: str) -> Mapping[str, object]:
    if not isinstance(value, Mapping):
        raise TypeError(f"{what} is {type(value).__name__}, not an object")
    return value


def _array(value: object, what: str) -> list[object]:
    if not isinstance(value, list):
        raise TypeError(f"{what} is {type(value).__name__}, not an array")
    return value


def _text(value: object, what: str) -> str:
    if not isinstance(value, str):
        raise TypeError(f"{what} is {type(value).__name__}, not a string")
    return value


def _quantity(value: object, what: str) -> int:
    return int(_text(value, what), 16)


def _normalize_call_error(
    error_raw: object, return_data: HexBytes
) -> tuple[str | None, HexBytes]:
    """Normalize a per-call `error` into (message, revert payload).

    Per the eth_simulateV1 spec, a failed call carries a JSON-RPC error object
    `{code, message, data}` — the revert payload is in `error.data`, and some
    clients leave `returnData` empty in that case. Other clients report the
    error as a plain string and put the revert payload in `returnData`.
    """
    if isinstance(error_raw, Mapping):
        error = _object(error_raw, "call error")
        data = error.get("data")
        if not return_data and data:
            return_data = HexBytes(_text(data, "call error data"))
        message = error.get("message")
        return (message if isinstance(message, str) else None), return_data
    return (error_raw if isinstance(error_raw, str) else None), return_data


def _failure_message(message: str | None, return_data: HexBytes) -> str:
    """A failed call's ``error``: the node's message joined with the decoded
    revert unless the message already carries it, else whichever exists, else
    ``"reverted without a reason"`` with any return data too short to decode."""
    reason = _decode_revert(return_data, None)
    if message and reason:
        return message if reason in message else f"{message}: {reason}"
    if message:
        return message
    if reason:
        return reason
    if return_data:
        return f"reverted without a reason (return data 0x{bytes(return_data).hex()})"
    return "reverted without a reason"


def _decode_revert(return_data: HexBytes, error: str | None) -> str | None:
    if return_data and len(return_data) >= 4:
        selector = bytes(return_data[:4])
        if selector == b"\x08\xc3\x79\xa0":  # Error(string)
            try:
                (msg,) = decode(["string"], bytes(return_data[4:]))
                return msg
            except (DecodingError, OverflowError, ValueError):
                pass
        if selector == b"\x4e\x48\x7b\x71":  # Panic(uint256)
            try:
                (code,) = decode(["uint256"], bytes(return_data[4:]))
                return f"Panic(0x{code:x})"
            except (DecodingError, OverflowError, ValueError):
                pass
        decoded = decode_custom_error(selector, bytes(return_data[4:]))
        if decoded is not None:
            return decoded
        return f"custom error 0x{selector.hex()}"
    return error

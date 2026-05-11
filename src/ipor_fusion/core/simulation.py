from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from hexbytes import HexBytes
from web3 import Web3
from web3.types import RPCEndpoint, BlockIdentifier

from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.fuses.base import FuseAction


@dataclass(slots=True)
class _Call:
    to: ChecksumAddress
    data: bytes
    from_: ChecksumAddress | None
    label: str | None
    decode_types: list[str] | None
    is_execute: bool


@dataclass(slots=True)
class _Block:
    calls: list[_Call] = field(default_factory=list)
    block_overrides: dict[str, Any] = field(default_factory=dict)
    state_overrides: dict[str, dict[str, Any]] = field(default_factory=dict)


@dataclass(slots=True)
class SimulatedCallResult:
    label: str | None
    success: bool
    return_data: HexBytes
    gas_used: int
    error: str | None
    logs: list[dict]
    decoded: Any | None


@dataclass(slots=True)
class SimulationResult:
    success: bool
    all_success: bool
    revert_reason: str | None
    gas_used: int
    execute_logs: list[dict]
    observations: dict[str, Any]
    calls: list[SimulatedCallResult] = field(default_factory=list)
    failed_calls: list[SimulatedCallResult] = field(default_factory=list)

    def get(self, label: str) -> Any:
        return self.observations[label]


def is_simulate_v1_supported(web3: Web3) -> bool:
    """Check whether the connected RPC provider implements `eth_simulateV1`.

    Sends a minimal probe payload and inspects the response. Use this to gate
    simulation-based features in production code without raising; for tests,
    prefer `pytest.skip` based on this check.
    """
    probe = web3.provider.make_request(
        RPCEndpoint("eth_simulateV1"),
        [
            {
                "blockStateCalls": [
                    {
                        "calls": [
                            {
                                "to": "0x0000000000000000000000000000000000000000",
                                "input": "0x",
                            }
                        ]
                    }
                ]
            },
            "latest",
        ],
    )
    return "error" not in probe


class VaultSimulator:
    """Build and run an `eth_simulateV1` payload for a PlasmaVault flow.

    Buffers writes (FuseAction batches via `execute`) and reads (`observe`)
    into a single JSON-RPC roundtrip. State carries between calls within the
    same simulated block — and across blocks via `next_block(...)` — mirroring
    fork semantics without running anvil/foundry.

    Typical use cases:
      - Simulation gate before submitting a strategy on-chain
      - Health-factor projection across time (`next_block(time_shift_seconds)`)
      - Parameter sweep via parallel sims on the same baseline state
      - Test infrastructure replacement for anvil-forked integration tests

    Example — simulate a leveraged loop and read post-state:

        from ipor_fusion import VaultSimulator, is_simulate_v1_supported

        if not is_simulate_v1_supported(web3):
            raise RuntimeError("provider must implement eth_simulateV1")

        sim = VaultSimulator(web3, vault=VAULT, alpha=ALPHA, block="latest")
        sim.observe("ltv_before", aave_pool, "getUserAccountData(address)", (vault,),
                    output_types=["uint256","uint256","uint256","uint256","uint256","uint256"])
        sim.execute([flash_loan_action])
        sim.observe("ltv_after", aave_pool, "getUserAccountData(address)", (vault,),
                    output_types=["uint256","uint256","uint256","uint256","uint256","uint256"])

        result = sim.run()
        if result.all_success and is_health_safe(result.get("ltv_after")):
            vault.execute([flash_loan_action])  # green-light real execution

    Limitations:
      - State does not survive across separate `run()` calls (each is fresh)
      - Read values cannot be threaded into later call calldata in the same batch
      - Up to 256 blocks per batch (eth_simulateV1 limit)
      - Requires geth/reth-based provider (Polygon Bor / zkSync etc. unsupported)
    """

    def __init__(
        self,
        web3: Web3,
        vault: ChecksumAddress,
        alpha: ChecksumAddress,
        block: BlockIdentifier = "latest",
        validation: bool = False,
        trace_transfers: bool = False,
    ):
        self._web3 = web3
        self._vault = Web3.to_checksum_address(vault)
        self._alpha = Web3.to_checksum_address(alpha)
        self._block = block
        self._validation = validation
        self._trace_transfers = trace_transfers
        self._blocks: list[_Block] = [_Block()]
        self._baseline_timestamp: int | None = None

    @property
    def _current(self) -> _Block:
        return self._blocks[-1]

    def _baseline(self) -> int:
        """Pinned-block timestamp; cached so multi-block batches stay consistent."""
        if self._baseline_timestamp is None:
            self._baseline_timestamp = int(
                self._web3.eth.get_block(self._block)["timestamp"]
            )
        return self._baseline_timestamp

    def with_block_time_shift(self, seconds: int) -> "VaultSimulator":
        """Override the current block's `time` to baseline + seconds."""
        self._current.block_overrides["time"] = hex(self._baseline() + int(seconds))
        return self

    def with_block_override(self, **fields: Any) -> "VaultSimulator":
        for key, value in fields.items():
            self._current.block_overrides[key] = (
                hex(value) if isinstance(value, int) else value
            )
        return self

    def with_state_override(
        self, address: ChecksumAddress, **overrides: Any
    ) -> "VaultSimulator":
        self._current.state_overrides[Web3.to_checksum_address(address)] = overrides
        return self

    def next_block(self, time_shift_seconds: int | None = None) -> "VaultSimulator":
        """Seal the current block and open a new one, optionally shifted in time.

        State carries from the previous block — same semantics as `evm_mine` after
        `evm_increaseTime` on a fork. Use this when an action needs to occur in a
        later block (e.g. cooldown periods, accrual).
        """
        new_block = _Block()
        if time_shift_seconds is not None:
            prev_time = self._current_time()
            new_block.block_overrides["time"] = hex(prev_time + int(time_shift_seconds))
        self._blocks.append(new_block)
        return self

    def _current_time(self) -> int:
        """Resolve the most recently set block timestamp, walking back if needed."""
        for block in reversed(self._blocks):
            if "time" in block.block_overrides:
                return int(block.block_overrides["time"], 16)
        return self._baseline()

    def execute(self, actions: list[FuseAction]) -> "VaultSimulator":
        return self.execute_on(
            target=self._vault,
            signature="execute((address,bytes)[])",
            actions=actions,
        )

    def execute_on(
        self,
        target: ChecksumAddress,
        signature: str,
        actions: list[FuseAction],
        from_: ChecksumAddress | None = None,
    ) -> "VaultSimulator":
        """Encode and queue a `signature((address,bytes)[])`-style FuseAction batch
        targeting `target`, sent from `from_` (defaults to alpha). Used both for
        `PlasmaVault.execute(...)` and `RewardsManager.claimRewards(...)`.
        """
        data = FuseAction.encode_execute_payload(actions, signature)
        self._current.calls.append(
            _Call(
                to=Web3.to_checksum_address(target),
                data=data,
                from_=Web3.to_checksum_address(from_) if from_ else self._alpha,
                label=None,
                decode_types=None,
                is_execute=True,
            )
        )
        return self

    def observe(
        self,
        label: str,
        contract: ChecksumAddress,
        signature: str,
        args: tuple = (),
        output_types: list[str] | None = None,
    ) -> "VaultSimulator":
        selector = function_signature_to_4byte_selector(signature)
        input_types = _parse_param_types(signature)
        data = selector + (encode(input_types, list(args)) if input_types else b"")
        decode_types = output_types or [_default_return_type(signature)]
        self._current.calls.append(
            _Call(
                to=Web3.to_checksum_address(contract),
                data=data,
                from_=None,
                label=label,
                decode_types=decode_types,
                is_execute=False,
            )
        )
        return self

    def add_call(
        self,
        to: ChecksumAddress,
        data: bytes,
        from_: ChecksumAddress | None = None,
        label: str | None = None,
        output_types: list[str] | None = None,
    ) -> "VaultSimulator":
        self._current.calls.append(
            _Call(
                to=Web3.to_checksum_address(to),
                data=data,
                from_=Web3.to_checksum_address(from_) if from_ else None,
                label=label,
                decode_types=output_types,
                is_execute=False,
            )
        )
        return self

    def run(self) -> SimulationResult:
        non_empty_blocks = [b for b in self._blocks if b.calls]
        if not non_empty_blocks:
            raise ValueError("No calls buffered — call execute() or observe() first")

        block_state_calls: list[dict[str, Any]] = []
        for block in self._blocks:
            if not block.calls:
                continue
            entry: dict[str, Any] = {
                "calls": [self._serialize_call(c) for c in block.calls]
            }
            if block.block_overrides:
                entry["blockOverrides"] = block.block_overrides
            if block.state_overrides:
                entry["stateOverrides"] = dict(block.state_overrides)
            block_state_calls.append(entry)

        payload = [
            {
                "blockStateCalls": block_state_calls,
                "validation": self._validation,
                "traceTransfers": self._trace_transfers,
            },
            self._block if isinstance(self._block, str) else hex(int(self._block)),
        ]

        response = self._web3.provider.make_request(
            RPCEndpoint("eth_simulateV1"), payload
        )
        if "error" in response:
            err = response["error"]
            raise RuntimeError(f"eth_simulateV1 failed: {err}")

        return self._parse_response(response["result"])

    def _serialize_call(self, call: _Call) -> dict[str, Any]:
        out: dict[str, Any] = {
            "to": call.to,
            "input": "0x" + call.data.hex(),
        }
        if call.from_:
            out["from"] = call.from_
        return out

    def _parse_response(self, result: list[dict]) -> SimulationResult:
        # Flatten the multi-block response back into the order calls were queued.
        sources: list[_Call] = [c for b in self._blocks for c in b.calls]
        raw_calls: list[dict] = []
        for block_result in result:
            raw_calls.extend(block_result.get("calls", []))

        execute_success = True
        revert_reason: str | None = None
        execute_gas = 0
        execute_logs: list[dict] = []
        observations: dict[str, Any] = {}
        parsed: list[SimulatedCallResult] = []

        for source, raw in zip(sources, raw_calls):
            return_hex = raw.get("returnData", "0x")
            return_data = HexBytes(return_hex)
            status = int(raw.get("status", "0x1"), 16)
            success = status == 1
            gas_used = int(raw.get("gasUsed", "0x0"), 16)
            error = raw.get("error")
            logs = raw.get("logs", []) or []

            decoded: Any | None = None
            if success and source.decode_types and return_data:
                try:
                    values = tuple(decode(source.decode_types, bytes(return_data)))
                    decoded = values[0] if len(values) == 1 else values
                except (DecodingError, OverflowError, ValueError):
                    decoded = None

            parsed.append(
                SimulatedCallResult(
                    label=source.label,
                    success=success,
                    return_data=return_data,
                    gas_used=gas_used,
                    error=error if isinstance(error, str) else None,
                    logs=logs,
                    decoded=decoded,
                )
            )

            if source.is_execute:
                execute_success = execute_success and success
                execute_gas += gas_used
                execute_logs.extend(logs)
                if not success and revert_reason is None:
                    revert_reason = _decode_revert(return_data, error)
            elif source.label is not None and success:
                observations[source.label] = decoded

        failed_calls = [c for c in parsed if not c.success]
        all_success = not failed_calls
        if revert_reason is None and failed_calls:
            # No execute call reverted but a setup/observation did — surface it.
            first_failed = failed_calls[0]
            revert_reason = _decode_revert(first_failed.return_data, first_failed.error)

        return SimulationResult(
            success=execute_success,
            all_success=all_success,
            revert_reason=revert_reason,
            gas_used=execute_gas,
            execute_logs=execute_logs,
            observations=observations,
            calls=parsed,
            failed_calls=failed_calls,
        )


def _default_return_type(signature: str) -> str:
    name = signature.split("(", 1)[0].lower()
    if name in {
        "balanceof",
        "totalassets",
        "totalsupply",
        "decimals",
        "maxwithdraw",
        "maxredeem",
        "maxdeposit",
        "maxmint",
        "converttoshares",
        "converttoassets",
        "previewdeposit",
        "previewwithdraw",
        "previewmint",
        "previewredeem",
        "totalassetsinmarket",
    }:
        return "uint256"
    if name in {"asset", "owner"}:
        return "address"
    return "bytes"


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
        return f"custom error 0x{selector.hex()}"
    return error

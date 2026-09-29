from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from eth_abi import decode, encode
from eth_abi.exceptions import DecodingError
from eth_typing import ChecksumAddress
from eth_utils import keccak
from hexbytes import HexBytes
from web3 import Web3
from web3.types import BlockIdentifier, RPCEndpoint
from web3.utils.address import get_create_address

from ipor_fusion.core.contract import Call, _encode_calldata
from ipor_fusion.errors import SimulationError, decode_custom_error
from ipor_fusion.fuses.base import ZERO_ADDRESS, FuseAction

DEFAULT_BLOCK_TIME_INCREMENT = 12


@dataclass(slots=True)
class _Call:
    to: ChecksumAddress | None
    data: bytes
    label: str | None
    decode_types: list[str] | None
    decoder: Callable[..., Any] | None
    is_execute: bool
    from_: ChecksumAddress | None = None
    nonce: int | None = None
    value: int | None = None
    gas: int | None = None
    predicted_address: ChecksumAddress | None = None


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
    predicted_address: ChecksumAddress | None = None


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

    def raise_for_failure(self) -> None:
        """Raise :class:`SimulationError` naming the first failed call (label,
        client error, decoded revert reason); a no-op when every call succeeded."""
        if not self.failed_calls:
            return
        first = self.failed_calls[0]
        reason = _decode_revert(first.return_data, first.error)
        label = first.label or "<unlabelled>"
        detail = first.error or "reverted"
        if reason and reason != first.error:
            detail = f"{detail}: {reason}"
        raise SimulationError(
            f"simulated call {label!r} failed: {detail}",
            label=first.label,
            revert_reason=reason,
        )


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
        sim.observe("ltv_before", aave_pool.get_user_account_data(vault))
        sim.execute([flash_loan_action])
        sim.observe("ltv_after", aave_pool.get_user_account_data(vault))

        result = sim.run()
        if result.all_success and is_health_safe(result.get("ltv_after")):
            vault.execute([flash_loan_action]).send()  # green-light real execution

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

    @property
    def has_calls(self) -> bool:
        """Whether anything is buffered; ``run()`` refuses an empty batch."""
        return any(block.calls for block in self._blocks)

    @property
    def current_time(self) -> int:
        """Timestamp of the latest sent block or an explicit pending time.

        A pending block without an explicit time will run 12 seconds later
        once it contains a call.
        """
        return self._current_time()

    @property
    def current_block_number(self) -> int:
        """Pinned block number plus the number of blocks containing calls.

        A numeric pinned block is required because tags such as ``latest`` do
        not identify a stable parent block number.
        """
        if not isinstance(self._block, int):
            raise ValueError("current_block_number requires a numeric pinned block")
        return self._block + sum(bool(block.calls) for block in self._blocks)

    def _baseline(self) -> int:
        """Pinned-block timestamp; cached so multi-block batches stay consistent."""
        if self._baseline_timestamp is None:
            self._baseline_timestamp = int(
                self._web3.eth.get_block(self._block)["timestamp"]
            )
        return self._baseline_timestamp

    def with_block_time_shift(self, seconds: int) -> VaultSimulator:
        """Override the current block's `time` to baseline + seconds."""
        self._current.block_overrides["time"] = hex(self._baseline() + int(seconds))
        return self

    def with_block_override(self, **fields: Any) -> VaultSimulator:
        for key, value in fields.items():
            self._current.block_overrides[key] = (
                hex(value) if isinstance(value, int) else value
            )
        return self

    def with_state_override(
        self, address: ChecksumAddress, **overrides: Any
    ) -> VaultSimulator:
        """Merge an account override into the current block.

        Later fields and ``stateDiff`` slots win. ``state`` replaces storage;
        a later ``stateDiff`` patches it. One call cannot contain both forms.
        """
        checksum_address = Web3.to_checksum_address(address)
        current = self._current.state_overrides.get(checksum_address, {})
        self._current.state_overrides[checksum_address] = _compose_account_overrides(
            current, overrides
        )
        return self

    def with_erc20_balance(
        self,
        token: ChecksumAddress,
        holder: ChecksumAddress,
        amount: int,
        *,
        slot: int,
    ) -> VaultSimulator:
        """Set ``holder``'s balance of ``token`` to ``amount`` in the current
        block by overriding the ``balances`` mapping entry at storage ``slot``
        (see ``erc20_balance_slot``). Other overrides on the token are kept."""
        return self.with_state_override(
            token,
            stateDiff={
                _mapping_key(holder, slot): "0x" + amount.to_bytes(32, "big").hex()
            },
        )

    def next_block(self, time_shift_seconds: int | None = None) -> VaultSimulator:
        """Seal the current block and open a new one, optionally shifted in time.

        State carries from the previous block — same semantics as `evm_mine` after
        `evm_increaseTime` on a fork. Use this when an action needs to occur in a
        later block (e.g. cooldown periods, accrual).
        """
        new_block = _Block()
        if time_shift_seconds is not None:
            prev_time = self._current_time()
            next_time = prev_time + int(time_shift_seconds)
            if next_time <= prev_time:
                raise ValueError("next block time must be strictly increasing")
            new_block.block_overrides["time"] = hex(next_time)
        self._blocks.append(new_block)
        return self

    def _current_time(self) -> int:
        """Resolve the current modeled timestamp.

        Clients advance a simulated block without an explicit timestamp by 12
        seconds. Empty blocks are folded into the next sent block, so they do
        not consume that increment.
        """
        current_time = self._baseline()
        pending_time: int | None = None
        for block in self._blocks:
            if "time" in block.block_overrides:
                pending_time = int(block.block_overrides["time"], 16)
            if block.calls:
                current_time = (
                    pending_time
                    if pending_time is not None
                    else current_time + DEFAULT_BLOCK_TIME_INCREMENT
                )
                pending_time = None
        return pending_time if pending_time is not None else current_time

    def execute(self, actions: list[FuseAction]) -> VaultSimulator:
        """Queue a default `execute((address,bytes)[])` batch on the vault, from alpha."""
        data = FuseAction.encode_execute_payload(actions, "execute((address,bytes)[])")
        return self._queue_execute(self._vault, data, self._alpha)

    def execute_call(
        self, call: Call, from_: ChecksumAddress | None = None
    ) -> VaultSimulator:
        """Queue a pre-encoded write call as an `execute`-style step. Used for
        e.g. `RewardsManager.claim_rewards(...)` where the wrapper differs from
        `PlasmaVault.execute`. `from_` defaults to alpha.
        """
        return self._queue_execute(
            call.to,
            call.data,
            Web3.to_checksum_address(from_) if from_ else self._alpha,
        )

    def _queue_execute(
        self, to: ChecksumAddress, data: bytes, from_: ChecksumAddress
    ) -> VaultSimulator:
        self._current.calls.append(
            _Call(
                to=to,
                data=data,
                from_=from_,
                label=None,
                decode_types=None,
                decoder=None,
                is_execute=True,
            )
        )
        return self

    def add_call(
        self,
        call: Call,
        from_: ChecksumAddress | None = None,
        label: str | None = None,
    ) -> VaultSimulator:
        """Queue an arbitrary write/setup call (role grant, config tweak,
        impersonated deposit). Build via a wrapper method, e.g.
        `access_manager.grant_role(...)` or `usdc.approve(...)`.
        """
        self._current.calls.append(
            _Call(
                to=call.to,
                data=call.data,
                from_=Web3.to_checksum_address(from_) if from_ else None,
                label=label,
                decode_types=call.output_types,
                decoder=call.decoder,
                is_execute=False,
            )
        )
        return self

    def deploy_contract(
        self,
        init_code: bytes,
        *,
        from_: ChecksumAddress,
        nonce: int,
        value: int = 0,
        gas: int | None = None,
        label: str | None = None,
    ) -> ChecksumAddress:
        """Queue an EVM ``CREATE`` transaction and return its predicted address.

        ``init_code`` is creation bytecode with ABI-encoded constructor arguments
        appended. The returned address is correct only when the sender's
        simulated state nonce equals ``nonce`` when this call executes. Pin the
        starting nonce with ``with_state_override(from_, nonce=hex(nonce))`` and
        deploy sequentially without interleaving other calls from that sender.
        """
        if not init_code:
            raise ValueError("init_code must not be empty")
        if nonce < 0:
            raise ValueError(f"nonce must be non-negative, got {nonce}")
        if value < 0:
            raise ValueError(f"value must be non-negative, got {value}")
        if gas is not None and gas <= 0:
            raise ValueError(f"gas must be positive, got {gas}")
        sender = Web3.to_checksum_address(from_)
        predicted_address = get_create_address(sender, nonce)
        self._current.calls.append(
            _Call(
                to=None,
                data=bytes(init_code),
                from_=sender,
                nonce=nonce,
                value=value,
                gas=gas,
                label=label,
                decode_types=None,
                decoder=None,
                is_execute=False,
                predicted_address=predicted_address,
            )
        )
        return predicted_address

    def observe(self, label: str, call: Call) -> VaultSimulator:
        """Queue a view-style read; decoded per the wrapper method's types and
        decoder so `result.get(label)` returns the typed Python value
        (`Amount`, `Decimals`, …). Build `call` via a wrapper method, e.g.
        `usdc.balance_of(addr)` or `plasma_vault.total_assets()`.
        """
        if not call.output_types:
            raise ValueError(
                f"observe({label!r}): Call must carry output_types — pass a "
                "view-returning wrapper method (e.g. `usdc.balance_of(addr)`)."
            )
        self._current.calls.append(
            _Call(
                to=call.to,
                data=call.data,
                label=label,
                decode_types=call.output_types,
                decoder=call.decoder,
                is_execute=False,
            )
        )
        return self

    def run(self) -> SimulationResult:
        non_empty_blocks = [b for b in self._blocks if b.calls]
        if not non_empty_blocks:
            raise ValueError("No calls buffered — call execute() or observe() first")

        block_state_calls: list[dict[str, Any]] = []
        # A block with no calls is not sent, but its overrides still apply to
        # everything after it (state carries between blocks), so they fold
        # into the next block that is sent; that block's own values win.
        block_overrides: dict[str, Any] = {}
        state_overrides: dict[str, dict[str, Any]] = {}
        modeled_time = self._baseline()
        previous_sent_time = modeled_time
        for block in self._blocks:
            block_overrides = {**block_overrides, **block.block_overrides}
            for address, fields in block.state_overrides.items():
                state_overrides[address] = _compose_account_overrides(
                    state_overrides.get(address, {}), fields
                )
            if not block.calls:
                continue
            if "time" in block_overrides:
                modeled_time = int(block_overrides["time"], 16)
            else:
                modeled_time += DEFAULT_BLOCK_TIME_INCREMENT
                block_overrides["time"] = hex(modeled_time)
            if modeled_time <= previous_sent_time:
                raise ValueError("simulated block times must be strictly increasing")
            previous_sent_time = modeled_time
            entry: dict[str, Any] = {
                "calls": [self._serialize_call(c) for c in block.calls]
            }
            entry["blockOverrides"] = block_overrides
            if state_overrides:
                entry["stateOverrides"] = state_overrides
            block_state_calls.append(entry)
            block_overrides, state_overrides = {}, {}

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
        out: dict[str, Any] = {"input": "0x" + call.data.hex()}
        if call.to is not None:
            out["to"] = call.to
        if call.from_:
            out["from"] = call.from_
        if call.nonce is not None:
            out["nonce"] = hex(call.nonce)
        if call.value:
            out["value"] = hex(call.value)
        if call.gas is not None:
            out["gas"] = hex(call.gas)
        return out

    def _parse_response(self, result: list[dict]) -> SimulationResult:
        # Flatten the multi-block response back into the order calls were queued.
        sources: list[_Call] = [c for b in self._blocks for c in b.calls]
        raw_calls: list[dict] = []
        for block_result in result:
            raw_calls.extend(block_result.get("calls", []))
        if len(raw_calls) != len(sources):
            raise RuntimeError(
                "eth_simulateV1 returned "
                f"{len(raw_calls)} call results for {len(sources)} queued calls"
            )

        execute_success = True
        revert_reason: str | None = None
        execute_gas = 0
        execute_logs: list[dict] = []
        observations: dict[str, Any] = {}
        parsed: list[SimulatedCallResult] = []

        for source, raw in zip(sources, raw_calls, strict=True):
            return_hex = raw.get("returnData", "0x")
            return_data = HexBytes(return_hex)
            status = int(raw.get("status", "0x1"), 16)
            success = status == 1
            gas_used = int(raw.get("gasUsed", "0x0"), 16)
            error, return_data = _normalize_call_error(raw.get("error"), return_data)
            logs = raw.get("logs", []) or []

            decoded: Any | None = None
            if success and source.decode_types and return_data:
                try:
                    values = tuple(decode(source.decode_types, bytes(return_data)))
                    raw_value = values[0] if len(values) == 1 else values
                    decoded = (
                        source.decoder(raw_value)
                        if source.decoder is not None
                        else raw_value
                    )
                except (DecodingError, OverflowError, ValueError):
                    decoded = None

            parsed.append(
                SimulatedCallResult(
                    label=source.label,
                    success=success,
                    return_data=return_data,
                    gas_used=gas_used,
                    error=error,
                    logs=logs,
                    decoded=decoded,
                    predicted_address=(source.predicted_address if success else None),
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


def _compose_account_overrides(
    earlier: dict[str, Any], later: dict[str, Any]
) -> dict[str, Any]:
    for fields in (earlier, later):
        if "state" in fields and "stateDiff" in fields:
            raise ValueError("account override cannot contain both state and stateDiff")

    merged = {**earlier, **later}
    if "state" in later:
        merged["state"] = dict(later["state"])
        merged.pop("stateDiff", None)
    elif "stateDiff" in later:
        if "state" in earlier:
            merged["state"] = {**earlier["state"], **later["stateDiff"]}
            merged.pop("stateDiff", None)
        else:
            merged["stateDiff"] = {
                **earlier.get("stateDiff", {}),
                **later["stateDiff"],
            }
    return merged


def _mapping_key(holder: str, slot: int) -> str:
    """Storage key of ``mapping(address => uint256)`` entry ``holder`` at ``slot``."""
    return "0x" + keccak(encode(["address", "uint256"], [holder, slot])).hex()


def erc20_balance_slot(
    web3: Web3,
    token: ChecksumAddress,
    *,
    block: BlockIdentifier = "latest",
    max_slot: int = 32,
) -> int:
    """Find the storage slot of ``token``'s ``balances`` mapping by overriding
    every candidate in one ``eth_simulateV1`` request, using a distinct holder
    per slot, until ``balanceOf`` reflects the override (slot 9 for Circle's
    FiatToken, 0 for OpenZeppelin ERC20). Raises ``ValueError`` when no slot up
    to ``max_slot`` answers, which is the case for tokens whose balances are not
    a plain address-keyed mapping."""
    probe = 0x1234_5678_9ABC
    no_slot = f"no balances mapping slot found for {token} up to {max_slot}"
    if max_slot < 0:
        raise ValueError(no_slot)

    zero: Any = ZERO_ADDRESS
    sim = VaultSimulator(web3, vault=zero, alpha=zero, block=block)
    for slot in range(max_slot + 1):
        holder = Web3.to_checksum_address(
            "0x" + keccak(encode(["uint256"], [slot]))[-20:].hex()
        )
        sim.with_erc20_balance(token, holder, probe, slot=slot)
        sim.observe(
            f"balance_slot_{slot}",
            Call(
                to=token,
                data=_encode_calldata("balanceOf(address)", holder),
                output_types=["uint256"],
            ),
        )

    result = sim.run()
    for slot in range(max_slot + 1):
        if result.get(f"balance_slot_{slot}") == probe:
            return slot
    raise ValueError(no_slot)


def _normalize_call_error(
    error_raw: object, return_data: HexBytes
) -> tuple[str | None, HexBytes]:
    """Normalize a per-call `error` into (message, revert payload).

    Per the eth_simulateV1 spec, a failed call carries a JSON-RPC error object
    `{code, message, data}` — the revert payload is in `error.data`, and some
    clients leave `returnData` empty in that case. Other clients report the
    error as a plain string and put the revert payload in `returnData`.
    """
    if isinstance(error_raw, dict):
        if not return_data and error_raw.get("data"):
            return_data = HexBytes(error_raw["data"])
        return error_raw.get("message"), return_data
    return (error_raw if isinstance(error_raw, str) else None), return_data


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

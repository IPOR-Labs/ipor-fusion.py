from __future__ import annotations

from collections.abc import Callable, Mapping
from dataclasses import dataclass, field
from typing import Final, Generic, NotRequired, TypedDict, TypeVar, Unpack

from eth_abi import encode
from eth_typing import ChecksumAddress
from eth_utils import keccak
from hexbytes import HexBytes
from web3 import Web3
from web3.types import BlockIdentifier, Nonce, RPCEndpoint, Timestamp, TxParams
from web3.utils.address import get_create_address

from ipor_fusion.core.contract import Call, _encode_calldata
from ipor_fusion.core.fuse_action import FuseAction
from ipor_fusion.core.simulation_result import (
    SimulatedCallResult,
    SimulationResult,
    parse_simulate_response,
)
from ipor_fusion.errors import (
    BalanceSlotNotFoundError,
    EmptyCallResultError,
    SimulationError,
    SimulationRunError,
)
from ipor_fusion.types import PlasmaVaultAddress

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)


class AccountOverride(TypedDict, total=False):
    """One account's ``eth_simulateV1`` state override, sent as given:
    quantities and code are ``0x`` hex strings, storage maps a ``0x`` slot to a
    ``0x`` word. ``state`` replaces the account's storage, ``stateDiff``
    patches it."""

    balance: str
    nonce: str
    code: str
    state: Mapping[str, str]
    stateDiff: Mapping[str, str]
    movePrecompileToAddress: str


class BlockOverrides(TypedDict, total=False):
    """``eth_simulateV1`` block overrides; an ``int`` is sent as ``0x`` hex."""

    number: int | str
    time: int | str
    gasLimit: int | str
    feeRecipient: str
    prevRandao: str
    baseFeePerGas: int | str
    blobBaseFee: int | str


#: ``n`` (the sent block's index in the run) -> account overrides for it.
StateOverrideProvider = Callable[[int], Mapping[str, AccountOverride]]


class _AccessListEntry(TypedDict):
    address: ChecksumAddress
    storageKeys: list[str]


# One entry of a block's ``calls``; functional syntax because ``from`` is a
# keyword. ``to`` is absent for a contract creation.
_SimulatedCall = TypedDict(
    "_SimulatedCall",
    {
        "from": NotRequired[ChecksumAddress],
        "to": NotRequired[ChecksumAddress],
        "input": str,
        "nonce": NotRequired[str],
        "gas": NotRequired[str],
        "value": NotRequired[str],
        "chainId": NotRequired[str],
        "type": NotRequired[str],
        "gasPrice": NotRequired[str],
        "maxFeePerGas": NotRequired[str],
        "maxPriorityFeePerGas": NotRequired[str],
        "accessList": NotRequired[list[_AccessListEntry]],
    },
)


class _BlockStateCall(TypedDict):
    calls: list[_SimulatedCall]
    blockOverrides: dict[str, str]
    stateOverrides: NotRequired[dict[str, AccountOverride]]


class _SimulateRequest(TypedDict):
    blockStateCalls: list[_BlockStateCall]
    validation: bool
    traceTransfers: bool


DEFAULT_BLOCK_TIME_INCREMENT = 12

_TRANSACTION_QUANTITIES: Final = (
    "nonce",
    "gas",
    "value",
    "chainId",
    "type",
    "gasPrice",
    "maxFeePerGas",
    "maxPriorityFeePerGas",
)
_TRANSACTION_KEYS = {
    "from",
    "to",
    "data",
    "input",
    "accessList",
    *_TRANSACTION_QUANTITIES,
}


@dataclass(slots=True)
class _Call:
    to: ChecksumAddress | None
    data: bytes
    label: str | None
    is_execute: bool
    from_: ChecksumAddress | None = None
    nonce: int | None = None
    value: int | None = None
    gas: int | None = None
    predicted_address: ChecksumAddress | None = None
    # A complete serialized transaction (`add_transaction`), sent as is.
    transaction: _SimulatedCall | None = None
    observation: SimulationObservation[object] | None = None


@dataclass(slots=True)
class _Block:
    calls: list[_Call] = field(default_factory=list)
    block_overrides: dict[str, str] = field(default_factory=dict)
    state_overrides: dict[str, AccountOverride] = field(default_factory=dict)
    override_provider: StateOverrideProvider | None = None


class SimulationObservation(Generic[T_co]):
    """A read queued by `VaultSimulator.observe`, resolved by each `run()`:
    ``value`` is the read decoded as its `Call` decodes, from the latest run.
    Every `run()` unresolves it when it starts and resolves it only once the
    whole response has parsed, so a failed run leaves it unresolved rather
    than holding an earlier run's answer."""

    __slots__ = ("_call", "_label", "_result")

    def __init__(self, call: Call[T_co], label: str | None) -> None:
        self._call = call
        self._label = label
        self._result: SimulatedCallResult | None = None

    @property
    def label(self) -> str | None:
        return self._label

    @property
    def result(self) -> SimulatedCallResult:
        """The raw simulated call (success, return data, gas, logs). Raises
        ``RuntimeError`` until a `run()` has completed, and after a failed one."""
        if self._result is None:
            raise RuntimeError(
                f"observation {self._label or '<unlabelled>'!r} has no result: "
                "no VaultSimulator.run() has completed since it was queued, or "
                "the latest run failed"
            )
        return self._result

    @property
    def value(self) -> T_co:
        """The decoded read. Raises `SimulationError` when the simulated read
        reverted and `EmptyCallResultError` when it returned no data."""
        result = self.result
        name = self._label or "<unlabelled>"
        if not result.success:
            raise SimulationError(
                f"observation {name!r} failed: {result.error}",
                label=self._label,
                revert_reason=result.revert_reason,
            )
        if not result.return_data:
            raise EmptyCallResultError(
                f"observation {name!r} on {self._call.to} returned no data"
            )
        return self._call.decode(bytes(result.return_data))


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
        ltv_before = sim.observe(aave_pool.get_user_account_data(vault))
        sim.execute([flash_loan_action])
        ltv_after = sim.observe(aave_pool.get_user_account_data(vault))

        result = sim.run()
        if result.all_success and is_health_safe(ltv_after.value):
            vault.execute([flash_loan_action]).send(ctx)  # green-light real execution

    Limitations:
      - State does not survive across separate `run()` calls (each is fresh)
      - Read values cannot be threaded into later call calldata in the same batch
      - Up to 256 blocks per batch (eth_simulateV1 limit)
      - Requires geth/reth-based provider (Polygon Bor / zkSync etc. unsupported)
    """

    def __init__(
        self,
        web3: Web3,
        *,
        vault: PlasmaVaultAddress | None = None,
        alpha: ChecksumAddress | None = None,
        block: BlockIdentifier = "latest",
        validation: bool = False,
        trace_transfers: bool = False,
    ):
        self._web3 = web3
        self._vault = vault
        self._alpha = alpha
        self._block: BlockIdentifier = block
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
    def current_time(self) -> Timestamp:
        """Timestamp of the latest sent block or an explicit pending time.

        A pending block without an explicit time will run 12 seconds later
        once it contains a call.
        """
        return Timestamp(self._current_time())

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

    def with_block_override(self, **fields: Unpack[BlockOverrides]) -> VaultSimulator:
        """Set block overrides on the current block, in the order given."""
        for key, value in fields.items():
            if isinstance(value, int):
                value = hex(value)
            elif not isinstance(value, str):
                raise TypeError(
                    f"block override {key} must be an int or a hex string, "
                    f"got {type(value).__name__}"
                )
            self._current.block_overrides[key] = value
        return self

    def with_state_override(
        self, address: ChecksumAddress, **overrides: Unpack[AccountOverride]
    ) -> VaultSimulator:
        """Merge an account override into the current block.

        Later fields and ``stateDiff`` slots win. ``state`` replaces storage;
        a later ``stateDiff`` patches it. One call cannot contain both forms.
        """
        checksum_address = Web3.to_checksum_address(address)
        current = self._current.state_overrides.get(checksum_address)
        self._current.state_overrides[checksum_address] = _compose_account_overrides(
            current or AccountOverride(), overrides
        )
        return self

    def with_state_override_provider(
        self, provider: StateOverrideProvider
    ) -> VaultSimulator:
        """Derive state overrides per sent block from this block on.

        ``provider(n)`` returns account overrides for the ``n``-th block that
        carries calls (0 for the first in the run), merged under the block's
        own overrides. A later provider replaces an earlier one and ``n`` keeps
        counting across the swap. For state that must evolve with the simulated
        chain, such as a modelled precompile answering per block.
        """
        self._current.override_provider = provider
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
        if self._vault is None or self._alpha is None:
            raise ValueError("execute needs a simulator built with vault= and alpha=")
        data = FuseAction.encode_execute_payload(actions, "execute((address,bytes)[])")
        return self._queue_execute(self._vault, data, self._alpha)

    def execute_call(
        self, call: Call[object], from_: ChecksumAddress | None = None
    ) -> VaultSimulator:
        """Queue a pre-encoded write call as an `execute`-style step. Used for
        e.g. `RewardsManager.claim_rewards(...)` where the wrapper differs from
        `PlasmaVault.execute`. `from_` defaults to alpha.
        """
        sender = from_ or self._alpha
        if sender is None:
            raise ValueError(
                "execute_call needs from_ or a simulator built with alpha="
            )
        return self._queue_execute(call.to, call.data, sender)

    def _queue_execute(
        self, to: ChecksumAddress, data: bytes, from_: ChecksumAddress
    ) -> VaultSimulator:
        self._current.calls.append(
            _Call(
                to=to,
                data=data,
                from_=from_,
                label=None,
                is_execute=True,
            )
        )
        return self

    def add_call(
        self,
        call: Call[object],
        from_: ChecksumAddress | None = None,
        label: str | None = None,
        *,
        gas: int | None = None,
    ) -> VaultSimulator:
        """Queue an arbitrary write/setup call (role grant, config tweak,
        impersonated deposit). Build via a wrapper method, e.g.
        `access_manager.grant_role(...)` or `usdc.approve(...)`.

        ``gas`` caps the call explicitly; without it the node applies its own
        default (a few million on public providers), which a call that stores
        contract-sized blobs exceeds.
        """
        if gas is not None and gas <= 0:
            raise ValueError(f"gas must be positive, got {gas}")
        self._current.calls.append(
            _Call(
                to=call.to,
                data=call.data,
                from_=Web3.to_checksum_address(from_) if from_ else None,
                gas=gas,
                label=label,
                is_execute=False,
            )
        )
        return self

    def add_transaction(
        self, transaction: TxParams, label: str | None = None
    ) -> VaultSimulator:
        """Queue a complete transaction dict exactly as a signer would receive it,
        e.g. the output of `Call.build_transaction` or `Web3Context.build_transaction`.

        Every field is sent: nonce, gas, EIP-1559 fees, chainId, value and the
        access list, so the simulation runs the transaction that would be
        signed rather than a call rebuilt from its calldata. With
        ``validation=True`` the node also checks nonce, fees and balance. The
        result counts toward ``success`` and ``gas_used`` like `execute`. An
        unknown key raises instead of being dropped. The calldata may come as
        ``input`` instead of ``data``.
        """
        unknown = set(transaction) - _TRANSACTION_KEYS
        if unknown:
            raise ValueError(f"unsupported transaction fields: {sorted(unknown)}")
        if "data" in transaction and "input" in transaction:
            raise ValueError("transaction cannot carry both data and input")
        if not transaction.get("from") or not transaction.get("to"):
            raise ValueError("transaction requires from and to")
        data = HexBytes(_transaction_data(transaction))
        serialized = _serialize_transaction(transaction, data)
        self._current.calls.append(
            _Call(
                to=serialized["to"],
                data=bytes(data),
                from_=serialized["from"],
                label=label,
                is_execute=True,
                transaction=serialized,
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
        predicted_address = get_create_address(sender, Nonce(nonce))
        self._current.calls.append(
            _Call(
                to=None,
                data=bytes(init_code),
                from_=sender,
                nonce=nonce,
                value=value,
                gas=gas,
                label=label,
                is_execute=False,
                predicted_address=predicted_address,
            )
        )
        return predicted_address

    def observe(
        self, call: Call[T], *, label: str | None = None
    ) -> SimulationObservation[T]:
        """Queue a view-style read at this point of the flow and return its
        observation: after `run()`, ``observation.value`` is the read decoded
        as ``call`` decodes it (`Amount`, `Decimals`, …). Build ``call`` via a
        wrapper method, e.g. `usdc.balance_of(addr)` or
        `plasma_vault.total_assets()`. ``label`` names it in failures.
        """
        if not call.output_types:
            raise ValueError(
                f"observe({label!r}): Call must carry output_types — pass a "
                "view-returning wrapper method (e.g. `usdc.balance_of(addr)`)."
            )
        observation = SimulationObservation(call, label)
        self._current.calls.append(
            _Call(
                to=call.to,
                data=call.data,
                label=label,
                is_execute=False,
                observation=observation,
            )
        )
        return observation

    def run(self) -> SimulationResult:
        """Send every queued block in one ``eth_simulateV1`` request. Raises
        ``SimulationRunError`` when the request fails or the response is
        malformed; every observation is then left unresolved."""
        sources = [c for b in self._blocks for c in b.calls]
        for source in sources:
            if source.observation is not None:
                source.observation._result = None
        if not sources:
            raise ValueError("No calls buffered — call execute() or observe() first")

        request: _SimulateRequest = {
            "blockStateCalls": self._block_state_calls(),
            "validation": self._validation,
            "traceTransfers": self._trace_transfers,
        }
        payload = [
            request,
            self._block if isinstance(self._block, str) else hex(int(self._block)),
        ]

        response = self._web3.provider.make_request(
            RPCEndpoint("eth_simulateV1"), payload
        )
        if "error" in response:
            err = response["error"]
            raise SimulationRunError(f"eth_simulateV1 failed: {err}")

        result = parse_simulate_response(sources, response.get("result"))
        for source, call_result in zip(sources, result.calls, strict=True):
            if source.observation is not None:
                source.observation._result = call_result
        return result

    def _block_state_calls(self) -> list[_BlockStateCall]:
        block_state_calls: list[_BlockStateCall] = []
        # A block with no calls is not sent, but its overrides still apply to
        # everything after it (state carries between blocks), so they fold
        # into the next block that is sent; that block's own values win.
        block_overrides: dict[str, str] = {}
        state_overrides: dict[str, AccountOverride] = {}
        modeled_time = self._baseline()
        previous_sent_time = modeled_time
        provider: StateOverrideProvider | None = None
        sent_blocks = 0
        for block in self._blocks:
            provider = block.override_provider or provider
            block_overrides = {**block_overrides, **block.block_overrides}
            for address, fields in block.state_overrides.items():
                state_overrides[address] = _compose_account_overrides(
                    state_overrides.get(address, {}), fields
                )
            if not block.calls:
                continue
            _apply_override_provider(state_overrides, provider, sent_blocks)
            sent_blocks += 1
            if "time" in block_overrides:
                modeled_time = int(block_overrides["time"], 16)
            else:
                modeled_time += DEFAULT_BLOCK_TIME_INCREMENT
                block_overrides["time"] = hex(modeled_time)
            if modeled_time <= previous_sent_time:
                raise ValueError("simulated block times must be strictly increasing")
            previous_sent_time = modeled_time
            entry: _BlockStateCall = {
                "calls": [self._serialize_call(c) for c in block.calls],
                "blockOverrides": block_overrides,
            }
            if state_overrides:
                entry["stateOverrides"] = state_overrides
            block_state_calls.append(entry)
            block_overrides, state_overrides = {}, {}
        return block_state_calls

    def _serialize_call(self, call: _Call) -> _SimulatedCall:
        if call.transaction is not None:
            return call.transaction.copy()
        out: _SimulatedCall = {"input": "0x" + call.data.hex()}
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


def _transaction_data(transaction: TxParams) -> bytes | bytearray | str:
    """The calldata under ``data``, or under ``input``, which `TxParams` does
    not name but a node-shaped transaction carries."""
    if "data" in transaction:
        return transaction["data"]
    fields: Mapping[str, object] = transaction
    data = fields.get("input", b"")
    if not isinstance(data, bytes | bytearray | str):
        raise TypeError(f"transaction input must be bytes or hex, got {data!r}")
    return data


def _serialize_transaction(transaction: TxParams, data: HexBytes) -> _SimulatedCall:
    out: _SimulatedCall = {
        "from": Web3.to_checksum_address(transaction["from"]),
        "to": Web3.to_checksum_address(transaction["to"]),
        "input": data.to_0x_hex(),
    }
    for key in _TRANSACTION_QUANTITIES:
        value = transaction.get(key)
        if value is not None:
            out[key] = value if isinstance(value, str) else hex(value)
    if (access_list := transaction.get("accessList")) is not None:
        out["accessList"] = [
            {
                "address": Web3.to_checksum_address(entry["address"]),
                "storageKeys": [HexBytes(k).to_0x_hex() for k in entry["storageKeys"]],
            }
            for entry in access_list
        ]
    return out


def _apply_override_provider(
    overrides: dict[str, AccountOverride],
    provider: StateOverrideProvider | None,
    sent_block: int,
) -> None:
    """Merge the provider's overrides for this sent block under the block's own."""
    if provider is None:
        return
    for address, fields in provider(sent_block).items():
        overrides[address] = _compose_account_overrides(
            AccountOverride(**fields), overrides.get(address) or AccountOverride()
        )


def _compose_account_overrides(
    earlier: AccountOverride, later: AccountOverride
) -> AccountOverride:
    for fields in (earlier, later):
        if "state" in fields and "stateDiff" in fields:
            raise ValueError("account override cannot contain both state and stateDiff")

    merged: AccountOverride = {**earlier, **later}
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
    FiatToken, 0 for OpenZeppelin ERC20). Raises ``BalanceSlotNotFoundError``
    when no slot up to ``max_slot`` answers, which is the case for tokens whose
    balances are not a plain address-keyed mapping, and ``ValueError`` for a
    negative ``max_slot``."""
    probe = 0x1234_5678_9ABC
    if max_slot < 0:
        raise ValueError(f"max_slot must be non-negative, got {max_slot}")

    sim = VaultSimulator(web3, block=block)
    balances: list[SimulationObservation[int]] = []
    for slot in range(max_slot + 1):
        holder = Web3.to_checksum_address(
            "0x" + keccak(encode(["uint256"], [slot]))[-20:].hex()
        )
        sim.with_erc20_balance(token, holder, probe, slot=slot)
        balances.append(
            sim.observe(
                Call[int](
                    to=token,
                    data=_encode_calldata("balanceOf(address)", holder),
                    output_types=["uint256"],
                ),
                label=f"balance_slot_{slot}",
            )
        )

    sim.run()
    for slot, balance in enumerate(balances):
        if balance.result.success and balance.value == probe:
            return slot
    raise BalanceSlotNotFoundError(
        f"no balances mapping slot found for {token} up to {max_slot}"
    )

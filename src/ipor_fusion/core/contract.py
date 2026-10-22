from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from typing import Generic, TypeVar, cast

from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import BlockIdentifier, TxParams, TxReceipt

from ipor_fusion.core.context import Web3Context
from ipor_fusion.errors import EmptyCallResultError

T = TypeVar("T")
T_co = TypeVar("T_co", covariant=True)
U = TypeVar("U")


# No ``slots=True``: on early 3.11 and 3.12 patch releases (3.11.1, 3.12.0 and
# 3.12.1 checked) ``Call[int](...)`` on a slotted frozen generic dataclass
# raises ``TypeError`` when typing sets ``__orig_class__`` on the instance.
@dataclass(frozen=True)
class Call(Generic[T_co]):
    """A pre-encoded contract call: target, calldata, and (for views) the ABI
    output types + decoder to a typed Python return.

    Built by `ContractWrapper` methods (one per Solidity function) — instead of
    executing immediately, those methods return a `Call` so the same definition
    powers `eth_call` (`.call(ctx)`), `eth_sendTransaction` (`.send(ctx)`), and
    `eth_simulateV1` batching (`VaultSimulator.add_call`/`observe`).

    A `Call` is offline data: it carries no context and names no chain. The
    context passed to `.call`, `.send`, `.build_transaction` or
    `.estimate_gas` decides where it runs (and the chain id of the
    transaction), so a wrapper shared across chains, or an executor and
    dispatcher at the same address, needs the caller to pick the context.

    Example — read, send, and simulate share the same builder:

        balance = usdc.balance_of(addr).call(ctx)       # eth_call → Amount
        usdc.transfer(addr, amount).send(ctx)           # tx → TxReceipt
        after = sim.observe(usdc.balance_of(vault))      # eth_simulateV1
        sim.add_call(usdc.approve(spender, amount), from_=user)

    A `Call` is a hashable value: ``output_types`` is stored as a tuple
    whatever sequence it was given as. Equality compares the decoder by
    identity, so two separately built Calls with equivalent lambdas differ.
    """

    to: ChecksumAddress
    data: bytes
    output_types: Sequence[str] | None = None
    decoder: Callable[..., T_co] | None = None

    def __post_init__(self) -> None:
        if self.output_types is not None:
            # Frozen: normalizing the field is part of building the value.
            object.__setattr__(self, "output_types", tuple(self.output_types))

    @property
    def calldata(self) -> bytes:
        """Selector + ABI-encoded args as bytes. Use when handing the payload
        to an external signer (HTTP signing service, multisig flow) instead of
        routing through `.send(ctx)`. Pure data — no ctx required."""
        return self.data

    def map(self, transform: Callable[[T_co], U]) -> Call[U]:
        """Return the same call with ``transform`` applied to what ``call()`` returns."""
        if not self.output_types:
            raise ValueError("cannot map a write-only Call without output types")
        decoder = self.decoder

        def mapped(value: object) -> U:
            decoded = decoder(value) if decoder is not None else cast(T_co, value)
            return transform(decoded)

        return Call(
            to=self.to,
            data=self.data,
            output_types=self.output_types,
            decoder=mapped,
        )

    def call(self, ctx: Web3Context, *, block: BlockIdentifier | None = None) -> T_co:
        """Execute as `eth_call` on ``ctx`` at ``block`` (the context's default
        block when ``None``); decode and convert per `output_types`/`decoder`."""
        if not self.output_types:
            raise ValueError(
                "Call.call() on a write-only Call — use .send(ctx) instead "
                "(no output_types declared)."
            )
        # The default path keeps the two-argument call, so a context stand-in
        # that implements only `call(to, data)` still works.
        raw = (
            ctx.call(self.to, self.data)
            if block is None
            else ctx.call(self.to, self.data, block)
        )
        if len(raw) == 0:
            raise EmptyCallResultError(
                f"eth_call to {self.to} on chain {ctx.chain_id} returned no "
                f"data (selector 0x{self.data[:4].hex()}): no contract at this "
                "address at the requested block, or it does not implement the "
                "function."
            )
        return self.decode(raw)

    def decode(self, raw: bytes) -> T_co:
        """Decode raw return data per `output_types`/`decoder` — the second half
        of `.call(ctx)`, for return data fetched elsewhere (e.g. `Multicall3`)."""
        if not self.output_types:
            raise ValueError("Call.decode() on a write-only Call.")
        values = tuple(decode(self.output_types, bytes(raw)))
        single: object = values[0] if len(values) == 1 else values
        if self.decoder is not None:
            return self.decoder(single)
        return cast(T_co, single)

    def send(self, ctx: Web3Context) -> TxReceipt:
        """Submit as a transaction via ``ctx``'s signer."""
        return ctx.send(self.to, self.data)

    def build_transaction(
        self, ctx: Web3Context, *, block: BlockIdentifier = "latest"
    ) -> TxParams:
        """Build the transaction dict for this Call without signing or sending —
        mirrors `.send(ctx)`, delegating to `Web3Context.build_transaction`.
        Returns the dict a signer would submit; needs a signer address, no key."""
        return ctx.build_transaction(self.to, self.data, block=block)

    def estimate_gas(
        self, ctx: Web3Context, *, block: BlockIdentifier = "latest"
    ) -> int:
        """Estimate gas for this Call without signing or sending — mirrors
        `.send(ctx)`, delegating to `Web3Context.estimate_gas`. A would-revert
        Call raises the underlying revert."""
        return ctx.estimate_gas(self.to, self.data, block=block)


class ContractWrapper:
    """Base wrapper: a contract address and nothing chain-bound. Subclasses
    expose one method per Solidity function that returns a `Call[T]` — never
    executing. Callers run it with `.call(ctx)` for reads, `.send(ctx)` for
    writes, or hand the `Call` to `VaultSimulator` / `Multicall3`.

    A method that must execute to answer (a storage read, a log scan, a read
    that depends on another read) is named ``read_*`` and takes the context as
    its first argument; no other method carries the prefix.
    """

    def __init__(self, address: ChecksumAddress):
        self._address = Web3.to_checksum_address(address)

    @property
    def address(self) -> ChecksumAddress:
        return self._address

    def _view(
        self,
        signature: str,
        *args: object,
        output_types: Sequence[str],
        decoder: Callable[..., T] | None = None,
    ) -> Call[T]:
        return Call(
            to=self._address,
            data=_encode_calldata(signature, *args),
            output_types=output_types,
            decoder=decoder,
        )

    def _write(self, signature: str, *args: object) -> Call[None]:
        return Call(to=self._address, data=_encode_calldata(signature, *args))


def _encode_calldata(signature: str, *args: object) -> bytes:
    selector = function_signature_to_4byte_selector(signature)
    types = _parse_param_types(signature)
    return selector + encode(types, list(args)) if types else selector


def _parse_param_types(signature: str) -> list[str]:
    if not (params := signature[signature.index("(") + 1 : signature.rindex(")")]):
        return []
    result = []
    depth = 0
    current: list[str] = []
    for char in params:
        if char == "(":
            depth += 1
            current.append(char)
        elif char == ")":
            depth -= 1
            current.append(char)
        elif char == "," and depth == 0:
            result.append("".join(current).strip())
            current = []
        else:
            current.append(char)
    if last := "".join(current).strip():
        result.append(last)
    return result

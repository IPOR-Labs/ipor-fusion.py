from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from typing import Any, Generic, TypeVar, cast

from eth_abi import decode, encode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import TxReceipt

from ipor_fusion.core.context import Web3Context

T = TypeVar("T")


@dataclass(slots=True)
class Call(Generic[T]):
    """A pre-encoded contract call: target, calldata, and (for views) the ABI
    output types + decoder to a typed Python return.

    Built by `ContractWrapper` methods (one per Solidity function) — instead of
    executing immediately, those methods return a `Call` so the same definition
    powers `eth_call` (`.call()`), `eth_sendTransaction` (`.send()`), and
    `eth_simulateV1` batching (`VaultSimulator.add_call`/`observe`).

    Example — read, send, and simulate share the same builder:

        balance = usdc.balance_of(addr).call()          # eth_call → Amount
        usdc.transfer(addr, amount).send()              # tx → TxReceipt
        sim.observe("after", usdc.balance_of(vault))    # eth_simulateV1
        sim.add_call(call=usdc.approve(spender, amount), from_=user)

    The `ctx` carried inside is a *default* — `Call.call(other_ctx)` overrides
    it without rebuilding (useful for cross-context reads on the same address).
    """

    to: ChecksumAddress
    data: bytes
    output_types: list[str] | None = None
    decoder: Callable[..., T] | None = None
    ctx: Web3Context | None = None

    @property
    def calldata(self) -> bytes:
        """Selector + ABI-encoded args as bytes. Use when handing the payload
        to an external signer (HTTP signing service, multisig flow) instead of
        routing through `.send(ctx)`. Pure data — no ctx required."""
        return self.data

    def call(self, ctx: Web3Context | None = None) -> T:
        """Execute as `eth_call`; decode and convert per `output_types`/`decoder`."""
        actual = self._resolve_ctx(ctx)
        if not self.output_types:
            raise RuntimeError(
                "Call.call() on a write-only Call — use .send() instead "
                "(no output_types declared)."
            )
        raw = actual.call(self.to, self.data)
        values = tuple(decode(self.output_types, bytes(raw)))
        single: Any = values[0] if len(values) == 1 else values
        if self.decoder is not None:
            return self.decoder(single)
        return cast(T, single)

    def send(self, ctx: Web3Context | None = None) -> TxReceipt:
        """Submit as a transaction via the resolved context's signer."""
        actual = self._resolve_ctx(ctx)
        return actual.send(self.to, self.data)

    def _resolve_ctx(self, ctx: Web3Context | None) -> Web3Context:
        actual = ctx or self.ctx
        if actual is None:
            raise ValueError(
                "Web3Context required: pass it to .call(ctx)/.send(ctx) or "
                "build this Call via a wrapper instantiated with ctx."
            )
        return actual


class ContractWrapper:
    """Base wrapper. Subclasses expose one method per Solidity function that
    returns a `Call[T]` — never executing eagerly. Callers chain `.call()` for
    reads, `.send()` for writes, or hand the `Call` to `VaultSimulator`.
    """

    def __init__(self, ctx: Web3Context, address: ChecksumAddress):
        self._ctx = ctx
        self._address = Web3.to_checksum_address(address)

    @property
    def address(self) -> ChecksumAddress:
        return self._address

    def _view(
        self,
        signature: str,
        *args: Any,
        output_types: list[str],
        decoder: Callable[..., T] | None = None,
    ) -> Call[T]:
        return Call(
            to=self._address,
            data=_encode_calldata(signature, *args),
            output_types=output_types,
            decoder=decoder,
            ctx=self._ctx,
        )

    def _write(self, signature: str, *args: Any) -> Call[None]:
        return Call(
            to=self._address,
            data=_encode_calldata(signature, *args),
            ctx=self._ctx,
        )


def _encode_calldata(signature: str, *args: Any) -> bytes:
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

from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from eth_abi import decode as abi_decode
from eth_abi.exceptions import InsufficientDataBytes
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import TxReceipt

log = logging.getLogger(__name__)

PANIC_CODES: dict[int, str] = {
    0x00: "generic compiler panic",
    0x01: "assert failure",
    0x11: "arithmetic overflow/underflow",
    0x12: "division or modulo by zero",
    0x21: "enum conversion out of bounds",
    0x22: "incorrectly encoded storage byte array",
    0x31: "pop on empty array",
    0x32: "array index out of bounds",
    0x41: "out of memory",
    0x51: "zero-initialized function pointer",
}

ERROR_SELECTOR = bytes.fromhex("08c379a0")
PANIC_SELECTOR = bytes.fromhex("4e487b71")

#: Registered custom errors: selector -> (name, ABI parameter types).
CUSTOM_ERRORS: dict[bytes, tuple[str, tuple[str, ...]]] = {}

_SIGNATURE_RE = re.compile(r"^(\w+)\((.*)\)$")


def register_custom_errors(signatures: Iterable[str]) -> None:
    """Register ``Name(type,type)`` custom error signatures so their revert
    data decodes to ``Name(arg, arg)``. A later registration of the same
    selector replaces the earlier one."""
    for signature in signatures:
        match = _SIGNATURE_RE.fullmatch(signature.replace(" ", ""))
        if match is None:
            raise ValueError(f"not a custom error signature: {signature!r}")
        name, params = match.groups()
        types = tuple(param for param in params.split(",") if param)
        canonical = f"{name}({','.join(types)})"
        CUSTOM_ERRORS[function_signature_to_4byte_selector(canonical)] = (name, types)


def decode_custom_error(selector: bytes, payload: bytes) -> str | None:
    """``Name(arg, arg)`` for a registered selector, ``None`` otherwise."""
    registered = CUSTOM_ERRORS.get(bytes(selector))
    if registered is None:
        return None
    name, types = registered
    if not types:
        return f"{name}()"
    try:
        values = abi_decode(list(types), payload)
    except Exception:
        return f"{name}(<decode failed>: 0x{payload.hex()[:64]})"
    args = ", ".join(_format_arg(t, v) for t, v in zip(types, values, strict=True))
    return f"{name}({args})"


def _format_arg(abi_type: str, value: object) -> str:
    if abi_type == "address" and isinstance(value, str):
        return Web3.to_checksum_address(value)
    if isinstance(value, (bytes, bytearray)):
        return f"0x{bytes(value).hex()}"
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, str):
        return f'"{value}"'
    return str(value)


def _decode_revert_reason(data: bytes) -> str:
    """Decode raw revert bytes into a human-readable string."""
    if len(data) < 4:
        if data:
            return f"0x{data.hex()}"
        return "empty revert data"

    selector = data[:4]
    payload = data[4:]

    if selector == ERROR_SELECTOR:
        try:
            (reason,) = abi_decode(["string"], payload)
            return f'Error("{reason}")'
        except Exception:
            return f"Error(<decode failed>: 0x{payload.hex()[:64]})"

    if selector == PANIC_SELECTOR:
        try:
            (code,) = abi_decode(["uint256"], payload)
            description = PANIC_CODES.get(code, "unknown panic code")
            return f"Panic(0x{code:02x}: {description})"
        except Exception:
            return f"Panic(<decode failed>: 0x{payload.hex()[:64]})"

    custom = decode_custom_error(selector, payload)
    if custom is not None:
        return custom

    # Unknown selector — show truncated hex
    hex_str = f"0x{data.hex()}"
    if len(hex_str) > 72:
        hex_str = hex_str[:72] + "..."
    return hex_str


def get_revert_reason(web3: Web3, tx_hash: bytes, receipt: TxReceipt) -> str | None:
    """Replay a failed tx as eth_call to capture revert data."""
    try:
        tx = web3.eth.get_transaction(tx_hash)  # type: ignore[arg-type]
        call_params = {
            "from": tx["from"],
            "to": tx["to"],
            "data": tx["input"],
            "value": tx["value"],
        }
        if "gas" in tx:
            call_params["gas"] = tx["gas"]

        block_number = receipt["blockNumber"]
        web3.eth.call(call_params, block_identifier=block_number)  # type: ignore[arg-type]
        return None  # replay succeeded — can't determine reason
    except Exception as exc:
        exc_data = getattr(exc, "data", None)
        if isinstance(exc_data, str) and exc_data.startswith("0x"):
            raw = str(exc_data)
            return _decode_revert_reason(bytes.fromhex(raw[2:]))
        exc_message = str(exc)
        if (
            "revert" in exc_message.lower()
            or "execution reverted" in exc_message.lower()
        ):
            return exc_message
        log.debug("Could not decode revert reason: %s", exc)
        return None


class IporFusionError(Exception):
    """Base exception for all IPOR Fusion SDK errors."""


class ContractNotFoundError(IporFusionError, ValueError):
    """No contract deployed at the given address.

    Also a ValueError so MCP adapters can let it propagate unmapped.
    """


class NotPlasmaVaultError(IporFusionError, ValueError):
    """Contract exists but does not implement the Plasma Vault interface.

    Also a ValueError so MCP adapters can let it propagate unmapped.
    """


class UnsupportedChainError(IporFusionError, ValueError):
    """Chain is not (yet) supported by the on-chain vault tooling.

    Also a ValueError so MCP adapters can let it propagate unmapped.
    """


class EmptyCallResultError(IporFusionError, InsufficientDataBytes):
    """`eth_call` returned no data for a call that declares return values.

    Almost always there is no contract at the target address on the connected
    chain (or at the requested block); a contract whose catch-all fallback
    returns nothing looks the same. Subclasses eth_abi's InsufficientDataBytes
    — the error the ABI decoder used to raise — so existing handlers still
    catch it.
    """


class MorphoMarketNotFoundError(IporFusionError, ValueError):
    """Morpho Blue market ID was never created on the connected chain.

    Also a ValueError so MCP adapters can let it propagate unmapped.
    """


class SimulationError(IporFusionError):
    """A simulated call reverted; raised by ``SimulationResult.raise_for_failure``."""

    def __init__(
        self,
        message: str,
        *,
        label: str | None = None,
        revert_reason: str | None = None,
    ):
        self.label = label
        self.revert_reason = revert_reason
        super().__init__(message)


class TransactionError(IporFusionError):
    def __init__(
        self,
        message: str,
        tx_hash: str | None = None,
        revert_reason: str | None = None,
    ):
        self.tx_hash = tx_hash
        self.revert_reason = revert_reason
        parts = [message]
        if tx_hash:
            parts.append(f"tx_hash={tx_hash}")
        if revert_reason:
            parts.append(f"reason={revert_reason}")
        super().__init__(", ".join(parts))

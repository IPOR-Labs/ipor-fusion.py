from __future__ import annotations

import logging
import re
from collections.abc import Iterable

from eth_abi import decode as abi_decode
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.types import TxParams, TxReceipt

from ipor_fusion.types import TxHash

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


def get_revert_reason(web3: Web3, tx_hash: TxHash, receipt: TxReceipt) -> str | None:
    """Replay a failed tx as eth_call to capture revert data."""
    try:
        tx = web3.eth.get_transaction(tx_hash)
        call_params: TxParams = {
            "from": tx["from"],
            "to": tx["to"],
            "data": tx["input"],
            "value": tx["value"],
        }
        if "gas" in tx:
            call_params["gas"] = tx["gas"]

        block_number = receipt["blockNumber"]
        web3.eth.call(call_params, block_identifier=block_number)
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
    """Base of every exception the SDK defines.

    The rule the SDK follows everywhere: a subclass when the chain, a provider
    or a remote API answers something it cannot use. A caller's mistake (a bad
    argument, or an operation the object was not built for, such as
    ``.call(ctx)`` on a write-only ``Call``) raises a builtin ``ValueError``
    instead, as the standard library does for an operation on a closed file;
    an argument of the wrong type raises ``TypeError`` and an unknown name in a
    mapping-style lookup ``KeyError``. A subclass that also describes bad input
    (an address that is not a vault, a chain the tooling does not cover)
    additionally derives from ``ValueError``; others keep the builtin base
    their raise sites had before 4.0 (``ValueError``, ``RuntimeError``,
    ``LookupError``) so existing handlers still catch them.
    """


class ContractNotFoundError(IporFusionError, ValueError):
    """No contract deployed at the given address.

    Also a ValueError: the address the caller passed holds nothing to read.
    """


class NotPlasmaVaultError(IporFusionError, ValueError):
    """Contract exists but does not implement the Plasma Vault interface.

    Also a ValueError: the address the caller passed is not a vault.
    """


class UnsupportedChainError(IporFusionError, ValueError):
    """Chain is not (yet) supported by the on-chain vault tooling.

    Also a ValueError: the chain the caller passed is outside what the tooling
    covers.
    """


class UnsupportedVaultVersionError(IporFusionError, ValueError):
    """A Plasma Vault predates a getter the on-chain vault tooling requires.

    Also a ValueError: the vault the caller passed cannot be read by this
    tooling.
    """


class UnsupportedContractVersionError(IporFusionError, ValueError):
    """A contract reports a version whose layout the SDK has not verified, so
    reading it further could misread its storage or return data.

    Also a ValueError, the type these failures had before 4.0.
    """


class HyperCoreValuationError(IporFusionError, ValueError):
    """The HyperCore NAV cannot be valued: a precompile read failed, the
    oracle prices no granted spot token or its price overflows, the vault
    grants a perp dex bitmap the valuation does not support, or the NAV is
    negative (where the balance fuse reverts).

    Also a ValueError, the type these failures had before 4.0.
    """


class ExternalStateError(IporFusionError, ValueError):
    """The external-state market's on-chain data is not usable: the vault's
    storage holds no executor or a malformed word, or an executor's
    ``BalanceProposed`` log is malformed or carries a hash that is not the hash
    of its own fields.

    Also a ValueError, the type these failures had before 4.0.
    """


class ExternalStateEventDecodeError(IporFusionError, ValueError):
    """A log carries a listed ExternalState topic but its data does not decode
    to the declared parameters: malformed, never a plausible event. Also a
    ValueError: the log the caller passed is not a decodable event."""


class HyperCoreEventDecodeError(IporFusionError, ValueError):
    """A log carries a listed HyperCore topic but its data does not decode to
    the declared parameters: malformed, never a plausible event. Also a
    ValueError: the log the caller passed is not a decodable event."""


class OwnerNotFoundError(IporFusionError, LookupError):
    """An access manager has no account holding ``OWNER_ROLE`` (every owner
    was revoked or renounced). Also a LookupError: the owner looked up is not
    there."""


class BalanceSlotNotFoundError(IporFusionError, ValueError):
    """No candidate storage slot of a token answers as its ``balances``
    mapping: its balances are not a plain address-keyed mapping, or the
    mapping lies beyond the slots searched.

    Also a ValueError, the type this failure had before 4.0.
    """


class LogScanError(IporFusionError):
    """An `eth_getLogs` scan could not cover its whole block range.

    Raised instead of returning a partial result: the provider kept rejecting
    the range even one block at a time, or the scan ran out of its time or
    request budget. ``address``, ``from_block`` and ``to_block``
    name the unscanned range.
    """

    def __init__(
        self, message: str, *, address: ChecksumAddress, from_block: int, to_block: int
    ):
        self.address = address
        self.from_block = from_block
        self.to_block = to_block
        super().__init__(message)


class LogSourceUnavailable(IporFusionError):
    """A `LogSource` could not answer completely; `Web3Context.get_logs`
    falls back to its own `eth_getLogs` scan."""


class EmptyCallResultError(IporFusionError):
    """`eth_call` returned no data for a call that declares return values.

    Almost always there is no contract at the target address on the connected
    chain (or at the requested block); a contract whose catch-all fallback
    returns nothing looks the same. Return data that is present but too short
    still raises eth_abi's own decoding error.
    """


class MorphoMarketNotFoundError(IporFusionError, ValueError):
    """Morpho Blue market ID was never created on the connected chain.

    Also a ValueError: the market id the caller passed names no market.
    """


class MorphoApiError(IporFusionError, RuntimeError):
    """The Morpho API is unreachable, returned an error, or answered with a
    response the client cannot parse (missing keys, wrong types, invalid
    addresses or market ids). Also a RuntimeError, its base before 4.0."""


class AddressRegistryError(IporFusionError, RuntimeError):
    """The ipor-abi address registry is unreachable or its file does not parse
    as a ``{name: address}`` map."""


class AddressNotInRegistryError(IporFusionError, LookupError):
    """A chain's address registry has no contract by the requested name."""


class CrosschainDiscoveryError(IporFusionError):
    """A crosschain vault's on-chain configuration is not one discovery can
    use: a malformed or unexpected market grant, an executor managed by another
    vault or of no known transport, or a missing or duplicate lane fuse."""


class VerifierResultUnavailable(IporFusionError, LookupError):
    """No indexer could serve a verifier result for the message (not verified
    yet, the indexers were unreachable, or they answered something unreadable);
    the message says which, per indexer. Also a LookupError: the result looked
    up is not there (yet)."""


class SimulationRunError(IporFusionError, RuntimeError):
    """A simulation could not run to completion: the provider rejected
    ``eth_simulateV1`` or answered with a different number of call results than
    were queued, or a crosschain relay was still producing messages after its
    last round. Distinct from a simulated call that reverts (``SimulationError``).
    """


class SimulationError(IporFusionError):
    """A simulated call reverted; raised by ``SimulationResult.raise_for_failure``,
    by ``SimulationObservation.value`` for a reverted read and by
    ``quote_ccip_native_fee`` for a revert before the CCIP fee check."""

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
        tx_hash: TxHash | None = None,
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

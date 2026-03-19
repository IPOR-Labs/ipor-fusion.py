from __future__ import annotations

import logging

from eth_abi import decode as abi_decode
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
        except Exception:  # pylint: disable=broad-except
            return f"Error(<decode failed>: 0x{payload.hex()[:64]})"

    if selector == PANIC_SELECTOR:
        try:
            (code,) = abi_decode(["uint256"], payload)
            description = PANIC_CODES.get(code, "unknown panic code")
            return f"Panic(0x{code:02x}: {description})"
        except Exception:  # pylint: disable=broad-except
            return f"Panic(<decode failed>: 0x{payload.hex()[:64]})"

    # Unknown selector — show truncated hex
    hex_str = f"0x{data.hex()}"
    if len(hex_str) > 72:
        hex_str = hex_str[:72] + "..."
    return hex_str


def _get_revert_reason(web3: Web3, tx_hash: bytes, receipt: TxReceipt) -> str | None:
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
    except Exception as exc:  # pylint: disable=broad-except
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
    pass


class UnsupportedFuseError(IporFusionError):
    def __init__(self, fuse_name: str):
        self.fuse_name = fuse_name
        super().__init__(f"Fuse not supported: {fuse_name}")


class UnsupportedAssetError(IporFusionError):
    def __init__(self, asset: str):
        self.asset = asset
        super().__init__(f"Unsupported asset: {asset}")


class UnsupportedMarketError(IporFusionError):
    def __init__(self, market: str):
        self.market = market
        super().__init__(f"Unsupported market: {market}")


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


class ConfigurationError(IporFusionError):
    pass

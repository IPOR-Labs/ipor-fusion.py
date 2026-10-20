"""Verifier results of CCIP 2.0 messages, from the indexers Chainlink Labs
runs over the committee's storage, and the permissionless execution they
enable.

A message is executable on the destination once every CCV it names has
published a verifier result. ``OffRamp.execute`` then needs the message as
sent and those results; the default executor usually sends it, and anyone
else can when it does not.
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import Any

import requests
from eth_typing import ChecksumAddress
from eth_utils import keccak
from web3 import Web3

from ipor_fusion.core.contract import Call
from ipor_fusion.crosschain.ccip.chainlink import CcipOffRamp
from ipor_fusion.errors import VerifierResultUnavailable
from ipor_fusion.types import MessageId

#: Chainlink Labs' public mainnet indexers, tried in order.
INDEXER_URLS: tuple[str, ...] = (
    "https://indexer-1.ccip.chain.link",
    "https://indexer-2.ccip.chain.link",
)
#: The indexers answer 403 to Python's default User-Agent strings.
_HEADERS = {"Accept": "application/json", "User-Agent": "ipor-fusion-sdk"}


@dataclass(frozen=True, slots=True)
class VerifierResult:
    """One message's committee result as the indexer serves it."""

    message_id: MessageId
    #: The CCVs the message names, in the order ``execute`` expects.
    ccv_addresses: tuple[ChecksumAddress, ...]
    #: The verifier result for the committee CCV: version tag, signature
    #: length, signatures.
    ccv_data: bytes
    #: The executor the sender paid on the source chain.
    executor: ChecksumAddress
    #: The decoded MessageV1 fields as the indexer reports them.
    message: Mapping[str, Any]
    indexer: str


def fetch_verifier_result(
    message_id: MessageId,
    *,
    indexers: Sequence[str] = INDEXER_URLS,
    timeout: float = 15.0,
) -> VerifierResult:
    """``GET /v1/verifierresults/<messageId>`` from the first indexer that has
    a readable result; raises :class:`VerifierResultUnavailable` otherwise. An
    indexer whose answer cannot be read (not JSON, a missing or mistyped field,
    invalid hex or address, a result for another message) is skipped like one
    that is unreachable."""
    if len(message_id) != 32:
        raise ValueError("message_id must be 32 bytes")
    hex_id = "0x" + message_id.hex()
    reasons = []
    for base in indexers:
        try:
            response = requests.get(
                f"{base}/v1/verifierresults/{hex_id}", headers=_HEADERS, timeout=timeout
            )
        except requests.RequestException as exc:
            reasons.append(f"{base}: {type(exc).__name__}")
            continue
        if response.status_code != 200:
            reasons.append(f"{base}: HTTP {response.status_code}")
            continue
        try:
            result = _parse_answer(response.json(), message_id, base)
        except (KeyError, IndexError, TypeError, ValueError) as exc:
            reasons.append(f"{base}: malformed answer ({type(exc).__name__}: {exc})")
            continue
        if result is None:
            reasons.append(f"{base}: no result yet")
            continue
        return result
    raise VerifierResultUnavailable(
        f"no verifier result for {hex_id}: " + "; ".join(reasons)
    )


def _hex_bytes(value: Any) -> bytes:
    if not isinstance(value, str) or not value.startswith("0x"):
        raise ValueError(f"not 0x-prefixed hex: {value!r}")
    return bytes.fromhex(value[2:])


def _parse_answer(
    body: Any, message_id: MessageId, indexer: str
) -> VerifierResult | None:
    """The first result of an indexer's JSON answer; ``None`` when it has none.
    Anything else it cannot read raises a builtin error, reported by the caller
    as a malformed answer."""
    if not isinstance(body, Mapping):
        raise TypeError("answer is not a JSON object")
    results = body.get("results")
    if not results:
        return None
    result = results[0]["verifierResult"]
    served_id = _hex_bytes(result["message_id"])
    if served_id != message_id:
        raise ValueError(f"result is for message 0x{served_id.hex()}")
    message = result["message"]
    if not isinstance(message, Mapping):
        raise TypeError("message is not an object")
    ccvs = result["message_ccv_addresses"]
    if not isinstance(ccvs, list) or not ccvs:
        raise ValueError("message_ccv_addresses is not a non-empty array")
    return VerifierResult(
        message_id=MessageId(served_id),
        ccv_addresses=tuple(Web3.to_checksum_address(a) for a in ccvs),
        ccv_data=_hex_bytes(result["ccv_data"]),
        executor=Web3.to_checksum_address(result["message_executor_address"]),
        message=message,
        indexer=indexer,
    )


def manual_execution(
    off_ramp: CcipOffRamp,
    encoded_message: bytes,
    result: VerifierResult,
    *,
    gas_limit_override: int = 0,
) -> Call[None]:
    """The ``OffRamp.execute`` call that delivers ``encoded_message`` (the
    ``CcipMessageSent.encoded_message`` of the source receipt) with the
    committee's result. Send it from an account able to supply the message's
    gas limit plus the OffRamp's buffer, on HyperEVM one on big blocks."""
    if keccak(encoded_message) != result.message_id:
        raise ValueError("encoded_message does not hash to the result's message id")
    return off_ramp.execute(
        encoded_message,
        result.ccv_addresses,
        [result.ccv_data],
        gas_limit_override,
    )

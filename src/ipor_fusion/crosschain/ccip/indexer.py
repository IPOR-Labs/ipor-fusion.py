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

#: Chainlink Labs' public mainnet indexers, tried in order.
INDEXER_URLS: tuple[str, ...] = (
    "https://indexer-1.ccip.chain.link",
    "https://indexer-2.ccip.chain.link",
)
#: The indexers answer 403 to Python's default User-Agent strings.
_HEADERS = {"Accept": "application/json", "User-Agent": "ipor-fusion-sdk"}


class VerifierResultUnavailable(LookupError):
    """No indexer could serve a verifier result for the message (not verified
    yet, or the indexers were unreachable); the message says which."""


@dataclass(frozen=True, slots=True)
class VerifierResult:
    """One message's committee result as the indexer serves it."""

    message_id: bytes
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
    message_id: bytes,
    *,
    indexers: Sequence[str] = INDEXER_URLS,
    timeout: float = 15.0,
) -> VerifierResult:
    """``GET /v1/verifierresults/<messageId>`` from the first indexer that has
    a result; raises :class:`VerifierResultUnavailable` otherwise."""
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
        results = (response.json() or {}).get("results") or []
        if not results:
            reasons.append(f"{base}: no result yet")
            continue
        result = results[0]["verifierResult"]
        return VerifierResult(
            message_id=bytes.fromhex(result["message_id"][2:]),
            ccv_addresses=tuple(
                Web3.to_checksum_address(a) for a in result["message_ccv_addresses"]
            ),
            ccv_data=bytes.fromhex(result["ccv_data"][2:]),
            executor=Web3.to_checksum_address(result["message_executor_address"]),
            message=result["message"],
            indexer=base,
        )
    raise VerifierResultUnavailable(
        f"no verifier result for {hex_id}: " + "; ".join(reasons)
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

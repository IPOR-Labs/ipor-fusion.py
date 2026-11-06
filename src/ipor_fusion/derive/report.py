"""Derive market (60): EIP-712 NAV report helper for the keeper service.

Preview (`ipor_fusion.PREVIEW_FEATURES`): the type string and the domain are
copied from ``DeriveAccount.sol`` (the private ``_REPORT_TYPE``, whose hash is
the public ``REPORT_TYPEHASH``, and the domain the contract builds by hand:
name ``"DeriveAccount"``, version ``"1"``, exposed as ``DOMAIN_SEPARATOR()``;
the contract does not inherit OpenZeppelin ``EIP712``). The helper builds the
report from per-subaccount values with exact rational arithmetic, computes the
digest the contract verifies, signs it, applies the deadline rule of the
account and encodes ``submitNavReport`` calldata without needing a web3
provider or any network access (the module itself still imports ``web3``).

``subaccountIds`` is hashed by the contract as ``keccak256(abi.encodePacked(
uint64[]))``, which is one 32-byte word per element (Solidity pads array
elements), i.e. the standard EIP-712 array hash that ``encode_typed_data``
produces. ``eth_abi.packed.encode_packed`` would give 8 bytes per element and
is not equivalent.

Valuation coherence (every subaccount valued at the same instant) is the
keeper's responsibility; the helper does not check it.
"""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from decimal import Decimal
from fractions import Fraction

from eth_abi import encode
from eth_account import Account
from eth_account.messages import SignableMessage, encode_typed_data
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion.core.contract import Call, ContractWrapper
from ipor_fusion.fuses.derive import DERIVE_MAX_SUBACCOUNTS

#: The string behind ``DeriveAccount.REPORT_TYPEHASH`` (private ``_REPORT_TYPE``).
DERIVE_REPORT_TYPE = (
    "Report(address vault,uint64[] subaccountIds,int256 mtmUsdE18,"
    "bytes32 stateRoot,uint64 lastOpTimestamp,uint64 deadline)"
)
DERIVE_REPORT_DOMAIN_NAME = "DeriveAccount"
DERIVE_REPORT_DOMAIN_VERSION = "1"

_SUBMIT_SIGNATURE = (
    "submitNavReport((address,uint64[],int256,bytes32,uint64,uint64),bytes,bytes)"
)
_E18 = 10**18
_UINT64_MAX = (1 << 64) - 1
_INT256_MAX = (1 << 255) - 1


def _check_uint64(value: int, name: str) -> None:
    if isinstance(value, bool) or not 0 <= value <= _UINT64_MAX:
        raise ValueError(f"{name} must fit uint64, got {value}")


@dataclass(frozen=True, slots=True)
class DeriveNavReport:
    """``IDeriveAccount.DeriveNavReport``. ``subaccount_ids`` must be strictly
    ascending (at most 32), ``mtm_usd_e18`` non-negative."""

    vault: ChecksumAddress
    subaccount_ids: tuple[int, ...]
    mtm_usd_e18: int
    state_root: bytes
    last_op_timestamp: int
    deadline: int

    def __post_init__(self) -> None:
        if len(self.subaccount_ids) > DERIVE_MAX_SUBACCOUNTS:
            raise ValueError(
                f"at most {DERIVE_MAX_SUBACCOUNTS} subaccount ids, "
                f"got {len(self.subaccount_ids)}"
            )
        for sid in self.subaccount_ids:
            _check_uint64(sid, "subaccount id")
        if any(
            a >= b
            for a, b in zip(self.subaccount_ids, self.subaccount_ids[1:], strict=False)
        ):
            raise ValueError("subaccount ids must be strictly ascending")
        if isinstance(self.mtm_usd_e18, bool) or not isinstance(self.mtm_usd_e18, int):
            raise ValueError("mtm_usd_e18 must be an int, not a bool or other type")
        if not 0 <= self.mtm_usd_e18 <= _INT256_MAX:
            raise ValueError("mtm_usd_e18 must be non-negative and fit int256")
        if not isinstance(self.state_root, (bytes, bytearray)):
            raise ValueError("state_root must be bytes or bytearray")
        if len(self.state_root) != 32:
            raise ValueError("state_root must be 32 bytes")
        _check_uint64(self.last_op_timestamp, "last_op_timestamp")
        _check_uint64(self.deadline, "deadline")

    def as_tuple(self) -> tuple[str, list[int], int, bytes, int, int]:
        return (
            self.vault,
            list(self.subaccount_ids),
            self.mtm_usd_e18,
            self.state_root,
            self.last_op_timestamp,
            self.deadline,
        )


def derive_report_typed_data(
    report: DeriveNavReport, chain_id: int, account: ChecksumAddress
) -> dict[str, object]:
    """The EIP-712 typed data of ``report`` for the account on ``chain_id``."""
    return {
        "types": {
            "EIP712Domain": [
                {"name": "name", "type": "string"},
                {"name": "version", "type": "string"},
                {"name": "chainId", "type": "uint256"},
                {"name": "verifyingContract", "type": "address"},
            ],
            "Report": [
                {"name": "vault", "type": "address"},
                {"name": "subaccountIds", "type": "uint64[]"},
                {"name": "mtmUsdE18", "type": "int256"},
                {"name": "stateRoot", "type": "bytes32"},
                {"name": "lastOpTimestamp", "type": "uint64"},
                {"name": "deadline", "type": "uint64"},
            ],
        },
        "primaryType": "Report",
        "domain": {
            "name": DERIVE_REPORT_DOMAIN_NAME,
            "version": DERIVE_REPORT_DOMAIN_VERSION,
            "chainId": chain_id,
            "verifyingContract": Web3.to_checksum_address(account),
        },
        "message": {
            "vault": report.vault,
            "subaccountIds": list(report.subaccount_ids),
            "mtmUsdE18": report.mtm_usd_e18,
            "stateRoot": report.state_root,
            "lastOpTimestamp": report.last_op_timestamp,
            "deadline": report.deadline,
        },
    }


def _signable(
    report: DeriveNavReport, chain_id: int, account: ChecksumAddress
) -> SignableMessage:
    return encode_typed_data(
        full_message=derive_report_typed_data(report, chain_id, account)
    )


def derive_report_digest(
    report: DeriveNavReport, chain_id: int, account: ChecksumAddress
) -> bytes:
    """The digest ``DeriveAccount._reportDigest`` recovers keepers from."""
    message = _signable(report, chain_id, account)
    return bytes(Web3.keccak(b"\x19" + message.version + message.header + message.body))


def sign_derive_report(
    report: DeriveNavReport,
    chain_id: int,
    account: ChecksumAddress,
    private_key: str | bytes,
) -> bytes:
    """65-byte ``r || s || v`` keeper signature (low ``s``, ``v`` 27 or 28)."""
    signed = Account.sign_message(_signable(report, chain_id, account), private_key)
    return bytes(signed.signature)


def _to_fraction(value: object, name: str) -> Fraction:
    if isinstance(value, bool) or isinstance(value, float):
        raise TypeError(f"{name} must be int, str, Decimal or Fraction, not float/bool")
    if isinstance(value, Decimal):
        if not value.is_finite():
            raise ValueError(f"{name} must be finite, got {value}")
        return Fraction(value)
    if isinstance(value, (int, str, Fraction)):
        try:
            return Fraction(value)
        except (ValueError, ZeroDivisionError) as error:
            raise ValueError(f"{name} is not a finite number: {value!r}") from error
    raise TypeError(f"{name} must be int, str, Decimal or Fraction")


def mtm_usd_e18_from_subaccount_values(
    values: Mapping[int, int | str | Decimal | Fraction],
) -> int:
    """Sum of the subaccount values in USD, each floored at zero, scaled by
    1e18 and truncated down. Exact: independent of the caller's ``decimal``
    context; ``float`` raises ``TypeError``, NaN and infinities ``ValueError``."""
    total = Fraction(0)
    for sid, raw in values.items():
        value = _to_fraction(raw, f"value of subaccount {sid}")
        if value > 0:
            total += value
    return int(total * _E18 // 1)


def derive_report_from_subaccount_values(
    *,
    vault: ChecksumAddress,
    values: Mapping[int, int | str | Decimal | Fraction],
    state_root: bytes,
    last_op_timestamp: int,
    deadline: int,
) -> DeriveNavReport:
    """Build a report whose id set is exactly the keys of ``values`` (every id
    is kept, an underwater or empty one contributes zero), sorted ascending."""
    return DeriveNavReport(
        vault=vault,
        subaccount_ids=tuple(sorted(values)),
        mtm_usd_e18=mtm_usd_e18_from_subaccount_values(values),
        state_root=state_root,
        last_op_timestamp=last_op_timestamp,
        deadline=deadline,
    )


def derive_report_deadline(
    now: int, window_sec: int, last_accepted_deadline: int
) -> int:
    """``max(now + window, lastAcceptedDeadline + 1)``: deadlines strictly
    increase across accepted reports. Keeping the window within the account's
    ``maxDeadlineSec`` is the caller's responsibility."""
    deadline = max(now + window_sec, last_accepted_deadline + 1)
    _check_uint64(deadline, "deadline")
    return deadline


def encode_submit_nav_report(
    report: DeriveNavReport, signature1: bytes, signature2: bytes
) -> bytes:
    """Calldata of ``DeriveAccount.submitNavReport(report, sig1, sig2)``."""
    selector = function_signature_to_4byte_selector(_SUBMIT_SIGNATURE)
    return selector + encode(
        ["(address,uint64[],int256,bytes32,uint64,uint64)", "bytes", "bytes"],
        [report.as_tuple(), signature1, signature2],
    )


class DeriveAccount(ContractWrapper):
    """The Derive account of a vault (``DeriveAccount.sol``)."""

    def last_accepted_deadline(self) -> Call[int]:
        """Deadline of the last accepted report (0 before the first)."""
        return self._view("lastAcceptedDeadline()", output_types=["uint64"])

    def submit_nav_report(
        self, report: DeriveNavReport, signature1: bytes, signature2: bytes
    ) -> Call[None]:
        """Unsigned ``submitNavReport`` call; any funded account may submit."""
        return self._write(_SUBMIT_SIGNATURE, report.as_tuple(), signature1, signature2)

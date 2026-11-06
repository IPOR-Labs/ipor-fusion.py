"""Derive market (60): typed bytes32 substrates of a PlasmaVault that settles
against a Derive account.

Mirrors ``DeriveSubstrateLib.sol``: ``bytes32(uint256(type) << 248 | data)``
with every unused bit zero (the contracts reject a word with a stray bit).
:func:`ipor_fusion.decode_substrate` inverts :class:`DeriveSubstrates` for
market 60. The fuse call encoders are not part of this module yet.
"""

from __future__ import annotations

from dataclasses import dataclass

from eth_typing import ChecksumAddress

from ipor_fusion.fuses.base import (
    _substrate_address_bytes,
    _validate_not_zero_address,
)

#: ``DeriveSubstrateLib.MAX_SUBACCOUNTS``: SUBACCOUNT words one market may hold.
DERIVE_MAX_SUBACCOUNTS = 32


def _validate_uint(value: int, bits: int, name: str) -> None:
    if not 0 <= value < (1 << bits):
        raise ValueError(f"{name} must fit uint{bits}, got {value}")


def _word(substrate_type: int, data: int) -> bytes:
    return ((substrate_type << 248) | data).to_bytes(32, "big")


def _address_word(address: ChecksumAddress, name: str) -> int:
    _validate_not_zero_address(address, name)
    return int.from_bytes(_substrate_address_bytes(address), "big")


@dataclass(frozen=True)
class DeriveLimits:
    """The LIMITS word (``DeriveSubstrateLib.Limits``); a market holds exactly
    one. Seconds are ``uint32``, USD amounts carry 6 decimals (``uint64``) and
    ``nav_jump_bps`` is ``uint16`` (0 disables the band)."""

    max_report_age_sec: int
    max_deadline_sec: int
    max_session_key_ttl_sec: int
    max_new_subaccount_deposit_usd_e6: int
    max_withdraw_fee_usd_e6: int
    nav_jump_bps: int


class DeriveSubstrates:
    """Typed bytes32 substrate encoders for the Derive market (60).

    The Atomist grants an ASSET label per asset the account may hold, the
    MANAGER ids and SUBACCOUNT ids (at most :data:`DERIVE_MAX_SUBACCOUNTS`) the
    vault may use, SESSION_KEY and KEEPER addresses, SCOPE ids and exactly one
    LIMITS word. A scope id is an ``int`` below ``2**248``.
    """

    @staticmethod
    def asset(label: ChecksumAddress) -> bytes:
        """Protocol asset label (an address, not necessarily the token)."""
        return _word(1, _address_word(label, "label"))

    @staticmethod
    def manager(manager_id: int) -> bytes:
        _validate_uint(manager_id, 32, "manager_id")
        return _word(2, manager_id)

    @staticmethod
    def subaccount(subaccount_id: int) -> bytes:
        _validate_uint(subaccount_id, 64, "subaccount_id")
        return _word(3, subaccount_id)

    @staticmethod
    def session_key(key: ChecksumAddress) -> bytes:
        return _word(4, _address_word(key, "key"))

    @staticmethod
    def scope(scope_id: int) -> bytes:
        """Scope id as an integer; ids of 248 bits or more are rejected."""
        _validate_uint(scope_id, 248, "scope_id")
        return _word(5, scope_id)

    @staticmethod
    def keeper(keeper: ChecksumAddress) -> bytes:
        return _word(6, _address_word(keeper, "keeper"))

    @staticmethod
    def limits(limits: DeriveLimits) -> bytes:
        """The single LIMITS word; every field is range-checked."""
        _validate_uint(limits.max_report_age_sec, 32, "max_report_age_sec")
        _validate_uint(limits.max_deadline_sec, 32, "max_deadline_sec")
        _validate_uint(limits.max_session_key_ttl_sec, 32, "max_session_key_ttl_sec")
        _validate_uint(
            limits.max_new_subaccount_deposit_usd_e6,
            64,
            "max_new_subaccount_deposit_usd_e6",
        )
        _validate_uint(limits.max_withdraw_fee_usd_e6, 64, "max_withdraw_fee_usd_e6")
        _validate_uint(limits.nav_jump_bps, 16, "nav_jump_bps")
        return _word(
            7,
            (limits.max_report_age_sec << 208)
            | (limits.max_deadline_sec << 176)
            | (limits.max_session_key_ttl_sec << 144)
            | (limits.max_new_subaccount_deposit_usd_e6 << 80)
            | (limits.max_withdraw_fee_usd_e6 << 16)
            | limits.nav_jump_bps,
        )

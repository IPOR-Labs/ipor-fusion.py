"""Builders for validating a hand-made vault state offline."""

from __future__ import annotations

from unittest.mock import MagicMock

from ipor_fusion.validation import (
    Erc20Holding,
    Erc20Holdings,
    VaultState,
    VaultValidation,
    validate_vault_state,
)


def validate(
    state: VaultState,
    balance_fuses_total: int = 0,
    erc20: Erc20Holdings | None = None,
    plasma_vault: MagicMock | None = None,
) -> VaultValidation:
    """Validate ``state`` with every balance fuse summed into one market."""
    return validate_vault_state(
        MagicMock(),
        plasma_vault or MagicMock(),
        state,
        {0: balance_fuses_total},
        erc20 or Erc20Holdings(),
    )


def holding(
    address: str,
    symbol: str = "TKN",
    decimals: int = 18,
    balance: int = 0,
    price_usd: float | None = None,
) -> Erc20Holding:
    usd_value = balance / 10**decimals * price_usd if price_usd else None
    return Erc20Holding(address, symbol, decimals, balance, price_usd, usd_value, "")

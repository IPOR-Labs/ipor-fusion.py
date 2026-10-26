"""`FusionInstance.from_dict` parses a stored instance back into its typed
fields instead of passing raw JSON strings into role-typed addresses."""

from __future__ import annotations

from dataclasses import asdict

import pytest
from _addresses import miscased
from web3 import Web3

from ipor_fusion.core.fusion_factory import FusionInstance


def _stored() -> dict:
    address = {
        name: "0x" + f"{i:02x}" * 20
        for i, name in enumerate(
            [
                "underlying_token",
                "initial_owner",
                "plasma_vault",
                "plasma_vault_base",
                "access_manager",
                "fee_manager",
                "rewards_manager",
                "withdraw_manager",
                "context_manager",
                "price_manager",
            ],
            start=0xA1,
        )
    }
    return {
        "index": 3,
        "version": 2,
        "asset_name": "IPOR USDC",
        "asset_symbol": "ipUSDC",
        "asset_decimals": 8,
        "underlying_token_symbol": "USDC",
        "underlying_token_decimals": 6,
        **address,
    }


def test_from_dict_checksums_every_address_and_round_trips():
    stored = _stored()
    instance = FusionInstance.from_dict(stored)
    assert instance.plasma_vault == Web3.to_checksum_address(stored["plasma_vault"])
    assert instance.access_manager == Web3.to_checksum_address(stored["access_manager"])
    assert instance.price_manager == Web3.to_checksum_address(stored["price_manager"])
    assert FusionInstance.from_dict(asdict(instance)) == instance


@pytest.mark.parametrize(
    ("change", "match"),
    [
        (lambda d: d.pop("plasma_vault"), "missing"),
        (lambda d: d.update(extra=1), "extra"),
        (lambda d: d.update(plasma_vault="0x1234"), "address"),
        (lambda d: d.update(plasma_vault=" " + d["plasma_vault"]), "address"),
        (lambda d: d.update(plasma_vault=miscased(d["plasma_vault"])), "checksum"),
        (lambda d: d.update(price_manager=miscased(d["price_manager"])), "checksum"),
        (lambda d: d.update(asset_decimals="8"), "integer"),
        (lambda d: d.update(index=True), "integer"),
        (lambda d: d.update(asset_symbol=None), "string"),
    ],
)
def test_from_dict_rejects_a_malformed_field(change, match):
    stored = _stored()
    change(stored)
    with pytest.raises(ValueError, match=match):
        FusionInstance.from_dict(stored)

"""Unit tests for `ContractWrapper.encoder()` — ctx-less factory used by
external-signer flows (HTTP signing services, multisig review UIs) that
only consume `Call.calldata`.

The encoder hatch lets callers build wrappers without a Web3Context while
still using the same builder methods that drive `.call()` / `.send()` in
in-process flows. Previously downstream projects poked `__new__` + private
`_ctx`/`_address` slots; `encoder()` replaces that with a public API.
"""

from __future__ import annotations

import pytest
from web3 import Web3

from ipor_fusion.core import AccessManager, FusionFactory, PlasmaVault

SAMPLE_ADDRESS = Web3.to_checksum_address("0x1455717668fA96534f675856347A973fA907e922")
SAMPLE_USER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")


@pytest.mark.parametrize("cls", [FusionFactory, PlasmaVault, AccessManager])
def test_encoder_with_address_sets_target(cls):
    """Passing an address fills the placeholder; checksum normalization
    happens on the way in."""
    wrapper = cls.encoder(str(SAMPLE_ADDRESS).lower())
    assert wrapper.address == SAMPLE_ADDRESS


@pytest.mark.parametrize("cls", [FusionFactory, PlasmaVault, AccessManager])
def test_encoder_without_address_defaults_to_zero(cls):
    """Calling `encoder()` with no args yields a zero-address placeholder;
    fine because `Call.calldata` is target-independent."""
    wrapper = cls.encoder()
    assert int(wrapper.address, 16) == 0


def test_encoder_plasma_vault_builds_setter_calldata():
    """Builder methods on the encoder wrapper produce valid calldata that
    matches what an in-process ctx wrapper would produce."""
    encoder = PlasmaVault.encoder()
    add_fuses_data = encoder.add_fuses([SAMPLE_ADDRESS]).calldata
    assert add_fuses_data[:4].hex() == Web3.keccak(text="addFuses(address[])")[:4].hex()


def test_encoder_access_manager_builds_grant_role_calldata():
    """`AccessManager.encoder().grant_role(...).calldata` round-trip — proves
    the encoder is usable for both setters (PlasmaVault) and access control."""
    encoder = AccessManager.encoder()
    data = encoder.grant_role(1, SAMPLE_USER, 0).calldata
    assert (
        data[:4].hex() == Web3.keccak(text="grantRole(uint64,address,uint32)")[:4].hex()
    )


def test_encoder_call_without_ctx_raises():
    """`.call()` on an encoder-built Call has no ctx — must refuse rather
    than silently dispatch against `None`."""
    call = AccessManager.encoder(SAMPLE_ADDRESS).has_role(1, SAMPLE_USER)
    with pytest.raises(ValueError, match="Web3Context required"):
        call.call()


def test_encoder_send_without_ctx_raises():
    """`.send()` on an encoder-built Call also requires a ctx."""
    call = PlasmaVault.encoder(SAMPLE_ADDRESS).convert_to_public_vault()
    with pytest.raises(ValueError, match="Web3Context required"):
        call.send()

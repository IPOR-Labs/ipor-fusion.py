"""Offline wrappers: a `ContractWrapper` built with ``ctx=None`` serves
external-signer flows (HTTP signing services, multisig review UIs) that only
consume `Call.calldata`. Construction runs the normal constructor, so every
subclass is fully initialized; only executing a `Call` needs a context.
"""

from __future__ import annotations

import pytest
from eth_abi import decode
from web3 import Web3

from ipor_fusion.core import AccessManager, FusionFactory, Multicall3, PlasmaVault
from ipor_fusion.core.oracle import LegacyPriceOracleMiddleware
from ipor_fusion.core.withdraw_manager import WithdrawManager
from ipor_fusion.types import Decimals

SAMPLE_ADDRESS = Web3.to_checksum_address("0x1455717668fA96534f675856347A973fA907e922")
SAMPLE_USER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")


@pytest.mark.parametrize("cls", [FusionFactory, PlasmaVault, AccessManager])
def test_ctx_less_wrapper_checksums_its_address(cls):
    wrapper = cls(None, str(SAMPLE_ADDRESS).lower())
    assert wrapper.address == SAMPLE_ADDRESS


@pytest.mark.parametrize("cls", [FusionFactory, PlasmaVault, AccessManager])
def test_ctx_less_wrapper_requires_an_address(cls):
    with pytest.raises(TypeError):
        cls(None)


def test_there_is_no_encoder_factory():
    assert not hasattr(PlasmaVault, "encoder")


def test_ctx_less_plasma_vault_builds_setter_calldata():
    call = PlasmaVault(None, SAMPLE_ADDRESS).add_fuses([SAMPLE_ADDRESS])
    assert call.calldata[:4] == Web3.keccak(text="addFuses(address[])")[:4]
    assert call.to == SAMPLE_ADDRESS


def test_ctx_less_plasma_vault_pre_hook_governance():
    vault = PlasmaVault(None, SAMPLE_ADDRESS)
    selector = Web3.keccak(text="deposit(uint256,address)")[:4]
    call = vault.set_pre_hook_implementations(
        [selector], [SAMPLE_ADDRESS], [[b"\x01" * 32]]
    )
    assert (
        call.data[:4]
        == Web3.keccak(
            text="setPreHookImplementations(bytes4[],address[],bytes32[][])"
        )[:4]
    )
    assert decode(["bytes4[]", "address[]", "bytes32[][]"], call.data[4:]) == (
        (selector,),
        (SAMPLE_ADDRESS.lower(),),
        ((b"\x01" * 32,),),
    )
    assert (
        vault.get_pre_hook_selectors().data[:4]
        == Web3.keccak(text="getPreHookSelectors()")[:4]
    )
    assert vault.get_pre_hook_implementation(selector).data == (
        Web3.keccak(text="getPreHookImplementation(bytes4)")[:4]
        + selector
        + b"\x00" * 28
    )
    with pytest.raises(ValueError, match="equal length"):
        vault.set_pre_hook_implementations([selector], [], [[]])
    with pytest.raises(ValueError, match="invalid pre-hook selector"):
        vault.set_pre_hook_implementations([b"\x00" * 4], [SAMPLE_ADDRESS], [[]])
    with pytest.raises(ValueError, match="invalid pre-hook selector"):
        vault.get_pre_hook_implementation(b"\x01")


def test_ctx_less_access_manager_builds_grant_role_calldata():
    data = AccessManager(None, SAMPLE_ADDRESS).grant_role(1, SAMPLE_USER, 0).calldata
    assert data[:4] == Web3.keccak(text="grantRole(uint64,address,uint32)")[:4]


def test_ctx_less_legacy_oracle_keeps_its_decimals():
    """The legacy middleware's decoder needs the constructor's
    `base_currency_decimals`; an offline wrapper carries it too."""
    oracle = LegacyPriceOracleMiddleware(None, SAMPLE_ADDRESS, Decimals(8))
    price = oracle.get_asset_price(SAMPLE_USER).decode((123).to_bytes(32, "big"))
    assert price.amount == 123
    assert price.decimals == 8


def test_ctx_less_multicall_validates_its_batch_size():
    with pytest.raises(ValueError, match="batch_size"):
        Multicall3(None, batch_size=0)


def test_ctx_less_call_raises():
    call = AccessManager(None, SAMPLE_ADDRESS).has_role(1, SAMPLE_USER)
    with pytest.raises(ValueError, match="Web3Context required"):
        call.call()


@pytest.mark.parametrize("method", ["send", "build_transaction", "estimate_gas"])
def test_ctx_less_send_paths_raise(method):
    call = PlasmaVault(None, SAMPLE_ADDRESS).convert_to_public_vault()
    with pytest.raises(ValueError, match="Web3Context required"):
        getattr(call, method)()


def test_ctx_less_compound_reads_name_the_wrapper():
    """Methods that read the chain themselves raise a `ValueError` naming the
    wrapper, instead of an `AttributeError` on ``None``."""
    with pytest.raises(ValueError, match="Web3Context required: PlasmaVault"):
        PlasmaVault(None, SAMPLE_ADDRESS).withdraw_manager_address()
    with pytest.raises(ValueError, match="Web3Context required: Multicall3"):
        Multicall3(None).aggregate([])
    with pytest.raises(ValueError, match="Web3Context required: WithdrawManager"):
        WithdrawManager(None, SAMPLE_ADDRESS).get_pending_requests()

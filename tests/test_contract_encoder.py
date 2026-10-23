"""Context-free wrappers: a `ContractWrapper` is its address (plus genuine
contract parameters) and builds `Call`s offline, e.g. for external-signer flows
(HTTP signing services, multisig review UIs) that only consume
`Call.calldata`. Only executing a `Call` takes a `Web3Context`, and it is a
required argument there.
"""

from __future__ import annotations

import dataclasses
import inspect
from unittest.mock import MagicMock

import pytest
from eth_abi import decode, encode
from web3 import Web3

from ipor_fusion.core import AccessManager, FusionFactory, Multicall3, PlasmaVault
from ipor_fusion.core.contract import Call
from ipor_fusion.core.erc20 import ERC20
from ipor_fusion.core.oracle import LegacyPriceOracleMiddleware
from ipor_fusion.types import Decimals

SAMPLE_ADDRESS = Web3.to_checksum_address("0x1455717668fA96534f675856347A973fA907e922")
SAMPLE_USER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")


@pytest.mark.parametrize("cls", [FusionFactory, PlasmaVault, AccessManager, ERC20])
def test_wrapper_checksums_its_address(cls):
    wrapper = cls(str(SAMPLE_ADDRESS).lower())
    assert wrapper.address == SAMPLE_ADDRESS
    assert not hasattr(wrapper, "_ctx")


@pytest.mark.parametrize("cls", [FusionFactory, PlasmaVault, AccessManager, ERC20])
def test_wrapper_requires_an_address(cls):
    with pytest.raises(TypeError):
        cls()


@pytest.mark.parametrize("cls", [FusionFactory, PlasmaVault, AccessManager, ERC20])
def test_wrapper_rejects_the_3x_context_argument(cls):
    with pytest.raises(TypeError):
        cls(MagicMock(), SAMPLE_ADDRESS)


def test_there_is_no_encoder_factory():
    assert not hasattr(PlasmaVault, "encoder")


def test_plasma_vault_builds_setter_calldata():
    call = PlasmaVault(SAMPLE_ADDRESS).add_fuses([SAMPLE_ADDRESS])
    assert call.calldata[:4] == Web3.keccak(text="addFuses(address[])")[:4]
    assert call.to == SAMPLE_ADDRESS


def test_plasma_vault_pre_hook_governance():
    vault = PlasmaVault(SAMPLE_ADDRESS)
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


def test_access_manager_builds_grant_role_calldata():
    data = AccessManager(SAMPLE_ADDRESS).grant_role(1, SAMPLE_USER, 0).calldata
    assert data[:4] == Web3.keccak(text="grantRole(uint64,address,uint32)")[:4]


def test_legacy_oracle_keeps_its_decimals():
    """The legacy middleware's decoder needs the constructor's
    `base_currency_decimals`; the wrapper carries it, no context involved."""
    oracle = LegacyPriceOracleMiddleware(SAMPLE_ADDRESS, Decimals(8))
    price = oracle.get_asset_price(SAMPLE_USER).decode((123).to_bytes(32, "big"))
    assert price.amount == 123
    assert price.decimals == 8


def test_multicall_validates_its_batch_size():
    with pytest.raises(ValueError, match="batch_size"):
        Multicall3(MagicMock(), batch_size=0)


def test_call_carries_no_context():
    assert [field.name for field in dataclasses.fields(Call)] == [
        "to",
        "data",
        "output_types",
        "decoder",
        "decode_error",
    ]


@pytest.mark.parametrize(
    "method", ["call", "send", "build_transaction", "estimate_gas"]
)
def test_executing_a_call_requires_a_context(method):
    parameter = inspect.signature(getattr(Call, method)).parameters["ctx"]
    assert parameter.default is inspect.Parameter.empty
    assert parameter.kind is inspect.Parameter.POSITIONAL_OR_KEYWORD
    call = PlasmaVault(SAMPLE_ADDRESS).convert_to_public_vault()
    with pytest.raises(TypeError):
        getattr(call, method)()


def test_the_context_executes_and_the_defaults_differ_by_purpose():
    """A read follows the context's (pinned) block; transaction preparation
    defaults to the head."""
    ctx = MagicMock()
    ctx.call.return_value = encode(["uint8"], [6])
    erc20 = ERC20(SAMPLE_ADDRESS)

    assert erc20.decimals().call(ctx) == 6
    assert erc20.decimals().call(ctx=ctx, block=7) == 6
    assert ctx.call.call_args_list[0].args == (
        SAMPLE_ADDRESS,
        erc20.decimals().data,
    )
    assert ctx.call.call_args_list[1].args == (
        SAMPLE_ADDRESS,
        erc20.decimals().data,
        7,
    )

    write = erc20.approve(SAMPLE_USER, 1)
    write.build_transaction(ctx)
    write.estimate_gas(ctx)
    write.send(ctx)
    ctx.build_transaction.assert_called_once_with(
        SAMPLE_ADDRESS, write.data, block="latest"
    )
    ctx.estimate_gas.assert_called_once_with(SAMPLE_ADDRESS, write.data, block="latest")
    ctx.send.assert_called_once_with(SAMPLE_ADDRESS, write.data)

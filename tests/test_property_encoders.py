"""Property-based tests of the encoders and decoders that turn untrusted bytes
(calldata, on-chain substrate words) into SDK values and back. Substrate
decoders return lowercase addresses (``HexAddress``), so they are compared
lowercased."""

import math
from fractions import Fraction

import pytest
from eth_abi import encode
from eth_abi.tools import get_abi_strategy
from eth_utils import function_signature_to_4byte_selector
from hypothesis import given, settings
from hypothesis import strategies as st
from web3 import Web3

import ipor_fusion  # noqa: F401  (loads every fuse class)
from ipor_fusion import (
    FuseAction,
    decode_execute_calldata,
    decode_fuse_action,
    decode_substrate,
    fuse_classes_for,
)
from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.core.fuse_decoding import _loaded_fuse_classes
from ipor_fusion.crosschain.fuses.base import (
    CrosschainSubstrate,
    CrosschainSubstrateLib,
    CrosschainSubstrateType,
)
from ipor_fusion.fuses.euler_v2 import euler_substrate
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.readers.morpho import (
    VIRTUAL_ASSETS,
    VIRTUAL_SHARES,
    to_assets_down,
    to_assets_up,
)
from ipor_fusion.substrates import _SUBSTRATE_DECODERS
from ipor_fusion.types import ChainId, FuseAddress, MarketId

BATCH_SIGNATURES = ("execute((address,bytes)[])", "claimRewards((address,bytes)[])")
UINT128_MAX = (1 << 128) - 1
UINT256_MAX = (1 << 256) - 1

addresses = st.binary(min_size=20, max_size=20).map(Web3.to_checksum_address)
nonzero_addresses = addresses.filter(lambda a: int(a, 16) != 0)
fuse_actions = st.builds(
    FuseAction, fuse=addresses.map(FuseAddress), data=st.binary(max_size=200)
)


# ── execute / claimRewards calldata ──────────────────────────────────────────


@given(
    actions=st.lists(fuse_actions, max_size=8),
    signature=st.sampled_from(BATCH_SIGNATURES),
)
def test_batch_calldata_round_trips(actions, signature):
    calldata = FuseAction.encode_execute_payload(actions, signature)
    assert decode_execute_calldata(calldata) == actions


@st.composite
def untrusted_calldata(draw):
    """Raw bytes, often behind a valid batch selector so the ABI decoder runs."""
    tail = draw(st.binary(max_size=300))
    if draw(st.booleans()):
        signature = draw(st.sampled_from(BATCH_SIGNATURES))
        return function_signature_to_4byte_selector(signature) + tail
    return tail


@given(calldata=untrusted_calldata())
def test_untrusted_calldata_decodes_or_raises_value_error(calldata):
    try:
        actions = decode_execute_calldata(calldata)
    except ValueError:
        return
    assert all(isinstance(action, FuseAction) for action in actions)


# ── every fuse signature the SDK declares ────────────────────────────────────

FUSE_SIGNATURES = sorted(
    {
        (cls, signature)
        for cls in _loaded_fuse_classes()
        for signature in cls.signatures()
    },
    key=lambda pair: (pair[0].__module__, pair[0].__qualname__, pair[1]),
)


def abi_args(types: list[str]) -> st.SearchStrategy:
    if not types:
        return st.just(())
    return get_abi_strategy(f"({','.join(types)})")


@pytest.mark.parametrize(
    ("fuse_class", "signature"),
    FUSE_SIGNATURES,
    ids=[f"{cls.__name__}.{sig[: sig.index('(')]}" for cls, sig in FUSE_SIGNATURES],
)
@settings(max_examples=25)
@given(data=st.data(), fuse=addresses.map(FuseAddress))
def test_every_declared_fuse_call_decodes_to_its_own_bytes(
    fuse_class, signature, data, fuse
):
    types = _parse_param_types(signature)
    args = data.draw(abi_args(types))
    calldata = function_signature_to_4byte_selector(signature) + encode(types, args)
    action = FuseAction(fuse=fuse, data=calldata)

    decoded = decode_fuse_action(action, fuse_class)

    assert decoded.signature == signature
    assert decoded.fuse == fuse
    assert encode(types, decoded.args) == calldata[4:]
    assert fuse_class in fuse_classes_for(action)


# ── substrate words ──────────────────────────────────────────────────────────

CROSSCHAIN = IporFusionMarkets.CROSSCHAIN
MAX_CHAIN = CrosschainSubstrateLib.MAX_SUBSTRATE_CHAIN_ID


@st.composite
def crosschain_substrates(draw):
    kind = draw(
        st.sampled_from(
            [CrosschainSubstrateType.EXECUTOR, CrosschainSubstrateType.REMOTE_VAULT]
        )
    )
    chain_id = (
        0
        if kind is CrosschainSubstrateType.EXECUTOR
        else draw(st.integers(min_value=1, max_value=MAX_CHAIN))
    )
    return CrosschainSubstrate(kind, ChainId(chain_id), draw(nonzero_addresses))


@given(substrate=crosschain_substrates())
def test_crosschain_substrate_round_trips_through_lib_and_decoder(substrate):
    raw = CrosschainSubstrateLib.substrate_to_bytes32(substrate)

    assert len(raw) == 32
    assert CrosschainSubstrateLib.bytes32_to_substrate(raw) == substrate
    info = decode_substrate(raw, CROSSCHAIN)
    assert not info.is_error
    assert info.address == substrate.substrate_address.lower()
    assert info.type_label == substrate.substrate_type.name
    if substrate.substrate_type is CrosschainSubstrateType.REMOTE_VAULT:
        assert info.extra == {"chain_id": str(substrate.chain_id)}


@given(type_byte=st.sampled_from([1, 2]), rest=st.binary(min_size=31, max_size=31))
def test_crosschain_decoder_flags_exactly_the_grants_no_fuse_can_match(type_byte, rest):
    raw = bytes([type_byte]) + rest
    chain_id = int.from_bytes(raw[1:12], "big")
    canonical = (chain_id == 0) if type_byte == 1 else (chain_id != 0)

    info = decode_substrate(raw, CROSSCHAIN)

    assert info.is_error is not canonical
    if canonical:
        lib = CrosschainSubstrateLib.bytes32_to_substrate(raw)
        assert info.address == lib.substrate_address.lower()
        assert lib.chain_id == chain_id


@given(
    vault=addresses,
    is_collateral=st.booleans(),
    can_borrow=st.booleans(),
    sub_account=st.integers(min_value=0, max_value=0xFF),
)
def test_euler_substrate_matches_solidity_layout_and_decodes_back(
    vault, is_collateral, can_borrow, sub_account
):
    # EulerFuseLib.substrateToBytes32: vault<<96 | isCollateral<<88 |
    # canBorrow<<80 | subAccounts<<72, i.e. these bytes from the top.
    expected = (
        bytes.fromhex(vault[2:])
        + bytes([is_collateral, can_borrow, sub_account])
        + bytes(9)
    )

    raw = euler_substrate(
        euler_vault=vault,
        is_collateral=is_collateral,
        can_borrow=can_borrow,
        sub_account=sub_account,
    )

    assert raw == expected
    info = decode_substrate(raw, IporFusionMarkets.EULER_V2)
    assert info.address == vault.lower()
    assert info.extra["is_collateral"] == str(is_collateral)
    assert info.extra["can_borrow"] == str(can_borrow)
    assert info.extra["sub_account"] == f"0x{sub_account:02x}"


market_ids = st.one_of(
    st.none(),
    st.sampled_from(sorted(_SUBSTRATE_DECODERS)),
    st.integers(min_value=0, max_value=UINT256_MAX).map(MarketId),
)


@given(raw=st.binary(max_size=40), market_id=market_ids)
def test_decode_substrate_never_raises_on_bytes(raw, market_id):
    info = decode_substrate(raw, market_id)
    assert info.is_error or len(raw) == 32


@given(word=st.binary(min_size=32, max_size=32), market_id=market_ids)
def test_decode_substrate_never_raises_on_any_word(word, market_id):
    decode_substrate(word, market_id)


@given(raw=st.text(max_size=80), market_id=market_ids)
def test_decode_substrate_never_raises_on_text(raw, market_id):
    decode_substrate(raw, market_id)


# ── Morpho Blue share math ───────────────────────────────────────────────────

uint128 = st.integers(min_value=0, max_value=UINT128_MAX)


@given(shares=uint128, total_assets=uint128, total_shares=uint128)
def test_morpho_to_assets_rounds_like_exact_division(
    shares, total_assets, total_shares
):
    exact = Fraction(
        shares * (total_assets + VIRTUAL_ASSETS), total_shares + VIRTUAL_SHARES
    )

    assert to_assets_down(shares, total_assets, total_shares) == math.floor(exact)
    assert to_assets_up(shares, total_assets, total_shares) == math.ceil(exact)

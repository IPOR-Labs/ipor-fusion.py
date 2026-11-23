"""Property-based tests of the batch calldata decoder, the substrate codecs and
the Morpho share math. Substrate decoders return lowercase addresses
(``HexAddress``), so they are compared lowercased."""

import math
from fractions import Fraction

import pytest
from eth_abi import encode
from eth_abi.tools import get_abi_strategy
from eth_utils import function_signature_to_4byte_selector
from hypothesis import example, given, settings
from hypothesis import strategies as st
from web3 import Web3

from ipor_fusion import (
    FuseAction,
    IporFusionMarkets,
    decode_execute_calldata,
    decode_fuse_action,
    decode_substrate,
    euler_substrate,
    fuse_classes_for,
)
from ipor_fusion.core.contract import _parse_param_types
from ipor_fusion.core.fuse_decoding import _loaded_fuse_classes
from ipor_fusion.crosschain import (
    CrosschainSubstrate,
    CrosschainSubstrateLib,
    CrosschainSubstrateType,
)
from ipor_fusion.readers.morpho import (
    VIRTUAL_ASSETS,
    VIRTUAL_SHARES,
    to_assets_down,
    to_assets_up,
)
from ipor_fusion.types import ChainId, FuseAddress, MarketId

BATCH_SIGNATURES = ("execute((address,bytes)[])", "claimRewards((address,bytes)[])")
UINT88_MAX = (1 << 88) - 1  # CrosschainSubstrateLib: chainId in bits 247..160
UINT128_MAX = (1 << 128) - 1
UINT256_MAX = (1 << 256) - 1

addresses = st.binary(min_size=20, max_size=20).map(Web3.to_checksum_address)
nonzero_addresses = addresses.filter(lambda a: int(a, 16) != 0)
fuse_actions = st.builds(
    FuseAction, fuse=addresses.map(FuseAddress), data=st.binary(max_size=200)
)
MARKETS = sorted(
    {
        getattr(IporFusionMarkets, name)
        for name in dir(IporFusionMarkets)
        if not name.startswith("_")
        and isinstance(getattr(IporFusionMarkets, name), int)
    }
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
def corrupted_batches(draw):
    """A valid batch with one ABI word overwritten, a truncation or junk
    appended, so the decoder gets past the selector and the first offset."""
    signature = draw(st.sampled_from(BATCH_SIGNATURES))
    actions = draw(st.lists(fuse_actions, min_size=1, max_size=4))
    calldata = bytearray(FuseAction.encode_execute_payload(actions, signature))
    words = (len(calldata) - 4) // 32
    corruption = draw(st.sampled_from(["word", "truncate", "append"]))
    if corruption == "word":
        start = 4 + 32 * draw(st.integers(min_value=0, max_value=words - 1))
        value = draw(
            st.one_of(
                st.sampled_from([0, 1, 31, 32, 33, 2**32, UINT256_MAX]),
                st.integers(min_value=0, max_value=UINT256_MAX),
            )
        )
        calldata[start : start + 32] = value.to_bytes(32, "big")
    elif corruption == "truncate":
        del calldata[draw(st.integers(min_value=4, max_value=len(calldata) - 1)) :]
    else:
        calldata += draw(st.binary(min_size=1, max_size=64))
    return bytes(calldata)


@given(calldata=st.one_of(corrupted_batches(), st.binary(max_size=100)))
def test_untrusted_calldata_decodes_or_raises_value_error(calldata):
    try:
        actions = decode_execute_calldata(calldata)
    except ValueError:
        return
    assert all(isinstance(action, FuseAction) for action in actions)


# ── decoder dispatch over every declared fuse signature ──────────────────────

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
@settings(max_examples=5)
@given(data=st.data(), fuse=addresses.map(FuseAddress))
def test_decoder_dispatches_every_declared_signature(fuse_class, signature, data, fuse):
    """Dispatch only: the selector picks the declared signature and class, and
    the signature parser yields types that decode its arguments. Whether an
    encoder builds the right arguments is test_fuse_encoding.py's job."""
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
chain_slots = st.one_of(
    st.sampled_from([0, 1, UINT88_MAX]), st.integers(min_value=0, max_value=UINT88_MAX)
)


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
        else draw(st.one_of(st.sampled_from([1, UINT88_MAX]), chain_slots))
    )
    if kind is CrosschainSubstrateType.REMOTE_VAULT and chain_id == 0:
        chain_id = 1
    return CrosschainSubstrate(kind, ChainId(chain_id), draw(nonzero_addresses))


@given(substrate=crosschain_substrates())
def test_crosschain_substrate_matches_solidity_layout_and_decodes_back(substrate):
    # CrosschainSubstrateLib: type<<248 | chainId<<160 | address.
    expected = (
        bytes([substrate.substrate_type])
        + substrate.chain_id.to_bytes(11, "big")
        + bytes.fromhex(substrate.substrate_address[2:])
    )

    raw = CrosschainSubstrateLib.substrate_to_bytes32(substrate)

    assert raw == expected
    assert CrosschainSubstrateLib.bytes32_to_substrate(raw) == substrate
    info = decode_substrate(raw, CROSSCHAIN)
    assert not info.is_error
    assert info.address == substrate.substrate_address.lower()
    assert info.type_label == substrate.substrate_type.name
    if substrate.substrate_type is CrosschainSubstrateType.REMOTE_VAULT:
        assert info.extra == {"chain_id": str(substrate.chain_id)}


@given(type_byte=st.sampled_from([1, 2]), chain_id=chain_slots, address=addresses)
def test_crosschain_decoder_flags_exactly_the_grants_no_fuse_can_match(
    type_byte, chain_id, address
):
    raw = bytes([type_byte]) + chain_id.to_bytes(11, "big") + bytes.fromhex(address[2:])
    canonical = (chain_id == 0) if type_byte == 1 else (chain_id != 0)

    info = decode_substrate(raw, CROSSCHAIN)

    assert info.is_error is not canonical
    if canonical:
        assert info.address == address.lower()
    else:
        assert info.address is None
        assert info.raw_hex == "0x" + raw.hex()
        assert info.extra == {"chain_id": str(chain_id)}


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


@pytest.mark.parametrize("market_id", MARKETS)
@given(word=st.binary(min_size=32, max_size=32))
def test_decode_substrate_never_raises_on_any_word(market_id, word):
    decode_substrate(word, market_id)


@given(
    raw=st.binary(max_size=40),
    market_id=st.one_of(
        st.none(),
        st.sampled_from(MARKETS),
        st.integers(min_value=0, max_value=UINT256_MAX).map(MarketId),
    ),
)
def test_decode_substrate_reads_hex_text_like_bytes(raw, market_id):
    info = decode_substrate(raw, market_id)

    assert info.is_error or len(raw) == 32
    for text in (raw.hex(), "0x" + raw.hex(), "0x" + raw.hex().upper()):
        assert decode_substrate(text, market_id) == info


@given(text=st.text(max_size=80))
def test_decode_substrate_flags_text_that_is_not_a_word(text):
    try:
        is_word = len(bytes.fromhex(text.removeprefix("0x"))) == 32
    except ValueError:
        is_word = False
    if not is_word:
        assert decode_substrate(text, IporFusionMarkets.AAVE_V3).is_error


# ── Morpho Blue share math ───────────────────────────────────────────────────

uint128 = st.integers(min_value=0, max_value=UINT128_MAX)


@given(shares=uint128, total_assets=uint128, total_shares=uint128)
@example(shares=VIRTUAL_SHARES, total_assets=0, total_shares=0)  # exact: 1
@example(shares=3 * VIRTUAL_SHARES, total_assets=1, total_shares=0)  # exact: 6
@example(shares=UINT128_MAX, total_assets=UINT128_MAX, total_shares=0)
@example(shares=UINT128_MAX, total_assets=UINT128_MAX, total_shares=UINT128_MAX)
@example(shares=0, total_assets=UINT128_MAX, total_shares=0)
def test_morpho_to_assets_rounds_like_exact_division(
    shares, total_assets, total_shares
):
    exact = Fraction(
        shares * (total_assets + VIRTUAL_ASSETS), total_shares + VIRTUAL_SHARES
    )

    assert to_assets_down(shares, total_assets, total_shares) == math.floor(exact)
    assert to_assets_up(shares, total_assets, total_shares) == math.ceil(exact)

"""FusionFactory.clone() calldata encoding + live BASE eth_call preview.

Two test paths:

  * `test_clone_calldata_*` — pure encoding, no RPC. Each test pulls
    `factory.clone(...).calldata` from a bare wrapper instance (no ctx) and
    asserts the selector + length layout matches the deployed BASE impl
    signature (0x8697b10a).

  * `test_simulate_clone_*_base` — uses the `web3_base` session fixture
    (set via `BASE_PROVIDER_URL`) to run `factory.clone(...).call(ctx)`,
    decoding the 17-field `FusionInstance` tuple. eth_call is read-only
    so the CREATE2 addresses returned are deterministic for the current
    factory index; running it twice in the same block returns the same
    addresses.

References:
- Real impl bytecode selector grep (2026-05-12) confirms 6-arg `clone(...)`
  on the deployed proxy 0x1455717668fA96534f675856347A973fA907e922.
- Older registry shape (5-arg) is stale — ignore.
"""

from __future__ import annotations

import logging

import pytest
from eth_abi import encode as abi_encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import Web3Context
from ipor_fusion.core import CloneArgs, FusionFactory
from ipor_fusion.core.fusion_factory import _FUSION_INSTANCE_TUPLE_TYPE

LOG = logging.getLogger(__name__)

BASE_FUSION_FACTORY = Web3.to_checksum_address(
    "0x1455717668fA96534f675856347A973fA907e922"
)
BASE_USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
SAMPLE_OWNER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")


def _bare_factory() -> FusionFactory:
    """Build a FusionFactory without a Web3Context. Delegates to the
    public `encoder()` classmethod — `.calldata` only reads `Call.data`
    so the placeholder ctx is fine."""
    return FusionFactory.encoder(BASE_FUSION_FACTORY)


# --- pure encoding ----------------------------------------------------------


def test_clone_calldata_selector_matches_deployed():
    """Selector 0x8697b10a — verified against impl bytecode."""
    calldata = (
        _bare_factory()
        .clone(
            asset_name="IPOR USDC Vault",
            asset_symbol="ipUSDC",
            underlying_token=BASE_USDC,
            redemption_delay_seconds=86400,
            owner=SAMPLE_OWNER,
            dao_fee_package_index=0,
        )
        .calldata
    )
    assert calldata[:4].hex() == "8697b10a"


def test_clone_calldata_length_signals_six_args():
    """6-arg signature → selector (4) + 2 dynamic string offsets + 4 static
    args + 2 string heads (length+padded data). 4-arg or 5-arg variants
    would have a different byte count."""
    calldata = (
        _bare_factory()
        .clone(
            asset_name="A",
            asset_symbol="B",
            underlying_token=BASE_USDC,
            redemption_delay_seconds=0,
            owner=SAMPLE_OWNER,
        )
        .calldata
    )
    # 4 selector + 6 head slots (32 each = 192) + 2 string tails (32 len + 32 data = 64 each)
    assert len(calldata) == 4 + 6 * 32 + 2 * 64


def test_clone_calldata_defaults_dao_fee_to_zero():
    factory = _bare_factory()
    with_default = factory.clone(
        asset_name="x",
        asset_symbol="y",
        underlying_token=BASE_USDC,
        redemption_delay_seconds=0,
        owner=SAMPLE_OWNER,
    ).calldata
    with_explicit_zero = factory.clone(
        asset_name="x",
        asset_symbol="y",
        underlying_token=BASE_USDC,
        redemption_delay_seconds=0,
        owner=SAMPLE_OWNER,
        dao_fee_package_index=0,
    ).calldata
    assert with_default == with_explicit_zero


# --- public decoder helper --------------------------------------------------


def test_decode_clone_result_round_trip():
    """`FusionFactory.decode_clone_result(bytes)` is the public entry-point
    for off-context flows (agent runtimes broadcasting via external signer,
    replaying preview at a historical block). Round-trips through `eth_abi`
    encoding and the EIP-55 normalizer."""
    plasma_vault_lc = "0x" + "a1" * 20
    access_manager_lc = "0x" + "b2" * 20
    raw = abi_encode(
        [_FUSION_INSTANCE_TUPLE_TYPE],
        [
            (
                138,
                8,
                "IPOR USDC Vault",
                "ipUSDC",
                8,
                str(BASE_USDC).lower(),
                "USDC",
                6,
                str(SAMPLE_OWNER).lower(),
                plasma_vault_lc,
                "0x" + "33" * 20,
                access_manager_lc,
                "0x" + "44" * 20,
                "0x" + "55" * 20,
                "0x" + "66" * 20,
                "0x" + "77" * 20,
                "0x" + "88" * 20,
            )
        ],
    )
    inst = FusionFactory.decode_clone_result(raw)
    assert inst.index == 138
    assert inst.version == 8
    assert inst.asset_name == "IPOR USDC Vault"
    assert inst.asset_symbol == "ipUSDC"
    # Addresses normalized to EIP-55 (not raw lowercase from eth_abi).
    assert inst.underlying_token == BASE_USDC
    assert inst.initial_owner == SAMPLE_OWNER
    assert inst.plasma_vault == Web3.to_checksum_address(plasma_vault_lc)
    assert inst.access_manager == Web3.to_checksum_address(access_manager_lc)


# --- live eth_call preview (no gas, no state change) ------------------------


def test_simulate_clone_preview_base(web3_base):
    """eth_call against IporFusionFactoryProxy on BASE — decodes FusionInstance
    and verifies the CREATE2 address layout."""
    ctx = Web3Context(web3=web3_base, chain_id=8453, signer=SAMPLE_OWNER)
    factory = FusionFactory(ctx, BASE_FUSION_FACTORY)
    instance = factory.clone(
        asset_name="IPOR USDC Vault",
        asset_symbol="ipUSDC",
        underlying_token=BASE_USDC,
        redemption_delay_seconds=86400,
        owner=SAMPLE_OWNER,
        dao_fee_package_index=0,
    ).call()

    # Sanity on structural fields.
    assert instance.index > 0
    assert instance.version >= 1
    assert instance.asset_name == "IPOR USDC Vault"
    assert instance.asset_symbol == "ipUSDC"
    assert instance.underlying_token == BASE_USDC
    assert instance.underlying_token_symbol == "USDC"
    assert instance.underlying_token_decimals == 6
    assert instance.initial_owner == SAMPLE_OWNER

    # All 8 cloned addresses must be non-zero (CREATE2-deterministic).
    cloned = [
        instance.plasma_vault,
        instance.plasma_vault_base,
        instance.access_manager,
        instance.fee_manager,
        instance.rewards_manager,
        instance.withdraw_manager,
        instance.context_manager,
        instance.price_manager,
    ]
    for addr in cloned:
        assert int(addr, 16) != 0, f"unexpected zero address in clone preview: {addr}"
        assert len(addr) == 42

    LOG.info(
        "clone preview: index=%d version=%d plasmaVault=%s",
        instance.index,
        instance.version,
        instance.plasma_vault,
    )


def test_simulate_clone_two_calls_same_index(web3_base):
    """eth_call is read-only — calling clone() twice without intervening
    state changes returns the same FusionInstance.index (factory storage
    not mutated)."""
    ctx = Web3Context(web3=web3_base, chain_id=8453, signer=SAMPLE_OWNER)
    factory = FusionFactory(ctx, BASE_FUSION_FACTORY)
    first = factory.clone(
        asset_name="a",
        asset_symbol="b",
        underlying_token=BASE_USDC,
        redemption_delay_seconds=86400,
        owner=SAMPLE_OWNER,
    ).call()
    second = factory.clone(
        asset_name="a",
        asset_symbol="b",
        underlying_token=BASE_USDC,
        redemption_delay_seconds=86400,
        owner=SAMPLE_OWNER,
    ).call()
    assert first.index == second.index, (
        "eth_call must not mutate the factory index; got "
        f"first={first.index} second={second.index}"
    )


def test_clone_func_sig_constant_matches_deployed_selector():
    """`CLONE_FUNC_SIG` is the single source of truth — derived selector
    must equal the deployed impl bytecode selector and the
    `CLONE_SELECTOR` byte constant."""
    derived = function_signature_to_4byte_selector(FusionFactory.CLONE_FUNC_SIG)
    assert derived.hex() == "8697b10a"
    assert FusionFactory.CLONE_SELECTOR == derived


def test_decode_clone_calldata_round_trip():
    """Build calldata via `clone(...).calldata`, decode back to `CloneArgs`,
    assert all 6 fields survive ABI encoding round-trip (incl. EIP-55
    normalization on address fields)."""
    encoded = (
        _bare_factory()
        .clone(
            asset_name="IPOR USDC Vault",
            asset_symbol="ipUSDC",
            underlying_token=BASE_USDC,
            redemption_delay_seconds=86400,
            owner=SAMPLE_OWNER,
            dao_fee_package_index=2,
        )
        .calldata
    )
    args = FusionFactory.decode_clone_calldata(encoded)
    assert isinstance(args, CloneArgs)
    assert args.asset_name == "IPOR USDC Vault"
    assert args.asset_symbol == "ipUSDC"
    assert args.underlying_token == BASE_USDC
    assert args.redemption_delay_seconds == 86400
    assert args.owner == SAMPLE_OWNER
    assert args.dao_fee_package_index == 2


def test_decode_clone_calldata_rejects_unknown_selector():
    """Selector mismatch raises — guards callers that route by tx selector."""
    with pytest.raises(ValueError, match="does not match"):
        FusionFactory.decode_clone_calldata(b"\xde\xad\xbe\xef" + b"\x00" * 32)


def test_decode_clone_calldata_rejects_short_calldata():
    """Calldata too short for a selector also raises (no silent garbage)."""
    with pytest.raises(ValueError, match="does not match"):
        FusionFactory.decode_clone_calldata(b"\x86\x97\xb1")


def test_encoder_classmethod_builds_ctx_less_wrapper():
    """`FusionFactory.encoder(addr)` returns a wrapper with no ctx but a
    real address; `.calldata` works, but `.call()` / `.send()` raise."""
    f = FusionFactory.encoder(BASE_FUSION_FACTORY)
    assert f.address == BASE_FUSION_FACTORY
    call = f.clone(
        asset_name="a",
        asset_symbol="b",
        underlying_token=BASE_USDC,
        redemption_delay_seconds=0,
        owner=SAMPLE_OWNER,
    )
    # calldata works without ctx
    assert call.calldata[:4] == FusionFactory.CLONE_SELECTOR
    # .call() requires a ctx — should refuse since encoder()'s placeholder is None
    with pytest.raises(ValueError, match="Web3Context required"):
        call.call()


def test_encoder_classmethod_defaults_to_zero_address():
    """No address arg → placeholder zero address. Calldata still encodes
    fine because `to` is irrelevant to ABI-encoded args."""
    f = FusionFactory.encoder()
    assert int(f.address, 16) == 0
    data = f.clone(
        asset_name="a",
        asset_symbol="b",
        underlying_token=BASE_USDC,
        redemption_delay_seconds=0,
        owner=SAMPLE_OWNER,
    ).calldata
    assert data[:4] == FusionFactory.CLONE_SELECTOR


def test_clone_calldata_known_layout():
    """Spot-check: leading word after selector is the offset to the first
    dynamic string (assetName). For 6-arg signature with 2 strings, both
    dynamic-arg head slots point past the 6-word header (offset >= 0xa0)."""
    calldata = (
        _bare_factory()
        .clone(
            asset_name="IPOR USDC Vault",
            asset_symbol="ipUSDC",
            underlying_token=BASE_USDC,
            redemption_delay_seconds=0,
            owner=SAMPLE_OWNER,
            dao_fee_package_index=0,
        )
        .calldata
    )
    first_head = int.from_bytes(calldata[4:36], "big")
    second_head = int.from_bytes(calldata[36:68], "big")
    # 6 head slots × 32 bytes = 0xc0 — first string body starts right after.
    assert first_head == 0xC0
    assert second_head > first_head, "assetSymbol offset must point past assetName"

"""PlasmaVaultGovernance setters: encoding + live `getFuses()` probe on BASE.

Two test paths:

  * `test_encode_*` — pure helpers, no RPC. Each setter encoder is asserted
    against its 4-byte selector AND round-tripped through `eth_abi.decode` to
    confirm the on-chain canonical shape is what we produce.

  * `test_simulate_plasma_vault_*_base` — uses the `web3_base` session
    fixture (set via `BASE_PROVIDER_URL`) to read live state from the
    plasmaVault deployed by scripts/smoke_base_deploy.py
    (`0xc4f086a9389c647ffa9b8f255150c3d1fc05d4fd`, immutable proxy).

The 5 selectors below were independently verified against the impl bytecode
via the `smoke_base_postsetup` script (2026-05-12).
"""

from __future__ import annotations

import logging

from eth_abi import decode as abi_decode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from ipor_fusion import Web3Context
from ipor_fusion.core import PlasmaVault

LOG = logging.getLogger(__name__)

# Deployed by scripts/smoke_base_deploy.py — addresses are CREATE2-deterministic
# for that factory index and stable across runs.
BASE_PLASMA_VAULT = Web3.to_checksum_address(
    "0xc4f086a9389c647ffa9b8f255150c3d1fc05d4fd"
)
SAMPLE_OWNER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")
SAMPLE_FUSE_A = Web3.to_checksum_address("0x" + "a1" * 20)
SAMPLE_FUSE_B = Web3.to_checksum_address("0x" + "b2" * 20)


# --- pure encoding: selector + ABI round-trip -------------------------------


def test_encode_add_fuses_selector_and_roundtrip():
    fuses = [SAMPLE_FUSE_A, SAMPLE_FUSE_B]
    calldata = PlasmaVault.encode_add_fuses_calldata(fuses)
    assert calldata[:4] == function_signature_to_4byte_selector("addFuses(address[])")
    (decoded,) = abi_decode(["address[]"], calldata[4:])
    assert [Web3.to_checksum_address(a) for a in decoded] == fuses


def test_encode_add_balance_fuse_selector_and_roundtrip():
    calldata = PlasmaVault.encode_add_balance_fuse_calldata(7, SAMPLE_FUSE_A)
    assert calldata[:4] == function_signature_to_4byte_selector(
        "addBalanceFuse(uint256,address)"
    )
    market_id, fuse = abi_decode(["uint256", "address"], calldata[4:])
    assert market_id == 7
    assert Web3.to_checksum_address(fuse) == SAMPLE_FUSE_A


def test_encode_grant_market_substrates_selector_and_roundtrip():
    substrate = b"\x00" * 12 + bytes.fromhex(SAMPLE_FUSE_A[2:])
    calldata = PlasmaVault.encode_grant_market_substrates_calldata(3, [substrate])
    assert calldata[:4] == function_signature_to_4byte_selector(
        "grantMarketSubstrates(uint256,bytes32[])"
    )
    market_id, substrates = abi_decode(["uint256", "bytes32[]"], calldata[4:])
    assert market_id == 3
    assert list(substrates) == [substrate]


def test_encode_setup_markets_limits_selector_and_roundtrip():
    pairs = [(1, 1_000_000_000_000), (2, 500_000_000_000)]
    calldata = PlasmaVault.encode_setup_markets_limits_calldata(pairs)
    assert calldata[:4] == function_signature_to_4byte_selector(
        "setupMarketsLimits((uint256,uint256)[])"
    )
    (decoded,) = abi_decode(["(uint256,uint256)[]"], calldata[4:])
    assert [(int(mkt), int(cap)) for mkt, cap in decoded] == pairs


def test_encode_configure_instant_withdrawal_fuses_selector_and_roundtrip():
    sub1 = b"\x11" * 32
    sub2 = b"\x22" * 32
    configs = [(SAMPLE_FUSE_A, [sub1]), (SAMPLE_FUSE_B, [sub1, sub2])]
    calldata = PlasmaVault.encode_configure_instant_withdrawal_fuses_calldata(configs)
    assert calldata[:4] == function_signature_to_4byte_selector(
        "configureInstantWithdrawalFuses((address,bytes32[])[])"
    )
    (decoded,) = abi_decode(["(address,bytes32[])[]"], calldata[4:])
    assert len(decoded) == 2
    fuse_a, params_a = decoded[0]
    fuse_b, params_b = decoded[1]
    assert Web3.to_checksum_address(fuse_a) == SAMPLE_FUSE_A
    assert list(params_a) == [sub1]
    assert Web3.to_checksum_address(fuse_b) == SAMPLE_FUSE_B
    assert list(params_b) == [sub1, sub2]


def test_call_builder_matches_static_encoder():
    """Call[T] data must equal static encoder output — the wrapper instance
    method and the `@staticmethod` helper must produce identical bytes."""

    # Bare context — `_view`/`_write` use `ctx` only at `.call()`/`.send()` time,
    # so we can build the Call shapes without hitting the network.
    class _BareCtx:
        pass

    vault = PlasmaVault.__new__(PlasmaVault)
    vault._ctx = _BareCtx()
    vault._address = BASE_PLASMA_VAULT

    pairs = [(1, 0)]
    assert vault.setup_markets_limits(pairs).data == (
        PlasmaVault.encode_setup_markets_limits_calldata(pairs)
    )
    assert vault.add_balance_fuse(2, SAMPLE_FUSE_A).data == (
        PlasmaVault.encode_add_balance_fuse_calldata(2, SAMPLE_FUSE_A)
    )
    sub = b"\x01" * 32
    assert vault.configure_instant_withdrawal_fuses(
        [(SAMPLE_FUSE_A, [sub])]
    ).data == PlasmaVault.encode_configure_instant_withdrawal_fuses_calldata(
        [(SAMPLE_FUSE_A, [sub])]
    )


# --- live eth_call against the deployed BASE plasmaVault --------------------


def test_simulate_plasma_vault_get_fuses_base(web3_base):
    """`getFuses()` on the live BASE plasmaVault — proves the PlasmaVault
    wrapper class round-trips correctly against the deployed bytecode and
    the wrapper's `_view`/`Call` path decodes the address[] result type."""
    ctx = Web3Context(web3=web3_base, chain_id=8453, signer=SAMPLE_OWNER)
    vault = PlasmaVault(ctx, BASE_PLASMA_VAULT)
    fuses = vault.get_fuses().call()
    # Returned shape: list[ChecksumAddress]. The vault may or may not have
    # any fuses registered depending on which smoke ran last; we only assert
    # the shape/decoding is valid.
    assert isinstance(fuses, list)
    for addr in fuses:
        assert int(addr, 16) >= 0
        assert len(addr) == 42
    LOG.info("getFuses() on %s → %d fuses", BASE_PLASMA_VAULT, len(fuses))

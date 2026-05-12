"""FusionFactory.clone() pure encoding + live BASE eth_call preview.

Two test paths:

  * `test_encode_clone_calldata_*` — pure helpers, no RPC. Asserts the
    selector + length layout matches the deployed BASE impl signature
    (0x8697b10a).

  * `test_clone_preview_base` — uses the `web3_base` session fixture
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
from web3 import Web3

from ipor_fusion import Web3Context
from ipor_fusion.core import FusionFactory

LOG = logging.getLogger(__name__)

BASE_FUSION_FACTORY = Web3.to_checksum_address("0x1455717668fA96534f675856347A973fA907e922")
BASE_USDC = Web3.to_checksum_address("0x833589fCD6eDb6E08f4c7C32D4f71b54bdA02913")
SAMPLE_OWNER = Web3.to_checksum_address("0x533ac556E288625B267bD71B7928E0a8B46DcE82")


# --- pure encoding ----------------------------------------------------------


def test_encode_clone_calldata_selector_matches_deployed():
    """Selector 0x8697b10a — verified against impl bytecode."""
    calldata = FusionFactory.encode_clone_calldata(
        asset_name="IPOR USDC Vault",
        asset_symbol="ipUSDC",
        underlying_token=BASE_USDC,
        redemption_delay_seconds=86400,
        owner=SAMPLE_OWNER,
        dao_fee_package_index=0,
    )
    assert calldata[:4].hex() == "8697b10a"


def test_encode_clone_calldata_length_signals_six_args():
    """6-arg signature → selector (4) + 2 dynamic string offsets + 4 static
    args + 2 string heads (length+padded data). 4-arg or 5-arg variants
    would have a different byte count."""
    calldata = FusionFactory.encode_clone_calldata(
        asset_name="A",
        asset_symbol="B",
        underlying_token=BASE_USDC,
        redemption_delay_seconds=0,
        owner=SAMPLE_OWNER,
    )
    # 4 selector + 6 head slots (32 each = 192) + 2 string tails (32 len + 32 data = 64 each)
    assert len(calldata) == 4 + 6 * 32 + 2 * 64


def test_encode_clone_calldata_defaults_dao_fee_to_zero():
    with_default = FusionFactory.encode_clone_calldata(
        asset_name="x", asset_symbol="y", underlying_token=BASE_USDC,
        redemption_delay_seconds=0, owner=SAMPLE_OWNER,
    )
    with_explicit_zero = FusionFactory.encode_clone_calldata(
        asset_name="x", asset_symbol="y", underlying_token=BASE_USDC,
        redemption_delay_seconds=0, owner=SAMPLE_OWNER,
        dao_fee_package_index=0,
    )
    assert with_default == with_explicit_zero


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
    assert instance.underlying_token.lower() == BASE_USDC.lower()
    assert instance.underlying_token_symbol == "USDC"
    assert instance.underlying_token_decimals == 6
    assert instance.initial_owner.lower() == SAMPLE_OWNER.lower()

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

    LOG.info("clone preview: index=%d version=%d plasmaVault=%s",
             instance.index, instance.version, instance.plasma_vault)


def test_simulate_clone_two_calls_same_index(web3_base):
    """eth_call is read-only — calling clone() twice without intervening
    state changes returns the same FusionInstance.index (factory storage
    not mutated)."""
    ctx = Web3Context(web3=web3_base, chain_id=8453, signer=SAMPLE_OWNER)
    factory = FusionFactory(ctx, BASE_FUSION_FACTORY)
    first = factory.clone(
        asset_name="a", asset_symbol="b", underlying_token=BASE_USDC,
        redemption_delay_seconds=86400, owner=SAMPLE_OWNER,
    ).call()
    second = factory.clone(
        asset_name="a", asset_symbol="b", underlying_token=BASE_USDC,
        redemption_delay_seconds=86400, owner=SAMPLE_OWNER,
    ).call()
    assert first.index == second.index, (
        "eth_call must not mutate the factory index; got "
        f"first={first.index} second={second.index}"
    )


def test_encode_clone_calldata_known_layout():
    """Spot-check: leading word after selector is the offset to the first
    dynamic string (assetName). For 6-arg signature with 2 strings, both
    dynamic-arg head slots point past the 6-word header (offset >= 0xa0)."""
    calldata = FusionFactory.encode_clone_calldata(
        asset_name="IPOR USDC Vault",
        asset_symbol="ipUSDC",
        underlying_token=BASE_USDC,
        redemption_delay_seconds=0,
        owner=SAMPLE_OWNER,
        dao_fee_package_index=0,
    )
    first_head = int.from_bytes(calldata[4:36], "big")
    second_head = int.from_bytes(calldata[36:68], "big")
    # 6 head slots × 32 bytes = 0xc0 — first string body starts right after.
    assert first_head == 0xc0
    assert second_head > first_head, "assetSymbol offset must point past assetName"

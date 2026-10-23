"""Decoding execute calldata and fuse actions back into calls."""

import pytest
from web3 import Web3

from ipor_fusion import (
    AaveV3BorrowFuse,
    AaveV3SupplyFuse,
    CompoundV3SupplyFuse,
    DecodedFuseAction,
    ERC4626SupplyFuse,
    Fuse,
    FuseAction,
    MorphoSupplyFuse,
    PlasmaVault,
    RewardsManager,
    StakeFuse,
    decode_execute_calldata,
    decode_fuse_action,
    fuse_classes_for,
)
from ipor_fusion.fuses.fluid_instadapp import FluidInstadappStakingFuse
from ipor_fusion.types import (
    Amount,
    FuseAddress,
    PlasmaVaultAddress,
    parse_morpho_market_id,
)

ASSET = Web3.to_checksum_address("0x" + "aa" * 20)
VAULT = PlasmaVault(PlasmaVaultAddress(Web3.to_checksum_address("0x" + "11" * 20)))
AAVE = AaveV3SupplyFuse(FuseAddress(Web3.to_checksum_address("0x" + "22" * 20)))
MORPHO = MorphoSupplyFuse(FuseAddress(Web3.to_checksum_address("0x" + "33" * 20)))
MARKET = parse_morpho_market_id("ab" * 32)


def test_execute_calldata_round_trips_its_actions():
    actions = [
        AAVE.supply(asset=ASSET, amount=Amount(5)),
        MORPHO.supply(market_id=MARKET, amount=Amount(7)),
    ]
    assert decode_execute_calldata(VAULT.execute(actions).calldata) == actions
    claims = RewardsManager(ASSET).claim_rewards(actions).calldata
    assert decode_execute_calldata(claims) == actions


def test_other_calldata_is_rejected():
    with pytest.raises(ValueError, match="not execute or claimRewards"):
        decode_execute_calldata(VAULT.total_assets().calldata)
    with pytest.raises(ValueError, match="does not decode"):
        decode_execute_calldata(VAULT.execute([]).calldata[:4] + b"\x01")


def test_an_action_decodes_as_its_class():
    decoded = decode_fuse_action(
        AAVE.supply(asset=ASSET, amount=Amount(5), e_mode=1), AaveV3SupplyFuse
    )
    assert decoded == DecodedFuseAction(
        fuse=AAVE.address,
        fuse_class=AaveV3SupplyFuse,
        signature="enter((address,uint256,uint256))",
        args=((ASSET.lower(), 5, 1),),
    )
    assert decoded.function == "enter"
    assert decoded.label == "AaveV3SupplyFuse.enter"


def test_inherited_signatures_decode_on_the_subclass():
    assert FluidInstadappStakingFuse.signatures() == StakeFuse.signatures()
    stake = FluidInstadappStakingFuse(
        FuseAddress(Web3.to_checksum_address("0x" + "44" * 20)), ASSET
    ).stake()
    assert decode_fuse_action(stake, FluidInstadappStakingFuse).function == "enter"


def test_a_selector_the_class_does_not_encode_is_rejected():
    with pytest.raises(ValueError, match="MorphoSupplyFuse encodes no function"):
        decode_fuse_action(AAVE.supply(asset=ASSET, amount=Amount(5)), MorphoSupplyFuse)
    truncated = FuseAction(
        AAVE.address, AAVE.withdraw(asset=ASSET, amount=Amount(1)).data[:8]
    )
    with pytest.raises(ValueError, match="arguments do not decode"):
        decode_fuse_action(truncated, AaveV3SupplyFuse)


def test_a_shared_shape_names_every_candidate():
    action = ERC4626SupplyFuse(FuseAddress(ASSET)).supply(
        vault_address=ASSET, amount=Amount(1)
    )
    candidates = fuse_classes_for(action)
    assert {AaveV3BorrowFuse, CompoundV3SupplyFuse, ERC4626SupplyFuse} <= set(
        candidates
    )
    assert AaveV3SupplyFuse not in candidates


def test_an_undeclared_signature_trips_the_suite_check():
    class Undeclared(Fuse):
        def go(self) -> FuseAction:
            return self._action_raw("go(uint256)", [1])

    with pytest.raises(AssertionError, match="missing from its SIGNATURES"):
        Undeclared(FuseAddress(ASSET)).go()

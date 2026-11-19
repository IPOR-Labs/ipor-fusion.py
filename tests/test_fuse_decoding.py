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
    MorphoBorrowFuse,
    MorphoCollateralFuse,
    MorphoFlashLoanFuse,
    MorphoSupplyFuse,
    PlasmaVault,
    RewardsManager,
    StakeFuse,
    decode_execute_calldata,
    decode_fuse_action,
    fuse_classes_for,
)
from ipor_fusion.fuses.fluid_instadapp import FluidInstadappStakingFuse
from ipor_fusion.fuses.lido_lend import (
    LidoLendBorrowFuse,
    LidoLendCollateralFuse,
    LidoLendFlashLoanFuse,
    LidoLendSettleFuse,
    LidoLendSupplyFuse,
)
from ipor_fusion.types import (
    Amount,
    FuseAddress,
    PlasmaVaultAddress,
    Shares,
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


LIDO_FUSE = FuseAddress(Web3.to_checksum_address("0x" + "55" * 20))


def test_lido_lend_shared_selectors_name_every_candidate():
    supply = LidoLendSupplyFuse(LIDO_FUSE).supply(market_id=MARKET, amount=Amount(1))
    assert {
        LidoLendSupplyFuse,
        LidoLendCollateralFuse,
        MorphoSupplyFuse,
        MorphoCollateralFuse,
    } <= set(fuse_classes_for(supply))
    borrow = LidoLendBorrowFuse(LIDO_FUSE).borrow(market_id=MARKET, amount=Amount(1))
    candidates = fuse_classes_for(borrow)
    assert {LidoLendBorrowFuse, MorphoBorrowFuse} <= set(candidates)
    assert LidoLendSettleFuse not in candidates
    flash = LidoLendFlashLoanFuse(LIDO_FUSE).flash_loan(
        asset=ASSET, amount=Amount(1), actions=[]
    )
    assert {LidoLendFlashLoanFuse, MorphoFlashLoanFuse} <= set(fuse_classes_for(flash))


def test_lido_lend_settle_selector_is_unambiguous():
    settle = LidoLendSettleFuse(LIDO_FUSE).settle_borrower(
        market_id=MARKET,
        borrower=ASSET,
        supply_shares_budget=Shares(9),
        min_collateral_per_supply_asset_wad=0,
    )
    assert fuse_classes_for(settle) == [LidoLendSettleFuse]


def test_lido_lend_actions_decode_their_arguments():
    market = bytes.fromhex("ab" * 32)
    decoded = decode_fuse_action(
        LidoLendBorrowFuse(LIDO_FUSE).borrow_shares(market_id=MARKET, shares=Shares(4)),
        LidoLendBorrowFuse,
    )
    assert decoded.signature == "enter((bytes32,uint256,uint256))"
    assert decoded.args == ((market, 0, 4),)
    assert decoded.label == "LidoLendBorrowFuse.enter"

    settle = decode_fuse_action(
        LidoLendSettleFuse(LIDO_FUSE).settle_queue_root(
            market_id=MARKET,
            supply_shares_budget=Shares(7),
            max_iterations=3,
            min_collateral_per_supply_asset_wad=815,
        ),
        LidoLendSettleFuse,
    )
    assert settle.signature == "enter((bytes32,address,uint256,uint256,uint256))"
    assert settle.args == ((market, "0x" + "00" * 20, 7, 3, 815),)

    supply = decode_fuse_action(
        LidoLendSupplyFuse(LIDO_FUSE).withdraw(market_id=MARKET, amount=Amount(2)),
        LidoLendSupplyFuse,
    )
    assert supply.function == "exit"
    assert supply.args == ((market, 2),)

import logging
import os

import pytest
from web3 import Web3

from constants import (
    ANVIL_WALLET,
    ETHEREUM_AAVE_V3_SUPPLY_FUSE,
    ETHEREUM_AAVE_V3_BORROW_FUSE,
)
from ipor_fusion.testing import AnvilTestContainerStarter, ForkedWeb3Context
from ipor_fusion import Roles, Markets, PlasmaVault, AccessManager, ERC20
from ipor_fusion.fuses import AaveV3SupplyFuse, AaveV3BorrowFuse, Erc4626SupplyFuse
from ipor_fusion.addresses import ETHEREUM_WBTC, ETHEREUM_WETH
from ipor_fusion.types import Amount

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

fork_url = os.environ["ETHEREUM_PROVIDER_URL"]


@pytest.fixture(scope="module")
def anvil():
    with AnvilTestContainerStarter(fork_url) as a:
        yield a


def test_should_borrow_aave_v3(anvil):
    anvil.reset_fork(22616438)

    atomist = Web3.to_checksum_address("0x46B48240f61C831B85fCf4c198C98028Ab8EE68d")
    vault_address = Web3.to_checksum_address(
        "0x1fdf5dc3F915Cb40E0AD5690DE51E3cB464d1BAD"
    )
    wbtc_holder = Web3.to_checksum_address("0xE940ae8cF59fE2709BBc572CBAD2633fB45Abf46")

    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )

    # Grant roles
    forked_ctx.prank(atomist)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)
    access_manager.grant_role(Roles.WHITELIST_ROLE, wbtc_holder, 0)

    forked_ctx.prank(ANVIL_WALLET)

    aave_supply = AaveV3SupplyFuse(ETHEREUM_AAVE_V3_SUPPLY_FUSE)
    aave_borrow = AaveV3BorrowFuse(ETHEREUM_AAVE_V3_BORROW_FUSE)

    wbtc_collateral_amount = int(1e8)

    # Approve and deposit
    forked_ctx.prank(wbtc_holder)
    ERC20(forked_ctx, ETHEREUM_WBTC).approve(
        spender=plasma_vault.address, amount=wbtc_collateral_amount
    )
    plasma_vault.deposit(assets=wbtc_collateral_amount, receiver=wbtc_holder)

    assert ERC20(forked_ctx, ETHEREUM_WBTC).balance_of(vault_address) == 1e8

    forked_ctx.prank(ANVIL_WALLET)

    # Grant market substrates
    anvil.grant_market_substrates(
        _from=atomist,
        plasma_vault=vault_address,
        market_id=Markets.AAVE_V3,
        substrates=[
            "0000000000000000000000002260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
            "000000000000000000000000C02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        ],
    )

    # Supply to Aave V3
    supply = aave_supply.supply(
        asset=ETHEREUM_WBTC,
        amount=wbtc_collateral_amount,
        e_mode=1,
    )
    plasma_vault.execute([supply])

    assert ERC20(forked_ctx, ETHEREUM_WBTC).balance_of(vault_address) == 0

    # Borrow WETH
    weth_borrow_amount = int(20e18)
    borrow = aave_borrow.borrow(asset=ETHEREUM_WETH, amount=weth_borrow_amount)
    plasma_vault.execute([borrow])

    assert (
        ERC20(forked_ctx, ETHEREUM_WETH).balance_of(vault_address) == weth_borrow_amount
    )

    # Repay
    repay = aave_borrow.repay(asset=ETHEREUM_WETH, amount=weth_borrow_amount)
    plasma_vault.execute([repay])

    assert ERC20(forked_ctx, ETHEREUM_WETH).balance_of(vault_address) == 0

    # Withdraw
    withdraw = aave_supply.withdraw(
        ETHEREUM_WBTC,
        int(wbtc_collateral_amount * 0.99999),
    )
    plasma_vault.execute([withdraw])

    assert ERC20(forked_ctx, ETHEREUM_WBTC).balance_of(vault_address) == int(
        wbtc_collateral_amount * 0.99999
    )


def test_should_deposit_to_plasma_vault(anvil):
    anvil.reset_fork(22687555)

    atomist = Web3.to_checksum_address("0x46B48240f61C831B85fCf4c198C98028Ab8EE68d")
    vault_address = Web3.to_checksum_address(
        "0x1fdf5dc3F915Cb40E0AD5690DE51E3cB464d1BAD"
    )
    wbtc_holder = Web3.to_checksum_address("0xE940ae8cF59fE2709BBc572CBAD2633fB45Abf46")
    erc4626_fuse_address = Web3.to_checksum_address(
        "0x970b4f5522685D4826eceb0377B3DdBF12836dFd"
    )
    weth_vault_address = Web3.to_checksum_address(
        "0x9824dCdac89F208Bf8b5Cb5C4Dc41F04a0878607"
    )

    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )

    # Grant roles
    forked_ctx.prank(atomist)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)
    access_manager.grant_role(Roles.WHITELIST_ROLE, wbtc_holder, 0)

    forked_ctx.prank(ANVIL_WALLET)

    aave_supply = AaveV3SupplyFuse(ETHEREUM_AAVE_V3_SUPPLY_FUSE)
    aave_borrow = AaveV3BorrowFuse(ETHEREUM_AAVE_V3_BORROW_FUSE)
    erc4626 = Erc4626SupplyFuse(erc4626_fuse_address)

    # Grant market substrates for ERC4626
    anvil.grant_market_substrates(
        _from=atomist,
        plasma_vault=vault_address,
        market_id=Markets.ERC4626_0013,
        substrates=["0000000000000000000000009824dCdac89F208Bf8b5Cb5C4Dc41F04a0878607"],
    )

    # Grant market substrates for Aave V3
    anvil.grant_market_substrates(
        _from=atomist,
        plasma_vault=vault_address,
        market_id=Markets.AAVE_V3,
        substrates=[
            "0000000000000000000000002260FAC5E5542a773Aa44fBCfeDf7C193bc2C599",
            "000000000000000000000000C02aaA39b223FE8D0A0e5C4F27eAD9083C756Cc2",
        ],
    )

    wbtc_collateral_amount = Amount(int(1e8))

    # Approve and deposit
    forked_ctx.prank(wbtc_holder)
    ERC20(forked_ctx, ETHEREUM_WBTC).approve(
        spender=plasma_vault.address, amount=wbtc_collateral_amount
    )
    plasma_vault.deposit(assets=wbtc_collateral_amount, receiver=wbtc_holder)

    forked_ctx.prank(ANVIL_WALLET)

    # Setup WETH plasma_vault permissions
    weth_vault = PlasmaVault(forked_ctx, weth_vault_address)
    weth_access = AccessManager(forked_ctx, weth_vault.get_access_manager_address())
    weth_atomist = Web3.to_checksum_address(
        "0xf2C6a2225BE9829eD77263b032E3D92C52aE6694"
    )

    forked_ctx.prank(weth_atomist)
    weth_access.grant_role(
        role_id=Roles.WHITELIST_ROLE, account=vault_address, execution_delay=0
    )

    cap = weth_vault.get_total_supply_cap()
    weth_vault.set_total_supply_cap(int(cap / 4))

    forked_ctx.prank(ANVIL_WALLET)

    assert ERC20(forked_ctx, ETHEREUM_WBTC).balance_of(vault_address) == 1e8

    # Supply to Aave V3
    supply_aave = aave_supply.supply(
        asset=ETHEREUM_WBTC,
        amount=wbtc_collateral_amount,
        e_mode=1,
    )
    plasma_vault.execute([supply_aave])

    assert ERC20(forked_ctx, ETHEREUM_WBTC).balance_of(vault_address) == 0

    # Borrow WETH
    weth_borrow_amount = Amount(int(20e18))
    borrow = aave_borrow.borrow(asset=ETHEREUM_WETH, amount=weth_borrow_amount)
    plasma_vault.execute([borrow])

    assert (
        ERC20(forked_ctx, ETHEREUM_WETH).balance_of(vault_address) == weth_borrow_amount
    )

    log.info("weth_borrow_amount: %s", weth_borrow_amount / 1e18)

    # Supply to ERC4626 vault
    supply_erc4626 = erc4626.supply(weth_vault_address, weth_borrow_amount)

    assert (
        ERC20(forked_ctx, ETHEREUM_WETH).balance_of(vault_address) == weth_borrow_amount
    )

    plasma_vault.execute([supply_erc4626])

    assert ERC20(forked_ctx, ETHEREUM_WETH).balance_of(vault_address) == 0

    # Withdraw from ERC4626 vault
    withdraw_erc4626 = erc4626.withdraw(weth_vault_address, weth_borrow_amount)

    anvil.move_time(60)

    plasma_vault.execute([withdraw_erc4626])

    assert ERC20(forked_ctx, ETHEREUM_WETH).balance_of(vault_address) > 0

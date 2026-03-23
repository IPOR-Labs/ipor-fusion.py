import logging
import os

import pytest
from eth_typing import BlockNumber

from addresses import ARBITRUM_USDC
from constants import ARBITRUM_PILOT_SCHEDULED_PLASMA_VAULT, ANVIL_WALLET
from ipor_fusion.testing import AnvilTestContainerStarter, ForkedWeb3Context
from ipor_fusion import (
    Roles,
    IporFusionMarkets,
    PlasmaVault,
    AccessManager,
    ERC20,
    WithdrawManager,
)

USDC_ADDRESS = ARBITRUM_USDC

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

fork_url = os.environ["ARBITRUM_PROVIDER_URL"]


@pytest.fixture(scope="module")
def anvil():
    with AnvilTestContainerStarter(fork_url, BlockNumber(250690377)) as a:
        yield a


def setup_vault_basic(anvil):
    vault_address = ARBITRUM_PILOT_SCHEDULED_PLASMA_VAULT
    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )

    owner = access_manager.owner()
    forked_ctx.prank(owner)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)
    access_manager.grant_role(Roles.WHITELIST_ROLE, ANVIL_WALLET, 0)

    forked_ctx.prank(ANVIL_WALLET)
    return forked_ctx, plasma_vault, access_manager


def setup_vault_with_atomist(anvil):
    forked_ctx, plasma_vault, access_manager = setup_vault_basic(anvil)
    owner = access_manager.owner()
    forked_ctx.prank(owner)
    access_manager.grant_role(Roles.ATOMIST_ROLE, ANVIL_WALLET, 0)
    forked_ctx.prank(ANVIL_WALLET)
    return forked_ctx, plasma_vault, access_manager


def test_should_deposit(anvil):
    """Test depositing USDC into the plasma vault."""
    anvil.reset_fork(268934406)

    forked_ctx, plasma_vault, _ = setup_vault_basic(anvil)
    vault_address = plasma_vault.address

    usdc = ERC20(forked_ctx, USDC_ADDRESS)

    amount = 100_000000
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    forked_ctx.prank(whale_account)
    usdc.transfer(ANVIL_WALLET, amount)
    forked_ctx.prank(ANVIL_WALLET)
    usdc.approve(vault_address, amount)

    vault_total_assets_before = plasma_vault.total_assets()
    user_vault_balance_before = plasma_vault.balance_of(ANVIL_WALLET)

    plasma_vault.deposit(amount, ANVIL_WALLET)

    vault_total_assets_after = plasma_vault.total_assets()
    user_vault_balance_after = plasma_vault.balance_of(ANVIL_WALLET)

    assert (
        100_000000 < vault_total_assets_after - vault_total_assets_before < 100_100000
    )
    assert (
        100_00000000
        < user_vault_balance_after - user_vault_balance_before
        < 100_10000000
    )

    assert plasma_vault.total_assets_in_market(IporFusionMarkets.AAVE_V3) == 0


def test_should_mint(anvil):
    """Test minting shares in the plasma vault."""
    anvil.reset_fork(268934406)

    forked_ctx, plasma_vault, _ = setup_vault_basic(anvil)
    vault_address = plasma_vault.address

    usdc = ERC20(forked_ctx, USDC_ADDRESS)

    amount = 110_000000
    shares_amount = 100 * 10 ** plasma_vault.decimals()
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    forked_ctx.prank(whale_account)
    usdc.transfer(ANVIL_WALLET, amount)
    forked_ctx.prank(ANVIL_WALLET)
    usdc.approve(vault_address, amount)

    vault_total_assets_before = plasma_vault.total_assets()
    user_vault_balance_before = plasma_vault.balance_of(ANVIL_WALLET)
    plasma_vault_underlying_balance_before = usdc.balance_of(vault_address)

    plasma_vault.mint(shares_amount, ANVIL_WALLET)

    vault_total_assets_after = plasma_vault.total_assets()
    user_vault_balance_after = plasma_vault.balance_of(ANVIL_WALLET)
    user_vault_underlying_balance_after = plasma_vault.max_withdraw(ANVIL_WALLET)

    assert (
        abs(
            vault_total_assets_after
            - (vault_total_assets_before + user_vault_underlying_balance_after)
        )
        < 100000
    ), "vaultTotalAssetsAfter and before"
    assert (
        abs(user_vault_balance_after - (user_vault_balance_before + shares_amount))
        < 5000
    ), "userVaultBalanceAfter and before vault"
    assert (
        abs(
            plasma_vault_underlying_balance_before
            + user_vault_underlying_balance_after
            - usdc.balance_of(vault_address)
        )
        < 5000
    ), "ERC20(USDC).balanceOf(address(plasmaVault))"
    assert (
        abs(
            plasma_vault_underlying_balance_before
            + user_vault_underlying_balance_after
            - vault_total_assets_after
        )
        < 5000
    ), "vaultTotalAssetsAfter"
    assert plasma_vault.total_assets_in_market(IporFusionMarkets.AAVE_V3) == 0


def test_should_redeem(anvil):
    anvil.reset_fork(268934406)

    forked_ctx, plasma_vault, _ = setup_vault_with_atomist(anvil)
    vault_address = plasma_vault.address
    withdraw_manager = WithdrawManager(
        forked_ctx, plasma_vault.withdraw_manager_address()
    )

    usdc = ERC20(forked_ctx, USDC_ADDRESS)

    amount = 100_000000
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    forked_ctx.prank(whale_account)
    usdc.transfer(ANVIL_WALLET, amount)
    forked_ctx.prank(ANVIL_WALLET)
    usdc.approve(vault_address, amount)

    vault_total_assets_before = plasma_vault.total_assets()
    user_vault_balance_before = plasma_vault.balance_of(ANVIL_WALLET)
    erc_20_user_balance_before = usdc.balance_of(ANVIL_WALLET)

    plasma_vault.deposit(amount, ANVIL_WALLET)

    anvil.move_time(7 * 60 * 60)  # 7 hours

    to_redeem = 50 * 10 ** plasma_vault.decimals()
    to_withdraw = plasma_vault.convert_to_assets(to_redeem)

    withdraw_manager.update_withdraw_window(7 * 60 * 60)  # 7 hours

    withdraw_manager.request(to_withdraw)

    anvil.move_time(60 * 60)  # 1 hour

    withdraw_manager.release_funds()

    plasma_vault.redeem(to_redeem, ANVIL_WALLET, ANVIL_WALLET)

    vault_total_assets_after = plasma_vault.total_assets()
    user_vault_balance_after = plasma_vault.balance_of(ANVIL_WALLET)

    assert (
        abs(vault_total_assets_after - (vault_total_assets_before + 50_000000)) < 100000
    ), "vaultTotalAssetsAfter and before"

    assert (
        abs(
            user_vault_balance_after
            - (user_vault_balance_before + 50 * 10 ** plasma_vault.decimals())
        )
        < 10000000
    ), "userVaultBalanceAfter"

    assert (
        abs(usdc.balance_of(ANVIL_WALLET) - (erc_20_user_balance_before - 50_000000))
        < 100000
    ), "USDC balance of user"

    assert plasma_vault.total_assets_in_market(IporFusionMarkets.AAVE_V3) == 0


def test_should_withdraw(anvil):
    """Test withdrawing assets from the plasma vault."""
    anvil.reset_fork(268934406)

    forked_ctx, plasma_vault, _ = setup_vault_with_atomist(anvil)
    vault_address = plasma_vault.address
    withdraw_manager = WithdrawManager(
        forked_ctx, plasma_vault.withdraw_manager_address()
    )

    usdc = ERC20(forked_ctx, USDC_ADDRESS)

    amount = 100_000000
    shares_amount = 100 * 10 ** plasma_vault.decimals()
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    forked_ctx.prank(whale_account)
    usdc.transfer(ANVIL_WALLET, amount)
    forked_ctx.prank(ANVIL_WALLET)
    usdc.approve(vault_address, amount)

    plasma_vault.deposit(amount, ANVIL_WALLET)

    vault_total_assets_before = plasma_vault.total_assets()
    user_vault_balance_before = plasma_vault.balance_of(ANVIL_WALLET)

    anvil.move_time(7 * 60 * 60)  # 7 hours

    to_withdraw = plasma_vault.max_withdraw(ANVIL_WALLET)

    withdraw_manager.update_withdraw_window(7 * 60 * 60)  # 7 hours
    withdraw_manager.request(to_withdraw)

    anvil.move_time(60 * 60)  # 1 hour

    withdraw_manager.release_funds()

    to_withdraw_second = plasma_vault.max_withdraw(ANVIL_WALLET)

    plasma_vault.withdraw(to_withdraw_second, ANVIL_WALLET, ANVIL_WALLET)

    vault_total_assets_after = plasma_vault.total_assets()
    user_vault_balance_after = plasma_vault.balance_of(ANVIL_WALLET)

    assert (
        abs(vault_total_assets_after - (vault_total_assets_before - to_withdraw))
        < 100000
    ), "vaultTotalAssetsAfter and before"
    assert (
        abs(user_vault_balance_before - (user_vault_balance_after + shares_amount))
        < 10000000
    ), "userVaultBalanceAfter"
    assert plasma_vault.total_assets_in_market(IporFusionMarkets.AAVE_V3) == 0


def test_should_transfer(anvil):
    """Test transferring vault shares between users."""
    anvil.reset_fork(268934406)

    forked_ctx, plasma_vault, _ = setup_vault_basic(anvil)
    vault_address = plasma_vault.address

    usdc = ERC20(forked_ctx, USDC_ADDRESS)

    amount = 100_000000
    user_one = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    user_two = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    forked_ctx.prank(whale_account)
    usdc.transfer(user_one, amount)
    forked_ctx.prank(ANVIL_WALLET)
    usdc.approve(vault_address, 3 * amount)

    plasma_vault.deposit(amount, user_one)

    plasma_vault.transfer(user_two, amount)

    user_two_vault_balance = plasma_vault.balance_of(user_two)
    assert user_two_vault_balance == amount, "Incorrect balance after transfer"


def test_should_transfer_from(anvil):
    """Test transferring vault shares between users using transferFrom."""
    anvil.reset_fork(268934406)

    forked_ctx, plasma_vault, _ = setup_vault_basic(anvil)
    vault_address = plasma_vault.address

    usdc = ERC20(forked_ctx, USDC_ADDRESS)

    amount = 100_000000
    user_one = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    user_two = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    forked_ctx.prank(whale_account)
    usdc.transfer(user_one, amount)
    forked_ctx.prank(ANVIL_WALLET)
    usdc.approve(vault_address, 3 * amount)

    plasma_vault.deposit(amount, user_one)

    plasma_vault.approve(user_one, amount)

    forked_ctx.prank(user_one)
    plasma_vault.transfer_from(user_one, user_two, amount)

    user_two_vault_balance = plasma_vault.balance_of(user_two)
    assert user_two_vault_balance == amount, "Incorrect balance after transfer"

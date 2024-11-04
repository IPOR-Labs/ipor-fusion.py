import logging

import pytest

from constants import ARBITRUM, ALPHA_WALLET
from ipor_fusion.IporFusionMarkets import IporFusionMarkets
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.Roles import Roles
from ipor_fusion.WithdrawManager import WithdrawManager

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)


@pytest.fixture(scope="module", name="plasma_vault")
def plasma_vault_fixture(transaction_executor) -> PlasmaVault:
    """Fixture to create a PlasmaVault instance for testing."""
    return PlasmaVault(
        transaction_executor=transaction_executor,
        plasma_vault_address=ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT,
    )


@pytest.fixture(scope="module", name="withdraw_manager")
def withdraw_manager_fixture(transaction_executor) -> WithdrawManager:
    return WithdrawManager(
        transaction_executor=transaction_executor,
        withdraw_manager_address=ARBITRUM.PILOT.SCHEDULED.WITHDRAW_MANAGER,
    )


def test_should_deposit(
    anvil, cheating_transaction_executor, plasma_vault, usdc, cheating_usdc
):
    """Test depositing USDC into the plasma vault."""
    # Reset the fork and grant necessary roles
    anvil.reset_fork(268934406)
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.ALPHA_ROLE
    )
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.WHITELIST_ROLE
    )

    amount = 100_000000
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    # Set the account for the transaction executor to the whitelisted account
    cheating_transaction_executor.prank(whale_account)
    cheating_usdc.transfer(ALPHA_WALLET, amount)
    usdc.approve(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT, amount)

    vault_total_assets_before = plasma_vault.total_assets()
    user_vault_balance_before = plasma_vault.balance_of(ALPHA_WALLET)

    # Perform the deposit action
    plasma_vault.deposit(amount, ALPHA_WALLET)

    # Record the USDC balance after the deposit
    vault_total_assets_after = plasma_vault.total_assets()
    user_vault_balance_after = plasma_vault.balance_of(ALPHA_WALLET)

    assert vault_total_assets_after - vault_total_assets_before == 100_058437
    assert user_vault_balance_after - user_vault_balance_before == 100_01157810

    assert plasma_vault.total_assets_in_market(IporFusionMarkets.AAVE_V3) == 0


def test_should_mint(
    anvil, cheating_transaction_executor, plasma_vault, usdc, cheating_usdc
):
    """Test minting shares in the plasma vault."""
    # Reset the fork and grant necessary roles
    anvil.reset_fork(268934406)
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.ALPHA_ROLE
    )
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.WHITELIST_ROLE
    )

    # Setup: Define the whitelisted account
    amount = 110_000000
    shares_amount = 100 * 10 ** plasma_vault.decimals()
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    # Transfer USDC to ALPHA_WALLET
    cheating_transaction_executor.prank(whale_account)
    cheating_usdc.transfer(ALPHA_WALLET, amount)
    usdc.approve(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT, amount)

    vault_total_assets_before = plasma_vault.total_assets()
    user_vault_balance_before = plasma_vault.balance_of(ALPHA_WALLET)
    plasma_vault_underlying_balance_before = usdc.balance_of(
        ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT
    )

    # Perform the mint action
    plasma_vault.mint(shares_amount, ALPHA_WALLET)

    # Record the vault state after the minting
    vault_total_assets_after = plasma_vault.total_assets()
    user_vault_balance_after = plasma_vault.balance_of(ALPHA_WALLET)
    user_vault_underlying_balance_after = plasma_vault.max_withdraw(ALPHA_WALLET)

    # Assertions to verify expected outcomes
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
            - usdc.balance_of(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
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


def test_should_redeem(
    anvil,
    cheating_transaction_executor,
    plasma_vault,
    usdc,
    cheating_usdc,
    withdraw_manager,
):
    # Reset the fork and grant necessary roles
    anvil.reset_fork(268934406)
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.ALPHA_ROLE
    )
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.WHITELIST_ROLE
    )
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.ATOMIST_ROLE
    )

    amount = 100_000000
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    # Set the account for the transaction executor to the whitelisted account
    cheating_transaction_executor.prank(whale_account)
    cheating_usdc.transfer(ALPHA_WALLET, amount)
    usdc.approve(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT, amount)

    vault_total_assets_before = plasma_vault.total_assets()
    user_vault_balance_before = plasma_vault.balance_of(ALPHA_WALLET)
    erc_20_user_balance_before = usdc.balance_of(ALPHA_WALLET)

    # Perform the deposit action
    plasma_vault.deposit(amount, ALPHA_WALLET)

    anvil.move_time(7 * 60 * 60)  # 7 hours

    to_redeem = 50 * 10 ** plasma_vault.decimals()
    to_withdraw = plasma_vault.convert_to_assets(to_redeem)

    withdraw_manager.update_withdraw_window(7 * 60 * 60)  # 7 hours

    withdraw_manager.request(to_withdraw)

    anvil.move_time(60 * 60)  # 1 hour

    withdraw_manager.release_funds()

    plasma_vault.redeem(to_redeem, ALPHA_WALLET, ALPHA_WALLET)

    # then
    vault_total_assets_after = plasma_vault.total_assets()
    user_vault_balance_after = plasma_vault.balance_of(ALPHA_WALLET)

    # Assert that total assets changed by expected amount (within tolerance)
    assert (
        abs(vault_total_assets_after - (vault_total_assets_before + 50_000000)) < 100000
    ), "vaultTotalAssetsAfter and before"

    # Assert user's vault balance changed by expected amount
    assert (
        abs(
            user_vault_balance_after
            - (user_vault_balance_before + 50 * 10 ** plasma_vault.decimals())
        )
        < 10000000
    ), "userVaultBalanceAfter"

    # Assert user's USDC balance changed by expected amount
    assert (
        abs(usdc.balance_of(ALPHA_WALLET) - (erc_20_user_balance_before - 50_000000))
        < 100000
    ), "USDC balance of user"

    # Assert no assets in AAVE market
    assert plasma_vault.total_assets_in_market(IporFusionMarkets.AAVE_V3) == 0


def test_should_withdraw(
    anvil,
    cheating_transaction_executor,
    plasma_vault,
    usdc,
    cheating_usdc,
    withdraw_manager,
):
    """Test withdrawing assets from the plasma vault."""
    # Reset the fork and grant necessary roles
    anvil.reset_fork(268934406)
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.ALPHA_ROLE
    )
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.WHITELIST_ROLE
    )
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.ATOMIST_ROLE
    )

    # Setup initial values
    amount = 100_000000  # 100 * 1e6
    shares_amount = 100 * 10 ** plasma_vault.decimals()
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    # Transfer USDC to user_one
    cheating_transaction_executor.prank(whale_account)
    cheating_usdc.transfer(ALPHA_WALLET, amount)
    usdc.approve(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT, amount)

    plasma_vault.deposit(amount, ALPHA_WALLET)

    # Record initial state
    vault_total_assets_before = plasma_vault.total_assets()
    user_vault_balance_before = plasma_vault.balance_of(ALPHA_WALLET)

    # Move time forward 7 hours
    anvil.move_time(7 * 60 * 60)

    to_withdraw = plasma_vault.max_withdraw(ALPHA_WALLET)

    # Update withdraw window and request withdrawal
    withdraw_manager.update_withdraw_window(7 * 60 * 60)  # 7 hours

    cheating_transaction_executor.prank(ALPHA_WALLET)
    withdraw_manager.request(to_withdraw)

    # Move time forward 1 hour
    anvil.move_time(60 * 60)

    withdraw_manager.release_funds()

    to_withdraw_second = plasma_vault.max_withdraw(ALPHA_WALLET)

    # Perform withdrawal
    cheating_transaction_executor.prank(ALPHA_WALLET)
    plasma_vault.withdraw(to_withdraw_second, ALPHA_WALLET, ALPHA_WALLET)

    # Record final state
    vault_total_assets_after = plasma_vault.total_assets()
    user_vault_balance_after = plasma_vault.balance_of(ALPHA_WALLET)

    # Assertions
    assert (
        abs(vault_total_assets_after - (vault_total_assets_before - to_withdraw))
        < 100000
    ), "vaultTotalAssetsAfter and before"
    assert (
        abs(user_vault_balance_before - (user_vault_balance_after + shares_amount))
        < 10000000
    ), "userVaultBalanceAfter"
    assert plasma_vault.total_assets_in_market(IporFusionMarkets.AAVE_V3) == 0


def test_should_transfer(
    anvil, cheating_transaction_executor, plasma_vault, usdc, cheating_usdc
):
    """Test transferring vault shares between users."""
    # Reset the fork and grant necessary roles
    anvil.reset_fork(268934406)
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.ALPHA_ROLE
    )
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.WHITELIST_ROLE
    )

    # Setup initial values
    amount = 100_000000  # 100 * 1e6
    user_one = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    user_two = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    # Transfer USDC to user_one
    cheating_transaction_executor.prank(whale_account)
    cheating_usdc.transfer(user_one, amount)
    usdc.approve(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT, 3 * amount)

    plasma_vault.deposit(amount, user_one)

    # Transfer shares from user_one to user_two
    plasma_vault.transfer(user_two, amount)

    # Verify the transfer
    user_two_vault_balance = plasma_vault.balance_of(user_two)
    assert user_two_vault_balance == amount, "Incorrect balance after transfer"


def test_should_transfer_from(
    anvil, cheating_transaction_executor, plasma_vault, usdc, cheating_usdc
):
    """Test transferring vault shares between users using transferFrom."""
    # Reset the fork and grant necessary roles
    anvil.reset_fork(268934406)
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.ALPHA_ROLE
    )
    anvil.grant_role(
        ARBITRUM.PILOT.SCHEDULED.ACCESS_MANAGER, ALPHA_WALLET, Roles.WHITELIST_ROLE
    )

    # Setup initial values
    amount = 100_000000  # 100 * 1e6
    user_one = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    user_two = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    # Transfer USDC to user_one
    cheating_transaction_executor.prank(whale_account)
    cheating_usdc.transfer(user_one, amount)
    usdc.approve(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT, 3 * amount)

    plasma_vault.deposit(amount, user_one)

    # Approve transfer from user_one
    plasma_vault.approve(user_one, amount)

    # Transfer shares from user_one to user_two using transferFrom
    cheating_transaction_executor.prank(user_one)
    plasma_vault.transfer_from(user_one, user_two, amount)

    # Verify the transfer
    user_two_vault_balance = plasma_vault.balance_of(user_two)
    assert user_two_vault_balance == amount, "Incorrect balance after transfer"

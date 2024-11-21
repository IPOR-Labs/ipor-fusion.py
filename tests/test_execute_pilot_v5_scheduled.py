import logging
import os

from constants import ARBITRUM, ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.CheatingPlasmaVaultSystemFactory import (
    CheatingPlasmaVaultSystemFactory,
)
from ipor_fusion.IporFusionMarkets import IporFusionMarkets
from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

fork_url = os.getenv("ARBITRUM_PROVIDER_URL")
anvil = AnvilTestContainerStarter(fork_url, 250690377)
anvil.start()


def test_should_deposit():
    """Test depositing USDC into the plasma vault."""
    # Reset the fork and grant necessary roles
    anvil.reset_fork(268934406)

    # setup
    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
    vault = system.plasma_vault()
    usdc = system.usdc()

    cheating_system_factory = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )

    cheating = cheating_system_factory.get(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
    cheating.prank(system.access_manager().owner())
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, system.alpha(), 0)
    cheating.access_manager().grant_role(Roles.WHITELIST_ROLE, system.alpha(), 0)

    amount = 100_000000
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    # Set the account for the transaction executor to the whitelisted account
    cheating.prank(whale_account)
    cheating.usdc().transfer(system.alpha(), amount)
    usdc.approve(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT, amount)

    vault_total_assets_before = vault.total_assets()
    user_vault_balance_before = vault.balance_of(system.alpha())

    # Perform the deposit action
    vault.deposit(amount, system.alpha())

    # Record the USDC balance after the deposit
    vault_total_assets_after = vault.total_assets()
    user_vault_balance_after = vault.balance_of(system.alpha())

    assert (
        100_000000 < vault_total_assets_after - vault_total_assets_before < 100_100000
    )
    assert (
        100_00000000
        < user_vault_balance_after - user_vault_balance_before
        < 100_10000000
    )

    assert vault.total_assets_in_market(IporFusionMarkets.AAVE_V3) == 0


def test_should_mint():
    """Test minting shares in the plasma vault."""
    # Reset the fork and grant necessary roles
    anvil.reset_fork(268934406)

    system_factory = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    system = system_factory.get(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
    vault = system.plasma_vault()

    cheating = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(), private_key=ANVIL_WALLET_PRIVATE_KEY
    ).get(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)

    cheating.prank(system.access_manager().owner())
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, system.alpha(), 0)
    cheating.access_manager().grant_role(Roles.WHITELIST_ROLE, system.alpha(), 0)

    # Setup: Define the whitelisted account
    amount = 110_000000
    shares_amount = 100 * 10 ** vault.decimals()
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    # Transfer USDC to system.alpha()
    cheating.prank(whale_account)
    cheating.usdc().transfer(system.alpha(), amount)
    system.usdc().approve(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT, amount)

    vault_total_assets_before = vault.total_assets()
    user_vault_balance_before = vault.balance_of(system.alpha())
    plasma_vault_underlying_balance_before = system.usdc().balance_of(
        ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT
    )

    # Perform the mint action
    vault.mint(shares_amount, system.alpha())

    # Record the vault state after the minting
    vault_total_assets_after = vault.total_assets()
    user_vault_balance_after = vault.balance_of(system.alpha())
    user_vault_underlying_balance_after = vault.max_withdraw(system.alpha())

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
            - system.usdc().balance_of(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
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
    assert vault.total_assets_in_market(IporFusionMarkets.AAVE_V3) == 0


def test_should_redeem():
    # Reset the fork and grant necessary roles
    anvil.reset_fork(268934406)

    # setup
    system_factory = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    system = system_factory.get(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
    vault = system.plasma_vault()
    withdraw_manager = system.withdraw_manager()
    usdc = system.usdc()

    cheating_system_factory = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    cheating = cheating_system_factory.get(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
    cheating.prank(system.access_manager().owner())
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, system.alpha(), 0)
    cheating.access_manager().grant_role(Roles.WHITELIST_ROLE, system.alpha(), 0)
    cheating.access_manager().grant_role(Roles.ATOMIST_ROLE, system.alpha(), 0)

    amount = 100_000000
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    # Set the account for the transaction executor to the whitelisted account
    cheating.prank(whale_account)
    cheating.usdc().transfer(system.alpha(), amount)
    usdc.approve(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT, amount)

    vault_total_assets_before = vault.total_assets()
    user_vault_balance_before = vault.balance_of(system.alpha())
    erc_20_user_balance_before = usdc.balance_of(system.alpha())

    # Perform the deposit action
    vault.deposit(amount, system.alpha())

    anvil.move_time(7 * 60 * 60)  # 7 hours

    to_redeem = 50 * 10 ** vault.decimals()
    to_withdraw = vault.convert_to_assets(to_redeem)

    withdraw_manager.update_withdraw_window(7 * 60 * 60)  # 7 hours

    withdraw_manager.request(to_withdraw)

    anvil.move_time(60 * 60)  # 1 hour

    withdraw_manager.release_funds()

    vault.redeem(to_redeem, system.alpha(), system.alpha())

    # then
    vault_total_assets_after = vault.total_assets()
    user_vault_balance_after = vault.balance_of(system.alpha())

    # Assert that total assets changed by expected amount (within tolerance)
    assert (
        abs(vault_total_assets_after - (vault_total_assets_before + 50_000000)) < 100000
    ), "vaultTotalAssetsAfter and before"

    # Assert user's vault balance changed by expected amount
    assert (
        abs(
            user_vault_balance_after
            - (user_vault_balance_before + 50 * 10 ** vault.decimals())
        )
        < 10000000
    ), "userVaultBalanceAfter"

    # Assert user's USDC balance changed by expected amount
    assert (
        abs(usdc.balance_of(system.alpha()) - (erc_20_user_balance_before - 50_000000))
        < 100000
    ), "USDC balance of user"

    # Assert no assets in AAVE market
    assert vault.total_assets_in_market(IporFusionMarkets.AAVE_V3) == 0


def test_should_withdraw():
    """Test withdrawing assets from the plasma vault."""
    # Reset the fork and grant necessary roles
    anvil.reset_fork(268934406)

    # setup
    system_factory = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    system = system_factory.get(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
    vault = system.plasma_vault()
    withdraw_manager = system.withdraw_manager()
    usdc = system.usdc()

    cheating_system_factory = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    cheating = cheating_system_factory.get(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
    cheating.prank(system.access_manager().owner())
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, system.alpha(), 0)
    cheating.access_manager().grant_role(Roles.ATOMIST_ROLE, system.alpha(), 0)
    cheating.access_manager().grant_role(Roles.WHITELIST_ROLE, system.alpha(), 0)

    # Setup initial values
    amount = 100_000000  # 100 * 1e6
    shares_amount = 100 * 10 ** vault.decimals()
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    # Transfer USDC to user_one
    cheating.prank(whale_account)
    cheating.usdc().transfer(system.alpha(), amount)
    usdc.approve(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT, amount)

    vault.deposit(amount, system.alpha())

    # Record initial state
    vault_total_assets_before = vault.total_assets()
    user_vault_balance_before = vault.balance_of(system.alpha())

    # Move time forward 7 hours
    anvil.move_time(7 * 60 * 60)

    to_withdraw = vault.max_withdraw(system.alpha())

    # Update withdraw window and request withdrawal
    withdraw_manager.update_withdraw_window(7 * 60 * 60)  # 7 hours

    cheating.prank(system.alpha())
    withdraw_manager.request(to_withdraw)

    # Move time forward 1 hour
    anvil.move_time(60 * 60)

    withdraw_manager.release_funds()

    to_withdraw_second = vault.max_withdraw(system.alpha())

    # Perform withdrawal
    cheating.prank(system.alpha())
    vault.withdraw(to_withdraw_second, system.alpha(), system.alpha())

    # Record final state
    vault_total_assets_after = vault.total_assets()
    user_vault_balance_after = vault.balance_of(system.alpha())

    # Assertions
    assert (
        abs(vault_total_assets_after - (vault_total_assets_before - to_withdraw))
        < 100000
    ), "vaultTotalAssetsAfter and before"
    assert (
        abs(user_vault_balance_before - (user_vault_balance_after + shares_amount))
        < 10000000
    ), "userVaultBalanceAfter"
    assert vault.total_assets_in_market(IporFusionMarkets.AAVE_V3) == 0


def test_should_transfer():
    """Test transferring vault shares between users."""
    # Reset the fork and grant necessary roles
    anvil.reset_fork(268934406)

    # setup
    system_factory = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    system = system_factory.get(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
    vault = system.plasma_vault()
    usdc = system.usdc()

    cheating_system_factory = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    cheating = cheating_system_factory.get(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
    cheating.prank(system.access_manager().owner())
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, system.alpha(), 0)
    cheating.access_manager().grant_role(Roles.WHITELIST_ROLE, system.alpha(), 0)

    # Setup initial values
    amount = 100_000000  # 100 * 1e6
    user_one = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    user_two = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    # Transfer USDC to user_one
    cheating.prank(whale_account)
    cheating.usdc().transfer(user_one, amount)
    usdc.approve(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT, 3 * amount)

    vault.deposit(amount, user_one)

    # Transfer shares from user_one to user_two
    vault.transfer(user_two, amount)

    # Verify the transfer
    user_two_vault_balance = vault.balance_of(user_two)
    assert user_two_vault_balance == amount, "Incorrect balance after transfer"


def test_should_transfer_from():
    """Test transferring vault shares between users using transferFrom."""
    # Reset the fork and grant necessary roles
    anvil.reset_fork(268934406)

    # setup
    system_factory = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    system = system_factory.get(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
    vault = system.plasma_vault()
    usdc = system.usdc()

    cheating_system_factory = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    )
    cheating = cheating_system_factory.get(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT)
    cheating.prank(system.access_manager().owner())
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, system.alpha(), 0)
    cheating.access_manager().grant_role(Roles.WHITELIST_ROLE, system.alpha(), 0)

    # Setup initial values
    amount = 100_000000  # 100 * 1e6
    user_one = "0xf39Fd6e51aad88F6F4ce6aB8827279cffFb92266"
    user_two = "0x70997970C51812dc3A010C7d01b50e0d17dc79C8"
    whale_account = "0x1F7bc4dA1a0c2e49d7eF542F74CD46a3FE592cb1"

    # Transfer USDC to user_one
    cheating.prank(whale_account)
    cheating.usdc().transfer(user_one, amount)
    usdc.approve(ARBITRUM.PILOT.SCHEDULED.PLASMA_VAULT, 3 * amount)

    vault.deposit(amount, user_one)

    # Approve transfer from user_one
    vault.approve(user_one, amount)

    # Transfer shares from user_one to user_two using transferFrom
    cheating.prank(user_one)
    vault.transfer_from(user_one, user_two, amount)

    # Verify the transfer
    user_two_vault_balance = vault.balance_of(user_two)
    assert user_two_vault_balance == amount, "Incorrect balance after transfer"

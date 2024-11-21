import logging
import os

from web3 import Web3

from constants import ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.CheatingPlasmaVaultSystemFactory import (CheatingPlasmaVaultSystemFactory, )
from ipor_fusion.IporFusionMarkets import IporFusionMarkets
from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

fork_url = os.getenv("BASE_PROVIDER_URL")
anvil = AnvilTestContainerStarter(fork_url, 22661340)
anvil.start()

plasma_vault_address = Web3.to_checksum_address("0x45aa96f0b3188D47a1DaFdbefCE1db6B37f58216")

system = PlasmaVaultSystemFactory(provider_url=anvil.get_anvil_http_url(), private_key=ANVIL_WALLET_PRIVATE_KEY, ).get(
    plasma_vault_address)

cheating = CheatingPlasmaVaultSystemFactory(provider_url=anvil.get_anvil_http_url(),
                                            private_key=ANVIL_WALLET_PRIVATE_KEY, ).get(plasma_vault_address)


def test_should_execute():
    """Test depositing USDC into the plasma vault with shares calculation."""
    # Reset the fork and grant necessary roles
    anvil.reset_fork(22661340)

    cheating.prank(system.access_manager().owner())
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, system.alpha(), 0)
    cheating.access_manager().grant_role(Roles.WHITELIST_ROLE, system.alpha(), 0)

    # Setup initial values
    amount = 100_000000  # 100 * 1e6
    shares_amount = 100 * 10 ** system.plasma_vault().decimals()
    whale_account = "0x3304E22DDaa22bCdC5fCa2269b418046aE7b566A"

    # Transfer USDC to user
    cheating.prank(whale_account)
    cheating.usdc().transfer(system.alpha(), amount)
    system.usdc().approve(plasma_vault_address, amount)

    # Record initial state
    vault_total_assets_before = system.plasma_vault().total_assets()
    user_vault_balance_before = system.plasma_vault().balance_of(system.alpha())
    erc20_user_balance_before = system.usdc().balance_of(system.alpha())

    # Perform deposit
    system.plasma_vault().deposit(amount, system.alpha())

    # Record final state
    vault_total_assets_after = system.plasma_vault().total_assets()
    user_vault_balance_after = system.plasma_vault().balance_of(system.alpha())

    # Assertions
    assert (abs(vault_total_assets_after - (
            vault_total_assets_before + amount)) < 5000), "vaultTotalAssetsAfter and before"
    assert (abs(user_vault_balance_after - (
            user_vault_balance_before + shares_amount)) < 300000), "userVaultBalanceAfter"
    assert (abs(amount - system.usdc().balance_of(plasma_vault_address)) < 1000), "USDC balance of plasma vault"
    assert abs(amount - vault_total_assets_after) < 5000, "vaultTotalAssetsAfter"
    assert (abs(system.usdc().balance_of(system.alpha()) - (
                erc20_user_balance_before - amount)) < 5000), "USDC balance of user"
    assert system.plasma_vault().total_assets_in_market(IporFusionMarkets.AAVE_V3) == 0

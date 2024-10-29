import logging

import pytest

from constants import ARBITRUM, ALPHA_WALLET
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.Roles import Roles

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

# Constant for deposit amount
DEPOSIT_AMOUNT = int(2e6)


@pytest.fixture(scope="module", name="plasma_vault")
def plasma_vault_fixture(test_transaction_executor) -> PlasmaVault:
    """Fixture to create a PlasmaVault instance for testing."""
    return PlasmaVault(
        transaction_executor=test_transaction_executor,
        plasma_vault_address=ARBITRUM.PILOT.V5.PLASMA_VAULT,
    )


def test_should_deposit_to_plasma_vault_v5(
    anvil, test_transaction_executor, plasma_vault, usdc
):
    """Test depositing USDC into the plasma vault."""
    # Reset the fork and grant necessary roles
    anvil.reset_fork(266412948)
    anvil.grant_role(ARBITRUM.PILOT.V5.ACCESS_MANAGER, ALPHA_WALLET, Roles.ALPHA_ROLE)

    # Setup: Define the whitelisted account
    whale_account = "0x4E3C666F0c898a9aE1F8aBB188c6A2CC151E17fC"

    # Record the USDC balance before the deposit
    balance_before = usdc.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    # Set the account for the transaction executor to the whitelisted account
    test_transaction_executor.set_account(whale_account)

    # Perform the deposit action
    plasma_vault.deposit(DEPOSIT_AMOUNT, whale_account)

    # Record the USDC balance after the deposit
    balance_after = usdc.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    # Assert that the balance increased by the deposit amount
    assert (
        balance_after - balance_before == DEPOSIT_AMOUNT
    ), f"Expected balance change of {DEPOSIT_AMOUNT}, but got {balance_after - balance_before}"

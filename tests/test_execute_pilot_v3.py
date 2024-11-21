import os

from constants import (
    ARBITRUM,
    ANVIL_WALLET,
    ANVIL_WALLET_PRIVATE_KEY,
)
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.CheatingPlasmaVaultSystemFactory import (
    CheatingPlasmaVaultSystemFactory,
)
from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles

fork_url = os.getenv("ARBITRUM_PROVIDER_URL")
anvil = AnvilTestContainerStarter(fork_url, 250690377)
anvil.start()


def withdraw_from_fluid(system):
    fluid_staking_balance_before = (
        system.fluid_instadapp()
        .staking_pool()
        .balance_of(system.plasma_vault().address())
    )

    unstake_and_withdraw = system.fluid_instadapp().unstake_and_withdraw(
        amount=fluid_staking_balance_before,
    )

    system.plasma_vault().execute(unstake_and_withdraw)


def test_supply_and_withdraw_from_gearbox():
    anvil.reset_fork(250690377)

    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V3.PLASMA_VAULT)

    cheating = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V3.PLASMA_VAULT)

    cheating.prank(system.access_manager().owner())
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)

    withdraw_from_fluid(system)

    # given for supply
    vault_balance_before = system.usdc().balance_of(system.plasma_vault().address())
    gearbox_farm_balance_before = (
        system.gearbox_v3().farm_pool().balance_of(system.plasma_vault().address())
    )

    supply_and_stake = system.gearbox_v3().supply_and_stake(
        amount=vault_balance_before,
    )

    system.plasma_vault().execute(supply_and_stake)

    vault_balance_after = system.usdc().balance_of(system.plasma_vault().address())
    gearbox_farm_balance_after = (
        system.gearbox_v3().farm_pool().balance_of(system.plasma_vault().address())
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert gearbox_farm_balance_before == 0, "gearbox_farm_balance_before == 0"
    assert (
        gearbox_farm_balance_after > 11_000e6
    ), "gearbox_farm_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = system.usdc().balance_of(system.plasma_vault().address())
    gearbox_farm_balance_before = (
        system.gearbox_v3().farm_pool().balance_of(system.plasma_vault().address())
    )

    unstake_and_withdraw = system.gearbox_v3().unstake_and_withdraw(
        amount=gearbox_farm_balance_before,
    )

    system.plasma_vault().execute(unstake_and_withdraw)

    # then after withdraw
    vault_balance_after = system.usdc().balance_of(system.plasma_vault().address())
    gearbox_farm_balance_after = (
        system.gearbox_v3().farm_pool().balance_of(system.plasma_vault().address())
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert (
        gearbox_farm_balance_before > 11_000e6
    ), "gearbox_farm_balance_before > 11_000e6"
    assert gearbox_farm_balance_after == 0, "gearbox_farm_balance_after == 0"


def test_supply_and_withdraw_from_fluid():
    anvil.reset_fork(250690377)

    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V3.PLASMA_VAULT)

    cheating = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V3.PLASMA_VAULT)

    cheating.prank(system.access_manager().owner())
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)

    withdraw_from_fluid(system)

    vault_balance_before = system.usdc().balance_of(system.plasma_vault().address())
    fluid_staking_balance_before = (
        system.fluid_instadapp()
        .staking_pool()
        .balance_of(system.plasma_vault().address())
    )

    supply_and_stake = system.fluid_instadapp().supply_and_stake(
        amount=vault_balance_before,
    )

    system.plasma_vault().execute(supply_and_stake)

    vault_balance_after = system.usdc().balance_of(system.plasma_vault().address())
    fluid_staking_balance_after = (
        system.fluid_instadapp()
        .staking_pool()
        .balance_of(system.plasma_vault().address())
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert fluid_staking_balance_before == 0, "fluid_staking_balance_before == 0"
    assert (
        fluid_staking_balance_after > 11_000e6
    ), "fluid_staking_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = system.usdc().balance_of(system.plasma_vault().address())
    fluid_staking_balance_before = (
        system.fluid_instadapp()
        .staking_pool()
        .balance_of(system.plasma_vault().address())
    )

    unstake_and_withdraw = system.fluid_instadapp().unstake_and_withdraw(
        amount=fluid_staking_balance_before,
    )

    system.plasma_vault().execute(unstake_and_withdraw)

    # then after withdraw
    vault_balance_after = system.usdc().balance_of(system.plasma_vault().address())
    fluid_staking_balance_after = (
        system.fluid_instadapp()
        .staking_pool()
        .balance_of(system.plasma_vault().address())
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert (
        fluid_staking_balance_before > 11_000e6
    ), "fluid_staking_balance_before > 11_000e6"
    assert fluid_staking_balance_after == 0, "fluid_staking_balance_after == 0"


def test_supply_and_withdraw_from_aave_v3():
    anvil.reset_fork(250690377)

    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V3.PLASMA_VAULT)

    cheating = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V3.PLASMA_VAULT)

    cheating.prank(system.access_manager().owner())
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)

    withdraw_from_fluid(system)

    vault_balance_before = system.usdc().balance_of(system.plasma_vault().address())
    protocol_balance_before = (
        system.aave_v3()
        .usdc_a_token_arb_usdc_n()
        .balance_of(system.plasma_vault().address())
    )

    supply = system.aave_v3().supply(
        amount=vault_balance_before,
    )

    system.plasma_vault().execute([supply])

    vault_balance_after = system.usdc().balance_of(system.plasma_vault().address())
    protocol_balance_after = (
        system.aave_v3()
        .usdc_a_token_arb_usdc_n()
        .balance_of(system.plasma_vault().address())
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert protocol_balance_before == 0, "protocol_balance_before == 0"
    assert protocol_balance_after > 11_000e6, "protocol_balance_after > 11_000e6"

    vault_balance_before = system.usdc().balance_of(system.plasma_vault().address())
    protocol_balance_before = (
        system.aave_v3()
        .usdc_a_token_arb_usdc_n()
        .balance_of(system.plasma_vault().address())
    )

    withdraw = system.aave_v3().withdraw(
        amount=protocol_balance_before,
    )

    system.plasma_vault().execute([withdraw])

    # then after withdraw
    vault_balance_after = system.usdc().balance_of(system.plasma_vault().address())
    protocol_balance_after = (
        system.aave_v3()
        .usdc_a_token_arb_usdc_n()
        .balance_of(system.plasma_vault().address())
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert protocol_balance_before > 11_000e6, "protocol_balance_before > 11_000e6"
    assert protocol_balance_after < 1e6, "protocol_balance_after < 1e6"


def test_supply_and_withdraw_from_compound_v3():
    anvil.reset_fork(250690377)

    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V3.PLASMA_VAULT)

    cheating = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V3.PLASMA_VAULT)

    cheating.prank(system.access_manager().owner())
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)

    withdraw_from_fluid(system)

    vault_balance_before = system.usdc().balance_of(system.plasma_vault().address())
    protocol_balance_before = (
        system.compound_v3().usdc_c_token().balance_of(system.plasma_vault().address())
    )

    supply = system.compound_v3().supply(
        amount=vault_balance_before,
    )

    system.plasma_vault().execute([supply])

    vault_balance_after = system.usdc().balance_of(system.plasma_vault().address())
    protocol_balance_after = (
        system.compound_v3().usdc_c_token().balance_of(system.plasma_vault().address())
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert protocol_balance_before == 0, "protocol_balance_before == 0"
    assert protocol_balance_after > 11_000e6, "protocol_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = system.usdc().balance_of(system.plasma_vault().address())
    protocol_balance_before = (
        system.compound_v3().usdc_c_token().balance_of(system.plasma_vault().address())
    )

    withdraw = system.compound_v3().withdraw(
        amount=protocol_balance_before,
    )

    system.plasma_vault().execute([withdraw])

    # then after withdraw
    vault_balance_after = system.usdc().balance_of(system.plasma_vault().address())
    protocol_balance_after = (
        system.compound_v3().usdc_c_token().balance_of(system.plasma_vault().address())
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert protocol_balance_before > 11_000e6, "protocol_balance_before > 11_000e6"
    assert protocol_balance_after < 1e6, "protocol_balance_after < 1e6"

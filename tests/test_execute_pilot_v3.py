import os

from web3 import Web3

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
    usdc = system.erc20("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    vault_balance_before = usdc.balance_of(system.plasma_vault().address())
    gearbox_farm_balance_before = (
        system.gearbox_v3().farm_pool().balance_of(system.plasma_vault().address())
    )

    supply_and_stake = system.gearbox_v3().supply_and_stake(
        amount=vault_balance_before,
    )

    system.plasma_vault().execute(supply_and_stake)

    vault_balance_after = usdc.balance_of(system.plasma_vault().address())
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
    vault_balance_before = usdc.balance_of(system.plasma_vault().address())
    gearbox_farm_balance_before = (
        system.gearbox_v3().farm_pool().balance_of(system.plasma_vault().address())
    )

    unstake_and_withdraw = system.gearbox_v3().unstake_and_withdraw(
        amount=gearbox_farm_balance_before,
    )

    system.plasma_vault().execute(unstake_and_withdraw)

    # then after withdraw
    vault_balance_after = usdc.balance_of(system.plasma_vault().address())
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

    usdc = system.erc20("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    vault_balance_before = usdc.balance_of(system.plasma_vault().address())
    fluid_staking_balance_before = (
        system.fluid_instadapp()
        .staking_pool()
        .balance_of(system.plasma_vault().address())
    )

    supply_and_stake = system.fluid_instadapp().supply_and_stake(
        amount=vault_balance_before,
    )

    system.plasma_vault().execute(supply_and_stake)

    vault_balance_after = usdc.balance_of(system.plasma_vault().address())
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
    vault_balance_before = usdc.balance_of(system.plasma_vault().address())
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
    vault_balance_after = usdc.balance_of(system.plasma_vault().address())
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

    usdc_a_token_arb_usdc_n = system.erc20(
        Web3.to_checksum_address("0x724dc807b04555b71ed48a6896b6f41593b8c637")
    )

    usdc = system.erc20("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    vault_balance_before = usdc.balance_of(system.plasma_vault().address())
    protocol_balance_before = usdc_a_token_arb_usdc_n.balance_of(
        system.plasma_vault().address()
    )

    supply = system.aave_v3().supply(
        asset_address=Web3.to_checksum_address(
            "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
        ),
        amount=vault_balance_before,
        e_mode=300,
    )

    system.plasma_vault().execute([supply])

    vault_balance_after = usdc.balance_of(system.plasma_vault().address())
    protocol_balance_after = usdc_a_token_arb_usdc_n.balance_of(
        system.plasma_vault().address()
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert protocol_balance_before == 0, "protocol_balance_before == 0"
    assert protocol_balance_after > 11_000e6, "protocol_balance_after > 11_000e6"

    vault_balance_before = usdc.balance_of(system.plasma_vault().address())
    protocol_balance_before = usdc_a_token_arb_usdc_n.balance_of(
        system.plasma_vault().address()
    )

    withdraw = system.aave_v3().withdraw(
        asset_address=Web3.to_checksum_address(
            "0xaf88d065e77c8cC2239327C5EDb3A432268e5831"
        ),
        amount=protocol_balance_before,
    )

    system.plasma_vault().execute([withdraw])

    # then after withdraw
    vault_balance_after = usdc.balance_of(system.plasma_vault().address())
    protocol_balance_after = usdc_a_token_arb_usdc_n.balance_of(
        system.plasma_vault().address()
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

    usdc_c_token = system.erc20(
        asset_address=Web3.to_checksum_address(
            "0x9c4ec768c28520b50860ea7a15bd7213a9ff58bf"
        )
    )

    usdc = system.erc20("0xaf88d065e77c8cC2239327C5EDb3A432268e5831")
    vault_balance_before = usdc.balance_of(system.plasma_vault().address())
    protocol_balance_before = usdc_c_token.balance_of(system.plasma_vault().address())

    supply = system.compound_v3().supply(
        asset_address=usdc.address(),
        amount=vault_balance_before,
    )

    system.plasma_vault().execute([supply])

    vault_balance_after = usdc.balance_of(system.plasma_vault().address())
    protocol_balance_after = usdc_c_token.balance_of(system.plasma_vault().address())

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert protocol_balance_before == 0, "protocol_balance_before == 0"
    assert protocol_balance_after > 11_000e6, "protocol_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = usdc.balance_of(system.plasma_vault().address())
    protocol_balance_before = usdc_c_token.balance_of(system.plasma_vault().address())

    withdraw = system.compound_v3().withdraw(
        asset_address=usdc.address(),
        amount=protocol_balance_before,
    )

    system.plasma_vault().execute([withdraw])

    # then after withdraw
    vault_balance_after = usdc.balance_of(system.plasma_vault().address())
    protocol_balance_after = usdc_c_token.balance_of(system.plasma_vault().address())

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert protocol_balance_before > 11_000e6, "protocol_balance_before > 11_000e6"
    assert protocol_balance_after < 1e6, "protocol_balance_after < 1e6"

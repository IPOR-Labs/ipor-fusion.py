import pytest

from constants import (
    ARBITRUM,
    ANVIL_WALLET,
)
from ipor_fusion.MarketId import MarketId
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.Roles import Roles
from ipor_fusion.fuse.AaveV3SupplyFuse import AaveV3SupplyFuse
from ipor_fusion.fuse.CompoundV3SupplyFuse import CompoundV3SupplyFuse
from ipor_fusion.fuse.FluidInstadappSupplyFuse import FluidInstadappSupplyFuse
from ipor_fusion.fuse.GearboxSupplyFuse import GearboxSupplyFuse

fluid_fuse = FluidInstadappSupplyFuse(
    ARBITRUM.FLUID_INSTADAPP.V1.USDC.POOL,
    ARBITRUM.PILOT.V3.FLUID_INSTADAPP_POOL_FUSE,
    ARBITRUM.FLUID_INSTADAPP.V1.USDC.STAKING_POOL,
    ARBITRUM.PILOT.V3.FLUID_INSTADAPP_STAKING_FUSE,
    ARBITRUM.PILOT.V3.FLUID_INSTADAPP_CLAIM_FUSE,
)

gearbox_fuse = GearboxSupplyFuse(
    ARBITRUM.GEARBOX.V3.USDC.POOL,
    ARBITRUM.PILOT.V3.GEARBOX_POOL_FUSE,
    ARBITRUM.GEARBOX.V3.USDC.FARM_POOL,
    ARBITRUM.PILOT.V3.GEARBOX_FARM_FUSE,
    ARBITRUM.PILOT.V3.GEARBOX_CLAIM_FUSE,
)

aave_v3_fuse = AaveV3SupplyFuse(ARBITRUM.PILOT.V3.AAVE_V3_FUSE, ARBITRUM.USDC)

compound_v3_fuse = CompoundV3SupplyFuse(
    ARBITRUM.PILOT.V3.COMPOUND_V3_FUSE, ARBITRUM.USDC
)


@pytest.fixture(scope="module", name="plasma_vault")
def plasma_vault_fixture(test_transaction_executor) -> PlasmaVault:
    return PlasmaVault(
        transaction_executor=test_transaction_executor,
        plasma_vault_address=ARBITRUM.PILOT.V3.PLASMA_VAULT,
    )


def withdraw_from_fluid(plasma_vault, fluid_usdc_staking_pool):
    fluid_staking_balance_before = fluid_usdc_staking_pool.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    unstake_and_withdraw = fluid_fuse.unstake_and_withdraw(
        market_id=MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID, ARBITRUM.FLUID_INSTADAPP.V1.USDC.POOL
        ),
        amount=fluid_staking_balance_before,
    )

    plasma_vault.execute(unstake_and_withdraw)


def test_supply_and_withdraw_from_gearbox(
    anvil, plasma_vault, usdc, fluid_usdc_staking_pool, gearbox_v3_usdc_farm_pool
):
    anvil.reset_fork(250690377)
    anvil.grant_role(ARBITRUM.PILOT.V3.ACCESS_MANAGER, ANVIL_WALLET, Roles.ALPHA_ROLE)
    withdraw_from_fluid(plasma_vault, fluid_usdc_staking_pool)

    # given for supply
    vault_balance_before = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    gearbox_farm_balance_before = gearbox_v3_usdc_farm_pool.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    supply_and_stake = gearbox_fuse.supply_and_stake(
        market_id=MarketId(
            GearboxSupplyFuse.PROTOCOL_ID, ARBITRUM.GEARBOX.V3.USDC.POOL
        ),
        amount=vault_balance_before,
    )

    plasma_vault.execute(supply_and_stake)

    vault_balance_after = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    gearbox_farm_balance_after = gearbox_v3_usdc_farm_pool.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert gearbox_farm_balance_before == 0, "gearbox_farm_balance_before == 0"
    assert (
        gearbox_farm_balance_after > 11_000e6
    ), "gearbox_farm_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    gearbox_farm_balance_before = gearbox_v3_usdc_farm_pool.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    unstake_and_withdraw = gearbox_fuse.unstake_and_withdraw(
        market_id=MarketId(
            GearboxSupplyFuse.PROTOCOL_ID, ARBITRUM.GEARBOX.V3.USDC.POOL
        ),
        amount=gearbox_farm_balance_before,
    )

    plasma_vault.execute(unstake_and_withdraw)

    # then after withdraw
    vault_balance_after = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    gearbox_farm_balance_after = gearbox_v3_usdc_farm_pool.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert (
        gearbox_farm_balance_before > 11_000e6
    ), "gearbox_farm_balance_before > 11_000e6"
    assert gearbox_farm_balance_after == 0, "gearbox_farm_balance_after == 0"


def test_supply_and_withdraw_from_fluid(
    anvil, plasma_vault, usdc, fluid_usdc_staking_pool
):
    anvil.reset_fork(250690377)
    anvil.grant_role(ARBITRUM.PILOT.V3.ACCESS_MANAGER, ANVIL_WALLET, Roles.ALPHA_ROLE)
    withdraw_from_fluid(plasma_vault, fluid_usdc_staking_pool)

    vault_balance_before = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    fluid_staking_balance_before = fluid_usdc_staking_pool.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    supply_and_stake = fluid_fuse.supply_and_stake(
        market_id=MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID, ARBITRUM.FLUID_INSTADAPP.V1.USDC.POOL
        ),
        amount=vault_balance_before,
    )

    plasma_vault.execute(supply_and_stake)

    vault_balance_after = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    fluid_staking_balance_after = fluid_usdc_staking_pool.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert fluid_staking_balance_before == 0, "fluid_staking_balance_before == 0"
    assert (
        fluid_staking_balance_after > 11_000e6
    ), "fluid_staking_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    fluid_staking_balance_before = fluid_usdc_staking_pool.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    unstake_and_withdraw = fluid_fuse.unstake_and_withdraw(
        market_id=MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID, ARBITRUM.FLUID_INSTADAPP.V1.USDC.POOL
        ),
        amount=fluid_staking_balance_before,
    )

    plasma_vault.execute(unstake_and_withdraw)

    # then after withdraw
    vault_balance_after = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    fluid_staking_balance_after = fluid_usdc_staking_pool.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert (
        fluid_staking_balance_before > 11_000e6
    ), "fluid_staking_balance_before > 11_000e6"
    assert fluid_staking_balance_after == 0, "fluid_staking_balance_after == 0"


def test_supply_and_withdraw_from_aave_v3(
    anvil, plasma_vault, usdc, fluid_usdc_staking_pool, aave_v3_usdc_a_token_arb_usdc_n
):
    anvil.reset_fork(250690377)
    anvil.grant_role(ARBITRUM.PILOT.V3.ACCESS_MANAGER, ANVIL_WALLET, Roles.ALPHA_ROLE)
    withdraw_from_fluid(plasma_vault, fluid_usdc_staking_pool)

    vault_balance_before = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    protocol_balance_before = aave_v3_usdc_a_token_arb_usdc_n.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    supply = aave_v3_fuse.supply(
        market_id=MarketId(AaveV3SupplyFuse.PROTOCOL_ID, ARBITRUM.USDC),
        amount=vault_balance_before,
    )

    plasma_vault.execute([supply])

    vault_balance_after = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    protocol_balance_after = aave_v3_usdc_a_token_arb_usdc_n.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert protocol_balance_before == 0, "protocol_balance_before == 0"
    assert protocol_balance_after > 11_000e6, "protocol_balance_after > 11_000e6"

    vault_balance_before = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    protocol_balance_before = aave_v3_usdc_a_token_arb_usdc_n.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    withdraw = aave_v3_fuse.withdraw(
        market_id=MarketId(AaveV3SupplyFuse.PROTOCOL_ID, ARBITRUM.USDC),
        amount=protocol_balance_before,
    )

    plasma_vault.execute([withdraw])

    # then after withdraw
    vault_balance_after = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    protocol_balance_after = aave_v3_usdc_a_token_arb_usdc_n.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert protocol_balance_before > 11_000e6, "protocol_balance_before > 11_000e6"
    assert protocol_balance_after < 1e6, "protocol_balance_after < 1e6"


def test_supply_and_withdraw_from_compound_v3(
    anvil, plasma_vault, usdc, fluid_usdc_staking_pool, compound_v3_usdc_c_token
):
    anvil.reset_fork(250690377)
    anvil.grant_role(ARBITRUM.PILOT.V3.ACCESS_MANAGER, ANVIL_WALLET, Roles.ALPHA_ROLE)
    withdraw_from_fluid(plasma_vault, fluid_usdc_staking_pool)

    vault_balance_before = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    protocol_balance_before = compound_v3_usdc_c_token.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    supply = compound_v3_fuse.supply(
        market_id=MarketId(CompoundV3SupplyFuse.PROTOCOL_ID, ARBITRUM.USDC),
        amount=vault_balance_before,
    )

    plasma_vault.execute([supply])

    vault_balance_after = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    protocol_balance_after = compound_v3_usdc_c_token.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert protocol_balance_before == 0, "protocol_balance_before == 0"
    assert protocol_balance_after > 11_000e6, "protocol_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    protocol_balance_before = compound_v3_usdc_c_token.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    withdraw = compound_v3_fuse.withdraw(
        market_id=MarketId(CompoundV3SupplyFuse.PROTOCOL_ID, ARBITRUM.USDC),
        amount=protocol_balance_before,
    )

    plasma_vault.execute([withdraw])

    # then after withdraw
    vault_balance_after = usdc.balance_of(ARBITRUM.PILOT.V3.PLASMA_VAULT)
    protocol_balance_after = compound_v3_usdc_c_token.balance_of(
        ARBITRUM.PILOT.V3.PLASMA_VAULT
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert protocol_balance_before > 11_000e6, "protocol_balance_before > 11_000e6"
    assert protocol_balance_after < 1e6, "protocol_balance_after < 1e6"

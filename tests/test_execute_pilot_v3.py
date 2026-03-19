import os

import pytest
from eth_typing import BlockNumber
from web3 import Web3

from addresses import ARBITRUM_USDC
from constants import (
    ARBITRUM_PILOT_V3_PLASMA_VAULT,
    ANVIL_WALLET,
    ARBITRUM_AAVE_V3_SUPPLY_FUSE,
    ARBITRUM_V3_COMPOUND_V3_SUPPLY_FUSE,
    ARBITRUM_V3_GEARBOX_V3_FARM_FUSE,
    ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_3,
    ARBITRUM_V3_FLUID_INSTADAPP_STAKING_FUSE,
    ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_5,
)
from ipor_fusion.testing import AnvilTestContainerStarter, ForkedWeb3Context
from ipor_fusion import Roles, PlasmaVault, AccessManager, ERC20
from ipor_fusion.fuses import (
    AaveV3SupplyFuse,
    CompoundV3SupplyFuse,
    GearboxStakeFuse,
    GearboxSupplyFuse,
    FluidInstadappSupplyFuse,
    FluidInstadappStakingFuse,
)
from ipor_fusion.types import MAX_UINT256

fork_url = os.environ["ARBITRUM_PROVIDER_URL"]


@pytest.fixture(scope="module")
def anvil():
    with AnvilTestContainerStarter(fork_url, BlockNumber(250690377)) as a:
        yield a


# Gearbox V3 addresses on Arbitrum
GEARBOX_D_TOKEN = Web3.to_checksum_address("0x890A69EF363C9c7BdD5E36eb95Ceb569F63ACbF6")
GEARBOX_FARMD_TOKEN = Web3.to_checksum_address(
    "0xD0181a36B0566a8645B7eECFf2148adE7Ecf2BE9"
)

# Fluid Instadapp addresses on Arbitrum
FLUID_POOL_TOKEN = Web3.to_checksum_address(
    "0x1A996cb54bb95462040408C06122D45D6Cdb6096"
)
FLUID_STAKING_CONTRACT = Web3.to_checksum_address(
    "0x48f89d731C5e3b5BeE8235162FC2C639Ba62DB7d"
)


def setup_vault(anvil):
    vault_address = ARBITRUM_PILOT_V3_PLASMA_VAULT
    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )
    owner = access_manager.owner()
    forked_ctx.prank(owner)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)
    forked_ctx.prank(ANVIL_WALLET)
    return forked_ctx, plasma_vault, access_manager


def withdraw_from_fluid(forked_ctx, plasma_vault, vault_address):
    fluid_staking_token = ERC20(forked_ctx, FLUID_STAKING_CONTRACT)
    if (fluid_staking_balance := fluid_staking_token.balance_of(vault_address)) > 0:
        staking_fuse = FluidInstadappStakingFuse(
            fuse_address=ARBITRUM_V3_FLUID_INSTADAPP_STAKING_FUSE,
            staking_address=FLUID_STAKING_CONTRACT,
        )
        supply_fuse = FluidInstadappSupplyFuse(
            ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_5,
        )
        plasma_vault.execute(
            [
                staking_fuse.unstake(fluid_staking_balance),
                supply_fuse.withdraw(
                    vault_address=FLUID_POOL_TOKEN, amount=MAX_UINT256
                ),
            ]
        )


def test_supply_and_withdraw_from_gearbox(anvil):
    anvil.reset_fork(250690377)

    forked_ctx, plasma_vault, access_manager = setup_vault(anvil)
    vault_address = plasma_vault.address

    owner = access_manager.owner()
    farmd_substrate = "0x" + "0" * 24 + str(GEARBOX_FARMD_TOKEN)[2:].lower()
    anvil.grant_market_substrates(owner, vault_address, 4, [farmd_substrate])

    withdraw_from_fluid(forked_ctx, plasma_vault, vault_address)

    # given for supply
    usdc = ERC20(forked_ctx, ARBITRUM_USDC)
    gearbox_farm = ERC20(forked_ctx, GEARBOX_FARMD_TOKEN)

    vault_balance_before = usdc.balance_of(vault_address)
    gearbox_farm_balance_before = gearbox_farm.balance_of(vault_address)

    gearbox_supply = GearboxSupplyFuse(ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_3)
    gearbox_stake = GearboxStakeFuse(
        fuse_address=ARBITRUM_V3_GEARBOX_V3_FARM_FUSE,
        staking_address=GEARBOX_FARMD_TOKEN,
    )

    plasma_vault.execute(
        [
            gearbox_supply.supply(
                vault_address=GEARBOX_D_TOKEN, amount=vault_balance_before
            ),
            gearbox_stake.stake(),
        ]
    )

    vault_balance_after = usdc.balance_of(vault_address)
    gearbox_farm_balance_after = gearbox_farm.balance_of(vault_address)

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert gearbox_farm_balance_before == 0, "gearbox_farm_balance_before == 0"
    assert (
        gearbox_farm_balance_after > 11_000e6
    ), "gearbox_farm_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = usdc.balance_of(vault_address)
    gearbox_farm_balance_before = gearbox_farm.balance_of(vault_address)

    plasma_vault.execute(
        [
            gearbox_stake.unstake(gearbox_farm_balance_before),
            gearbox_supply.withdraw(vault_address=GEARBOX_D_TOKEN, amount=MAX_UINT256),
        ]
    )

    # then after withdraw
    vault_balance_after = usdc.balance_of(vault_address)
    gearbox_farm_balance_after = gearbox_farm.balance_of(vault_address)

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert (
        gearbox_farm_balance_before > 11_000e6
    ), "gearbox_farm_balance_before > 11_000e6"
    assert gearbox_farm_balance_after == 0, "gearbox_farm_balance_after == 0"


def test_supply_and_withdraw_from_fluid(anvil):
    anvil.reset_fork(250690377)

    forked_ctx, plasma_vault, _ = setup_vault(anvil)
    vault_address = plasma_vault.address
    withdraw_from_fluid(forked_ctx, plasma_vault, vault_address)

    usdc = ERC20(forked_ctx, ARBITRUM_USDC)
    fluid_staking = ERC20(forked_ctx, FLUID_STAKING_CONTRACT)

    vault_balance_before = usdc.balance_of(vault_address)
    fluid_staking_balance_before = fluid_staking.balance_of(vault_address)

    supply_fuse = FluidInstadappSupplyFuse(
        ARBITRUM_V3_ERC4626_SUPPLY_FUSE_MARKET_ID_5,
    )
    staking_fuse = FluidInstadappStakingFuse(
        fuse_address=ARBITRUM_V3_FLUID_INSTADAPP_STAKING_FUSE,
        staking_address=FLUID_STAKING_CONTRACT,
    )
    plasma_vault.execute(
        [
            supply_fuse.supply(
                vault_address=FLUID_POOL_TOKEN, amount=vault_balance_before
            ),
            staking_fuse.stake(),
        ]
    )

    vault_balance_after = usdc.balance_of(vault_address)
    fluid_staking_balance_after = fluid_staking.balance_of(vault_address)

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert fluid_staking_balance_before == 0, "fluid_staking_balance_before == 0"
    assert (
        fluid_staking_balance_after > 11_000e6
    ), "fluid_staking_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = usdc.balance_of(vault_address)
    fluid_staking_balance_before = fluid_staking.balance_of(vault_address)

    plasma_vault.execute(
        [
            staking_fuse.unstake(fluid_staking_balance_before),
            supply_fuse.withdraw(vault_address=FLUID_POOL_TOKEN, amount=MAX_UINT256),
        ]
    )

    # then after withdraw
    vault_balance_after = usdc.balance_of(vault_address)
    fluid_staking_balance_after = fluid_staking.balance_of(vault_address)

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert (
        fluid_staking_balance_before > 11_000e6
    ), "fluid_staking_balance_before > 11_000e6"
    assert fluid_staking_balance_after == 0, "fluid_staking_balance_after == 0"


def test_supply_and_withdraw_from_aave_v3(anvil):
    anvil.reset_fork(250690377)

    forked_ctx, plasma_vault, _ = setup_vault(anvil)
    vault_address = plasma_vault.address
    withdraw_from_fluid(forked_ctx, plasma_vault, vault_address)

    usdc_a_token_arb_usdc_n = ERC20(
        forked_ctx,
        Web3.to_checksum_address("0x724dc807b04555b71ed48a6896b6f41593b8c637"),
    )

    usdc = ERC20(forked_ctx, ARBITRUM_USDC)
    vault_balance_before = usdc.balance_of(vault_address)
    protocol_balance_before = usdc_a_token_arb_usdc_n.balance_of(vault_address)

    aave = AaveV3SupplyFuse(ARBITRUM_AAVE_V3_SUPPLY_FUSE)
    supply = aave.supply(
        asset=ARBITRUM_USDC,
        amount=vault_balance_before,
        e_mode=300,
    )

    plasma_vault.execute([supply])

    vault_balance_after = usdc.balance_of(vault_address)
    protocol_balance_after = usdc_a_token_arb_usdc_n.balance_of(vault_address)

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert protocol_balance_before == 0, "protocol_balance_before == 0"
    assert protocol_balance_after > 11_000e6, "protocol_balance_after > 11_000e6"

    vault_balance_before = usdc.balance_of(vault_address)
    protocol_balance_before = usdc_a_token_arb_usdc_n.balance_of(vault_address)

    withdraw = aave.withdraw(
        asset=ARBITRUM_USDC,
        amount=protocol_balance_before,
    )

    plasma_vault.execute([withdraw])

    # then after withdraw
    vault_balance_after = usdc.balance_of(vault_address)
    protocol_balance_after = usdc_a_token_arb_usdc_n.balance_of(vault_address)

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert protocol_balance_before > 11_000e6, "protocol_balance_before > 11_000e6"
    assert protocol_balance_after < 1e6, "protocol_balance_after < 1e6"


def test_supply_and_withdraw_from_compound_v3(anvil):
    anvil.reset_fork(250690377)

    forked_ctx, plasma_vault, _ = setup_vault(anvil)
    vault_address = plasma_vault.address
    withdraw_from_fluid(forked_ctx, plasma_vault, vault_address)

    usdc_c_token = ERC20(
        forked_ctx,
        Web3.to_checksum_address("0x9c4ec768c28520b50860ea7a15bd7213a9ff58bf"),
    )

    usdc = ERC20(forked_ctx, ARBITRUM_USDC)
    vault_balance_before = usdc.balance_of(vault_address)
    protocol_balance_before = usdc_c_token.balance_of(vault_address)

    compound = CompoundV3SupplyFuse(ARBITRUM_V3_COMPOUND_V3_SUPPLY_FUSE)
    supply = compound.supply(
        asset=ARBITRUM_USDC,
        amount=vault_balance_before,
    )

    plasma_vault.execute([supply])

    vault_balance_after = usdc.balance_of(vault_address)
    protocol_balance_after = usdc_c_token.balance_of(vault_address)

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert protocol_balance_before == 0, "protocol_balance_before == 0"
    assert protocol_balance_after > 11_000e6, "protocol_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = usdc.balance_of(vault_address)
    protocol_balance_before = usdc_c_token.balance_of(vault_address)

    withdraw = compound.withdraw(
        asset=ARBITRUM_USDC,
        amount=protocol_balance_before,
    )

    plasma_vault.execute([withdraw])

    # then after withdraw
    vault_balance_after = usdc.balance_of(vault_address)
    protocol_balance_after = usdc_c_token.balance_of(vault_address)

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert protocol_balance_before > 11_000e6, "protocol_balance_before > 11_000e6"
    assert protocol_balance_after < 1e6, "protocol_balance_after < 1e6"

import logging
import os

import pytest

from commons import read_token_balance, execute_transaction
from constants import (
    ANVIL_WALLET,
    ARBITRUM,
)
from ipor_fusion_sdk.MarketId import MarketId
from ipor_fusion_sdk.VaultExecuteCallFactory import VaultExecuteCallFactory
from ipor_fusion_sdk.fuse.AaveV3SupplyFuse import AaveV3SupplyFuse
from ipor_fusion_sdk.fuse.CompoundV3SupplyFuse import CompoundV3SupplyFuse
from ipor_fusion_sdk.fuse.FluidInstadappSupplyFuse import FluidInstadappSupplyFuse
from ipor_fusion_sdk.fuse.GearboxSupplyFuse import GearboxSupplyFuse

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

ARBITRUM_PROVIDER_URL = "ARBITRUM_PROVIDER_URL"
FORK_URL = os.getenv(ARBITRUM_PROVIDER_URL)
if not FORK_URL:
    raise ValueError("Environment variable ARBITRUM_PROVIDER_URL must be set")

SET_ANVIL_WALLET_AS_PILOT_V3_ALPHA_COMMAND = [
    "cast",
    "send",
    "--unlocked",
    "--from",
    "0x4E3C666F0c898a9aE1F8aBB188c6A2CC151E17fC",
    ARBITRUM.PILOT.V3.ACCESS_MANAGER,
    "grantRole(uint64,address,uint32)()",
    "200",
    ANVIL_WALLET,
    "0",
]

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


@pytest.fixture(scope="module", name="vault_execute_call_factory")
def vault_execute_call_factory_fixture() -> VaultExecuteCallFactory:
    return VaultExecuteCallFactory()


@pytest.fixture(name="setup", autouse=True)
def setup_fixture(web3, account, anvil, vault_execute_call_factory):
    anvil.reset_fork(250690377)
    anvil.execute_in_container(SET_ANVIL_WALLET_AS_PILOT_V3_ALPHA_COMMAND)
    withdraw_from_fluid(web3, account, vault_execute_call_factory)
    yield


def withdraw_from_fluid(web3, account, vault_execute_call_factory):
    fluid_staking_balance_before = read_token_balance(
        web3,
        ARBITRUM.PILOT.V3.PLASMA_VAULT,
        ARBITRUM.FLUID_INSTADAPP.V1.USDC.STAKING_POOL,
    )

    actions = fluid_fuse.unstake_and_withdraw(
        market_id=MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID, ARBITRUM.FLUID_INSTADAPP.V1.USDC.POOL
        ),
        amount=fluid_staking_balance_before,
    )

    function_call = vault_execute_call_factory.create_execute_call_from_actions(actions)

    execute_transaction(web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, function_call, account)


def test_supply_and_withdraw_from_gearbox(web3, account, vault_execute_call_factory):
    # given for supply
    vault_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    gearbox_farm_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.GEARBOX.V3.USDC.FARM_POOL
    )

    actions = gearbox_fuse.supply_and_stake(
        market_id=MarketId(
            GearboxSupplyFuse.PROTOCOL_ID, ARBITRUM.GEARBOX.V3.USDC.POOL
        ),
        amount=vault_balance_before,
    )

    # when supply
    execute_transaction(
        web3,
        ARBITRUM.PILOT.V3.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_actions(actions),
        account,
    )

    vault_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    gearbox_farm_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.GEARBOX.V3.USDC.FARM_POOL
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert gearbox_farm_balance_before == 0, "gearbox_farm_balance_before == 0"
    assert (
        gearbox_farm_balance_after > 11_000e6
    ), "gearbox_farm_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    gearbox_farm_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.GEARBOX.V3.USDC.FARM_POOL
    )

    actions = gearbox_fuse.unstake_and_withdraw(
        market_id=MarketId(
            GearboxSupplyFuse.PROTOCOL_ID, ARBITRUM.GEARBOX.V3.USDC.POOL
        ),
        amount=gearbox_farm_balance_before,
    )

    # when withdraw
    execute_transaction(
        web3,
        ARBITRUM.PILOT.V3.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_actions(actions),
        account,
    )

    # then after withdraw
    vault_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    gearbox_farm_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.GEARBOX.V3.USDC.FARM_POOL
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert (
        gearbox_farm_balance_before > 11_000e6
    ), "gearbox_farm_balance_before > 11_000e6"
    assert gearbox_farm_balance_after == 0, "gearbox_farm_balance_after == 0"


def test_supply_and_withdraw_from_fluid(web3, account, vault_execute_call_factory):
    # given for supply
    vault_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    fluid_staking_balance_before = read_token_balance(
        web3,
        ARBITRUM.PILOT.V3.PLASMA_VAULT,
        ARBITRUM.FLUID_INSTADAPP.V1.USDC.STAKING_POOL,
    )

    enter_actions = fluid_fuse.supply_and_stake(
        market_id=MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID, ARBITRUM.FLUID_INSTADAPP.V1.USDC.POOL
        ),
        amount=vault_balance_before,
    )

    # when supply
    execute_transaction(
        web3,
        ARBITRUM.PILOT.V3.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_actions(enter_actions),
        account,
    )

    vault_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    fluid_staking_balance_after = read_token_balance(
        web3,
        ARBITRUM.PILOT.V3.PLASMA_VAULT,
        ARBITRUM.FLUID_INSTADAPP.V1.USDC.STAKING_POOL,
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert fluid_staking_balance_before == 0, "fluid_staking_balance_before == 0"
    assert (
        fluid_staking_balance_after > 11_000e6
    ), "fluid_staking_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    fluid_staking_balance_before = read_token_balance(
        web3,
        ARBITRUM.PILOT.V3.PLASMA_VAULT,
        ARBITRUM.FLUID_INSTADAPP.V1.USDC.STAKING_POOL,
    )

    actions = fluid_fuse.unstake_and_withdraw(
        market_id=MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID, ARBITRUM.FLUID_INSTADAPP.V1.USDC.POOL
        ),
        amount=fluid_staking_balance_before,
    )

    function = vault_execute_call_factory.create_execute_call_from_actions(actions)

    # when withdraw
    execute_transaction(web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, function, account)

    # then after withdraw
    vault_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    fluid_staking_balance_after = read_token_balance(
        web3,
        ARBITRUM.PILOT.V3.PLASMA_VAULT,
        ARBITRUM.FLUID_INSTADAPP.V1.USDC.STAKING_POOL,
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert (
        fluid_staking_balance_before > 11_000e6
    ), "fluid_staking_balance_before > 11_000e6"
    assert fluid_staking_balance_after == 0, "fluid_staking_balance_after == 0"


def test_supply_and_withdraw_from_aave_v3(web3, account, vault_execute_call_factory):
    # given for supply
    vault_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    protocol_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.AAVE.V3.USDC.A_TOKEN_ARB_USDC_N
    )

    action = aave_v3_fuse.supply(
        market_id=MarketId(AaveV3SupplyFuse.PROTOCOL_ID, ARBITRUM.USDC),
        amount=vault_balance_before,
    )

    # when supply
    execute_transaction(
        web3,
        ARBITRUM.PILOT.V3.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(action),
        account,
    )

    vault_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    protocol_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.AAVE.V3.USDC.A_TOKEN_ARB_USDC_N
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert protocol_balance_before == 0, "protocol_balance_before == 0"
    assert protocol_balance_after > 11_000e6, "protocol_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    protocol_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.AAVE.V3.USDC.A_TOKEN_ARB_USDC_N
    )

    action = aave_v3_fuse.withdraw(
        market_id=MarketId(AaveV3SupplyFuse.PROTOCOL_ID, ARBITRUM.USDC),
        amount=protocol_balance_before,
    )

    # when withdraw
    execute_transaction(
        web3,
        ARBITRUM.PILOT.V3.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_action(action),
        account,
    )

    # then after withdraw
    vault_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    protocol_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.AAVE.V3.USDC.A_TOKEN_ARB_USDC_N
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert protocol_balance_before > 11_000e6, "protocol_balance_before > 11_000e6"
    assert protocol_balance_after < 1e6, "protocol_balance_after < 1e6"


def test_supply_and_withdraw_from_compound_v3(
    web3, account, vault_execute_call_factory
):
    # given for supply
    vault_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    protocol_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.COMPOUND.V3.USDC.C_TOKEN
    )

    actions = compound_v3_fuse.supply(
        market_id=MarketId(CompoundV3SupplyFuse.PROTOCOL_ID, ARBITRUM.USDC),
        amount=vault_balance_before,
    )

    # when supply
    execute_transaction(
        web3,
        ARBITRUM.PILOT.V3.PLASMA_VAULT,
        vault_execute_call_factory.create_execute_call_from_actions(actions),
        account,
    )

    vault_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    protocol_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.COMPOUND.V3.USDC.C_TOKEN
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert protocol_balance_before == 0, "protocol_balance_before == 0"
    assert protocol_balance_after > 11_000e6, "protocol_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    protocol_balance_before = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.COMPOUND.V3.USDC.C_TOKEN
    )

    action = compound_v3_fuse.withdraw(
        market_id=MarketId(CompoundV3SupplyFuse.PROTOCOL_ID, ARBITRUM.USDC),
        amount=protocol_balance_before,
    )

    function = vault_execute_call_factory.create_execute_call_from_action(action)

    # when withdraw
    execute_transaction(web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, function, account)

    # then after withdraw
    vault_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.USDC
    )
    protocol_balance_after = read_token_balance(
        web3, ARBITRUM.PILOT.V3.PLASMA_VAULT, ARBITRUM.COMPOUND.V3.USDC.C_TOKEN
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert protocol_balance_before > 11_000e6, "protocol_balance_before > 11_000e6"
    assert protocol_balance_after < 1e6, "protocol_balance_after < 1e6"

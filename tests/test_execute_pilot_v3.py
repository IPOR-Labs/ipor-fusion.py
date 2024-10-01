import logging
import os

import pytest

from commons import read_token_balance, execute_transaction
from constants import (
    FLUID_INSTADAPP_STAKING_FUSE_ADDRESS,
    FLUID_INSTADAPP_USDC_POOL_ADDRESS,
    FLUID_INSTADAPP_POOL_FUSE_ADDRESS,
    FLUID_INSTADAPP_STAKING_ADDRESS,
    FLUID_INSTADAPP_CLAIM_FUSE_ADDRESS,
    GEARBOX_USDC_POOL_ADDRESS,
    GEARBOX_POOL_FUSE_ADDRESS,
    GEARBOX_FARM_USDC_POOL_ADDRESS,
    GEARBOX_FARM_FUSE_ADDRESS,
    GEARBOX_CLAIM_FUSE_ADDRESS,
    PLASMA_VAULT_V3,
    USDC,
    FLUID_USDC_STAKING_POOL,
    AAVE_A_TOKEN_ARB_USDC_N,
    AAVEV_V3_FUSE_ADDRESS,
    COMPOUND_V3_C_TOKEN_ADDRESS,
    COMPOUND_V3_FUSE_ADDRESS,
    FORK_BLOCK_NUMBER,
    IPOR_FUSION_V3_ACCESS_MANAGER_USDC_ADDRESS,
    ANVIL_WALLET,
)
from ipor_fusion_sdk.VaultExecuteCallFactory import VaultExecuteCallFactory
from ipor_fusion_sdk.fuse.AaveV3SupplyFuse import AaveV3SupplyFuse
from ipor_fusion_sdk.fuse.CompoundV3SupplyFuse import CompoundV3SupplyFuse
from ipor_fusion_sdk.fuse.FluidInstadappSupplyFuse import FluidInstadappSupplyFuse
from ipor_fusion_sdk.fuse.GearboxSupplyFuse import GearboxSupplyFuse
from ipor_fusion_sdk.MarketId import MarketId

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
    IPOR_FUSION_V3_ACCESS_MANAGER_USDC_ADDRESS,
    "grantRole(uint64,address,uint32)()",
    "200",
    ANVIL_WALLET,
    "0",
]

fluid_fuse = FluidInstadappSupplyFuse(
    FLUID_INSTADAPP_USDC_POOL_ADDRESS,
    FLUID_INSTADAPP_POOL_FUSE_ADDRESS,
    FLUID_INSTADAPP_STAKING_ADDRESS,
    FLUID_INSTADAPP_STAKING_FUSE_ADDRESS,
    FLUID_INSTADAPP_CLAIM_FUSE_ADDRESS,
)

gearbox_fuse = GearboxSupplyFuse(
    GEARBOX_USDC_POOL_ADDRESS,
    GEARBOX_POOL_FUSE_ADDRESS,
    GEARBOX_FARM_USDC_POOL_ADDRESS,
    GEARBOX_FARM_FUSE_ADDRESS,
    GEARBOX_CLAIM_FUSE_ADDRESS,
)

aave_v3_fuse = AaveV3SupplyFuse(AAVEV_V3_FUSE_ADDRESS, USDC)

compound_v3_fuse = CompoundV3SupplyFuse(COMPOUND_V3_FUSE_ADDRESS, USDC)


@pytest.fixture(scope="module", name="vault_execute_call_factory")
def vault_execute_call_factory_fixture() -> VaultExecuteCallFactory:
    return VaultExecuteCallFactory()


@pytest.fixture(name="setup", autouse=True)
def setup_fixture(web3, account, anvil, vault_execute_call_factory):
    anvil.reset_fork(FORK_BLOCK_NUMBER)
    anvil.execute_in_container(SET_ANVIL_WALLET_AS_PILOT_V3_ALPHA_COMMAND)
    withdraw_from_fluid(web3, account, vault_execute_call_factory)
    yield


def withdraw_from_fluid(web3, account, vault_execute_call_factory):
    fluid_staking_balance_before = read_token_balance(
        web3, PLASMA_VAULT_V3, FLUID_INSTADAPP_STAKING_ADDRESS
    )

    actions = fluid_fuse.unstake_and_withdraw(
        market_id=MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID, FLUID_INSTADAPP_USDC_POOL_ADDRESS
        ),
        amount=fluid_staking_balance_before,
    )

    function_call = vault_execute_call_factory.create_execute_call_from_actions(actions)

    execute_transaction(web3, PLASMA_VAULT_V3, function_call, account)


def test_supply_and_withdraw_from_gearbox(web3, account, vault_execute_call_factory):
    # given for supply
    vault_balance_before = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    gearbox_farm_balance_before = read_token_balance(
        web3, PLASMA_VAULT_V3, GEARBOX_FARM_USDC_POOL_ADDRESS
    )

    actions = gearbox_fuse.supply_and_stake(
        market_id=MarketId(GearboxSupplyFuse.PROTOCOL_ID, GEARBOX_USDC_POOL_ADDRESS),
        amount=vault_balance_before,
    )

    # when supply
    execute_transaction(
        web3,
        PLASMA_VAULT_V3,
        vault_execute_call_factory.create_execute_call_from_actions(actions),
        account,
    )

    vault_balance_after = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    gearbox_farm_balance_after = read_token_balance(
        web3, PLASMA_VAULT_V3, GEARBOX_FARM_USDC_POOL_ADDRESS
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert gearbox_farm_balance_before == 0, "gearbox_farm_balance_before == 0"
    assert (
        gearbox_farm_balance_after > 11_000e6
    ), "gearbox_farm_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    gearbox_farm_balance_before = read_token_balance(
        web3, PLASMA_VAULT_V3, GEARBOX_FARM_USDC_POOL_ADDRESS
    )

    actions = gearbox_fuse.unstake_and_withdraw(
        market_id=MarketId(GearboxSupplyFuse.PROTOCOL_ID, GEARBOX_USDC_POOL_ADDRESS),
        amount=gearbox_farm_balance_before,
    )

    # when withdraw
    execute_transaction(
        web3,
        PLASMA_VAULT_V3,
        vault_execute_call_factory.create_execute_call_from_actions(actions),
        account,
    )

    # then after withdraw
    vault_balance_after = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    gearbox_farm_balance_after = read_token_balance(
        web3, PLASMA_VAULT_V3, GEARBOX_FARM_USDC_POOL_ADDRESS
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert (
        gearbox_farm_balance_before > 11_000e6
    ), "gearbox_farm_balance_before > 11_000e6"
    assert gearbox_farm_balance_after == 0, "gearbox_farm_balance_after == 0"


def test_supply_and_withdraw_from_fluid(web3, account, vault_execute_call_factory):
    # given for supply
    vault_balance_before = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    fluid_staking_balance_before = read_token_balance(
        web3, PLASMA_VAULT_V3, FLUID_USDC_STAKING_POOL
    )

    enter_actions = fluid_fuse.supply_and_stake(
        market_id=MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID, FLUID_INSTADAPP_USDC_POOL_ADDRESS
        ),
        amount=vault_balance_before,
    )

    # when supply
    execute_transaction(
        web3,
        PLASMA_VAULT_V3,
        vault_execute_call_factory.create_execute_call_from_actions(enter_actions),
        account,
    )

    vault_balance_after = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    fluid_staking_balance_after = read_token_balance(
        web3, PLASMA_VAULT_V3, FLUID_USDC_STAKING_POOL
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert fluid_staking_balance_before == 0, "fluid_staking_balance_before == 0"
    assert (
        fluid_staking_balance_after > 11_000e6
    ), "fluid_staking_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    fluid_staking_balance_before = read_token_balance(
        web3, PLASMA_VAULT_V3, FLUID_USDC_STAKING_POOL
    )

    actions = fluid_fuse.unstake_and_withdraw(
        market_id=MarketId(
            FluidInstadappSupplyFuse.PROTOCOL_ID, FLUID_INSTADAPP_USDC_POOL_ADDRESS
        ),
        amount=fluid_staking_balance_before,
    )

    function = vault_execute_call_factory.create_execute_call_from_actions(actions)

    # when withdraw
    execute_transaction(web3, PLASMA_VAULT_V3, function, account)

    # then after withdraw
    vault_balance_after = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    fluid_staking_balance_after = read_token_balance(
        web3, PLASMA_VAULT_V3, FLUID_USDC_STAKING_POOL
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert (
        fluid_staking_balance_before > 11_000e6
    ), "fluid_staking_balance_before > 11_000e6"
    assert fluid_staking_balance_after == 0, "fluid_staking_balance_after == 0"


def test_supply_and_withdraw_from_aave_v3(web3, account, vault_execute_call_factory):
    # given for supply
    vault_balance_before = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    protocol_balance_before = read_token_balance(
        web3, PLASMA_VAULT_V3, AAVE_A_TOKEN_ARB_USDC_N
    )

    action = aave_v3_fuse.supply(
        market_id=MarketId(AaveV3SupplyFuse.PROTOCOL_ID, USDC),
        amount=vault_balance_before,
    )

    # when supply
    execute_transaction(
        web3,
        PLASMA_VAULT_V3,
        vault_execute_call_factory.create_execute_call_from_action(action),
        account,
    )

    vault_balance_after = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    protocol_balance_after = read_token_balance(
        web3, PLASMA_VAULT_V3, AAVE_A_TOKEN_ARB_USDC_N
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert protocol_balance_before == 0, "protocol_balance_before == 0"
    assert protocol_balance_after > 11_000e6, "protocol_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    protocol_balance_before = read_token_balance(
        web3, PLASMA_VAULT_V3, AAVE_A_TOKEN_ARB_USDC_N
    )

    action = aave_v3_fuse.withdraw(
        market_id=MarketId(AaveV3SupplyFuse.PROTOCOL_ID, USDC),
        amount=protocol_balance_before,
    )

    # when withdraw
    execute_transaction(
        web3,
        PLASMA_VAULT_V3,
        vault_execute_call_factory.create_execute_call_from_action(action),
        account,
    )

    # then after withdraw
    vault_balance_after = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    protocol_balance_after = read_token_balance(
        web3, PLASMA_VAULT_V3, AAVE_A_TOKEN_ARB_USDC_N
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert protocol_balance_before > 11_000e6, "protocol_balance_before > 11_000e6"
    assert protocol_balance_after < 1e6, "protocol_balance_after < 1e6"


def test_supply_and_withdraw_from_compound_v3(
    web3, account, vault_execute_call_factory
):
    # given for supply
    vault_balance_before = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    protocol_balance_before = read_token_balance(
        web3, PLASMA_VAULT_V3, COMPOUND_V3_C_TOKEN_ADDRESS
    )

    actions = compound_v3_fuse.supply(
        market_id=MarketId(CompoundV3SupplyFuse.PROTOCOL_ID, USDC),
        amount=vault_balance_before,
    )

    # when supply
    execute_transaction(
        web3,
        PLASMA_VAULT_V3,
        vault_execute_call_factory.create_execute_call_from_actions(actions),
        account,
    )

    vault_balance_after = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    protocol_balance_after = read_token_balance(
        web3, PLASMA_VAULT_V3, COMPOUND_V3_C_TOKEN_ADDRESS
    )

    assert vault_balance_before > 11_000e6, "vault_balance_before > 11_000e6"
    assert vault_balance_after == 0, "vault_balance_after == 0"
    assert protocol_balance_before == 0, "protocol_balance_before == 0"
    assert protocol_balance_after > 11_000e6, "protocol_balance_after > 11_000e6"

    # given for withdraw
    vault_balance_before = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    protocol_balance_before = read_token_balance(
        web3, PLASMA_VAULT_V3, COMPOUND_V3_C_TOKEN_ADDRESS
    )

    action = compound_v3_fuse.withdraw(
        market_id=MarketId(CompoundV3SupplyFuse.PROTOCOL_ID, USDC),
        amount=protocol_balance_before,
    )

    function = vault_execute_call_factory.create_execute_call_from_action(action)

    # when withdraw
    execute_transaction(web3, PLASMA_VAULT_V3, function, account)

    # then after withdraw
    vault_balance_after = read_token_balance(web3, PLASMA_VAULT_V3, USDC)
    protocol_balance_after = read_token_balance(
        web3, PLASMA_VAULT_V3, COMPOUND_V3_C_TOKEN_ADDRESS
    )

    assert vault_balance_before == 0, "vault_balance_before == 0"
    assert vault_balance_after > 11_000e6, "vault_balance_after > 11_000e6"
    assert protocol_balance_before > 11_000e6, "protocol_balance_before > 11_000e6"
    assert protocol_balance_after < 1e6, "protocol_balance_after < 1e6"

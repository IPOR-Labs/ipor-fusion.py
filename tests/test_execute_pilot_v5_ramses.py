import logging
import os
import time

import pytest

from constants import ARBITRUM, ALPHA_WALLET, DAY, MONTH
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.RewardsClaimManager import RewardsClaimManager
from ipor_fusion.Roles import Roles
from ipor_fusion.UniswapV3UniversalRouter import UniswapV3UniversalRouter
from ipor_fusion.fuse.RamsesClaimFuse import RamsesClaimFuse
from ipor_fusion.fuse.RamsesV2CollectFuse import RamsesV2CollectFuse
from ipor_fusion.fuse.RamsesV2ModifyPositionFuse import RamsesV2ModifyPositionFuse
from ipor_fusion.fuse.RamsesV2NewPositionFuse import RamsesV2NewPositionFuse
from ipor_fusion.fuse.UniswapV3SwapFuse import UniswapV3SwapFuse

logging.basicConfig(level=logging.DEBUG)
log = logging.getLogger(__name__)

ARBITRUM_PROVIDER_URL = "ARBITRUM_PROVIDER_URL"
FORK_URL = os.getenv(ARBITRUM_PROVIDER_URL)
if not FORK_URL:
    raise ValueError("Environment variable ARBITRUM_PROVIDER_URL must be set")

ramses_v2_new_position_fuse = RamsesV2NewPositionFuse(
    ARBITRUM.PILOT.V5.RAMSES_V2_NEW_POSITION_FUSE
)
ramses_v2_modify_position_fuse = RamsesV2ModifyPositionFuse(
    ARBITRUM.PILOT.V5.RAMSES_V2_MODIFY_POSITION_FUSE
)
ramses_v2_collect_fuse = RamsesV2CollectFuse(ARBITRUM.PILOT.V5.RAMSES_V2_COLLECT_FUSE)
uniswap_v3_swap_fuse = UniswapV3SwapFuse(ARBITRUM.PILOT.V5.UNISWAP_V3_SWAP_FUSE)
ramses_claim_fuse = RamsesClaimFuse(ARBITRUM.PILOT.V5.RAMSES_V2_CLAIM_FUSE)


@pytest.fixture(scope="module", name="rewards_claim_manager")
def rewards_claim_manager_fixture(transaction_executor) -> RewardsClaimManager:
    return RewardsClaimManager(
        transaction_executor=transaction_executor,
        rewards_claim_manager=ARBITRUM.PILOT.V5.REWARDS_CLAIM_MANAGER,
    )


@pytest.fixture(scope="module", name="plasma_vault")
def plasma_vault_fixture(transaction_executor) -> PlasmaVault:
    return PlasmaVault(
        transaction_executor=transaction_executor,
        plasma_vault_address=ARBITRUM.PILOT.V5.PLASMA_VAULT,
    )


@pytest.fixture(scope="module", name="uniswap_v3_universal_router")
def uniswap_v3_universal_router_fixture(
    transaction_executor,
) -> UniswapV3UniversalRouter:
    return UniswapV3UniversalRouter(
        transaction_executor=transaction_executor,
        universal_router_address=ARBITRUM.UNISWAP.V3.UNIVERSAL_ROUTER,
    )


@pytest.fixture(name="setup", autouse=True)
def setup_fixture(anvil):
    anvil.reset_fork(261946538)  # 261946538 - 1002 USDC on pilot V5
    anvil.grant_role(ARBITRUM.PILOT.V5.ACCESS_MANAGER, ALPHA_WALLET, Roles.ALPHA_ROLE)
    yield


def test_should_open_new_position_ramses_v2(plasma_vault, usdc, usdt):
    # given
    swap = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=100,
        token_in_amount=int(500e6),
        min_out_amount=0,
    )

    plasma_vault.execute([swap])

    vault_usdc_balance_after_swap = usdc.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)
    vault_usdt_balance_after_swap = usdt.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    new_position = ramses_v2_new_position_fuse.new_position(
        token0=ARBITRUM.USDC,
        token1=ARBITRUM.USDT,
        fee=50,
        tick_lower=-100,
        tick_upper=100,
        amount0_desired=int(499e6),
        amount1_desired=int(499e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
        ve_ram_token_id=0,
    )

    # when
    plasma_vault.execute([new_position])

    # then
    vault_usdc_balance_after_new_position = usdc.balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )
    vault_usdt_balance_after_new_position = usdt.balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )

    assert (
        vault_usdc_balance_after_new_position - vault_usdc_balance_after_swap
        == -int(456_205368)
    ), ("new_position_usdc_change == -int(456_205368)")
    assert (
        vault_usdt_balance_after_new_position - vault_usdt_balance_after_swap
        == -int(499_000000)
    ), ("new_position_usdt_change == -int(499000000)")


def test_should_collect_all_after_decrease_liquidity(anvil, plasma_vault, usdc, usdt):
    # given
    anvil.reset_fork(261946538)  # 261946538 - 1002 USDC on pilot V5
    anvil.grant_role(ARBITRUM.PILOT.V5.ACCESS_MANAGER, ALPHA_WALLET, Roles.ALPHA_ROLE)

    timestamp = int(time.time())

    swap_action = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=100,
        token_in_amount=int(500e6),
        min_out_amount=0,
    )

    new_position = ramses_v2_new_position_fuse.new_position(
        token0=ARBITRUM.USDC,
        token1=ARBITRUM.USDT,
        fee=50,
        tick_lower=-100,
        tick_upper=100,
        amount0_desired=int(499e6),
        amount1_desired=int(499e6),
        amount0_min=0,
        amount1_min=0,
        deadline=timestamp + 100,
        ve_ram_token_id=0,
    )

    receipt = plasma_vault.execute([swap_action, new_position])

    (
        _,
        new_token_id,
        liquidity,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
    ) = ramses_v2_new_position_fuse.extract_data_form_new_position_enter_event(receipt)

    decrease_action = ramses_v2_modify_position_fuse.decrease_position(
        token_id=new_token_id,
        liquidity=liquidity,
        amount0_min=0,
        amount1_min=0,
        deadline=timestamp + 100000,
    )

    plasma_vault.execute([decrease_action])

    vault_usdc_balance_before_collect = usdc.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)
    vault_usdt_balance_before_collect = usdt.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    collect_action = ramses_v2_collect_fuse.collect(
        token_ids=[new_token_id],
    )

    plasma_vault.execute([collect_action])

    # then
    vault_usdc_balance_after_collect = usdc.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)
    vault_usdt_balance_after_collect = usdt.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    assert (
        vault_usdc_balance_after_collect - vault_usdc_balance_before_collect
        == 456205367
    ), "collect_usdc_change == 456205367"
    assert (
        vault_usdt_balance_after_collect - vault_usdt_balance_before_collect
        == 498999999
    ), "collect_usdt_change == 456205367"

    close_position_action = ramses_v2_new_position_fuse.close_position(
        token_ids=[new_token_id]
    )

    receipt = plasma_vault.execute([close_position_action])

    (
        _,
        close_token_id,
    ) = ramses_v2_new_position_fuse.extract_data_form_new_position_exit_event(receipt)

    assert new_token_id == close_token_id, "new_token_id == close_token_id"


def test_should_increase_liquidity(anvil, plasma_vault, usdc, usdt):
    # given
    anvil.reset_fork(261946538)  # 261946538 - 1002 USDC on pilot V5
    anvil.grant_role(ARBITRUM.PILOT.V5.ACCESS_MANAGER, ALPHA_WALLET, Roles.ALPHA_ROLE)

    action = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=100,
        token_in_amount=(int(500e6)),
        min_out_amount=0,
    )

    new_position = ramses_v2_new_position_fuse.new_position(
        token0=ARBITRUM.USDC,
        token1=ARBITRUM.USDT,
        fee=50,
        tick_lower=-100,
        tick_upper=100,
        amount0_desired=int(300e6),
        amount1_desired=int(300e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
        ve_ram_token_id=0,
    )

    receipt = plasma_vault.execute([action, new_position])

    (
        _,
        new_token_id,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
    ) = ramses_v2_new_position_fuse.extract_data_form_new_position_enter_event(receipt)

    # Increase position
    increase_action = ramses_v2_modify_position_fuse.increase_position(
        token0=ARBITRUM.USDC,
        token1=ARBITRUM.USDT,
        token_id=new_token_id,
        amount0_desired=int(99e6),
        amount1_desired=int(99e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
    )

    vault_usdc_balance_before_increase = usdc.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)
    vault_usdt_balance_before_increase = usdt.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    # when
    plasma_vault.execute([increase_action])

    # then
    vault_usdc_balance_after_increase = usdc.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)
    vault_usdt_balance_after_increase = usdt.balance_of(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    assert (
        vault_usdc_balance_after_increase - vault_usdc_balance_before_increase
        == -90_509683
    ), "increase_position_change_usdc == -90_509683"
    assert (
        vault_usdt_balance_after_increase - vault_usdt_balance_before_increase
        == -99_000000
    ), "increase_position_change_usdt == -90_509683"


def test_should_claim_rewards_from_ramses_v2_swap_and_transfer_to_rewards_manager(
    anvil,
    plasma_vault,
    rewards_claim_manager,
    uniswap_v3_universal_router,
    usdc,
    ram,
):
    # given
    anvil.reset_fork(261946538)  # 261946538 - 1002 USDC on pilot V5
    anvil.grant_role(ARBITRUM.PILOT.V5.ACCESS_MANAGER, ALPHA_WALLET, Roles.ATOMIST_ROLE)
    anvil.grant_role(ARBITRUM.PILOT.V5.ACCESS_MANAGER, ALPHA_WALLET, Roles.ALPHA_ROLE)
    anvil.grant_role(
        ARBITRUM.PILOT.V5.ACCESS_MANAGER, ALPHA_WALLET, Roles.CLAIM_REWARDS_ROLE
    )
    anvil.grant_role(
        ARBITRUM.PILOT.V5.ACCESS_MANAGER, ALPHA_WALLET, Roles.TRANSFER_REWARDS_ROLE
    )

    swap = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=100,
        token_in_amount=int(500e6),
        min_out_amount=0,
    )

    new_position = ramses_v2_new_position_fuse.new_position(
        token0=ARBITRUM.USDC,
        token1=ARBITRUM.USDT,
        fee=50,
        tick_lower=-100,
        tick_upper=100,
        amount0_desired=int(499e6),
        amount1_desired=int(499e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
        ve_ram_token_id=0,
    )

    tx_result = plasma_vault.execute([swap, new_position])

    (
        _,
        new_token_id,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
        _,
    ) = ramses_v2_new_position_fuse.extract_data_form_new_position_enter_event(
        tx_result
    )

    # One month later
    anvil.move_time(MONTH)

    claim_action = ramses_claim_fuse.claim(
        token_ids=[new_token_id],
        token_rewards=[[ARBITRUM.RAMSES.V2.RAM, ARBITRUM.RAMSES.V2.X_REM]],
    )

    # Claim RAM rewards
    rewards_claim_manager.claim_rewards([claim_action])

    ram_after_claim = ram.balance_of(ARBITRUM.PILOT.V5.REWARDS_CLAIM_MANAGER)

    assert ram_after_claim > 0

    # Transfer REM to ALPHA wallet
    rewards_claim_manager.transfer(
        ARBITRUM.RAMSES.V2.RAM, ALPHA_WALLET, ram_after_claim
    )

    ram_after_transfer = ram.balance_of(ALPHA_WALLET)
    assert ram_after_transfer > 0

    usdc_before_swap_ram = usdc.balance_of(ALPHA_WALLET)

    # swap RAM -> USDC
    path = [ARBITRUM.RAMSES.V2.RAM, 10000, ARBITRUM.WETH, 500, ARBITRUM.USDC]
    uniswap_v3_universal_router.swap(ARBITRUM.RAMSES.V2.RAM, path, ram_after_transfer)

    usdc_after_swap_ram = usdc.balance_of(ALPHA_WALLET)

    rewards_in_usdc = usdc_after_swap_ram - usdc_before_swap_ram
    assert rewards_in_usdc > 0

    # Transfer USDC to rewards_claim_manager
    usdc.transfer(to=ARBITRUM.PILOT.V5.REWARDS_CLAIM_MANAGER, amount=rewards_in_usdc)

    usdc_after_transfer = usdc.balance_of(ALPHA_WALLET)
    assert usdc_after_transfer == 0

    # Update balance on rewards_claim_manager
    rewards_claim_manager.update_balance()
    rewards_claim_manager_balance_before_vesting = usdc.balance_of(
        ARBITRUM.PILOT.V5.REWARDS_CLAIM_MANAGER
    )
    assert rewards_claim_manager_balance_before_vesting > 0

    rewards_claim_manager.update_balance()

    # One month later
    anvil.move_time(DAY)
    rewards_claim_manager.update_balance()

    rewards_claim_manager_balance_after_vesting = usdc.balance_of(
        ARBITRUM.PILOT.V5.REWARDS_CLAIM_MANAGER
    )

    assert rewards_claim_manager_balance_after_vesting == 0

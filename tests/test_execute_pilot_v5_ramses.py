import logging
import os
import time

import pytest
from eth_abi import decode
from web3 import Web3
from web3.types import TxReceipt

from constants import (
    ANVIL_WALLET,
    ARBITRUM,
    ALPHA_WALLET,
)
from ipor_fusion.PlasmaVault import PlasmaVault
from ipor_fusion.RewardsClaimManager import RewardsClaimManager
from ipor_fusion.Roles import Roles
from ipor_fusion.TransactionExecutor import TransactionExecutor
from ipor_fusion.VaultExecuteCallFactory import VaultExecuteCallFactory
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


def grant_role(anvil, role):
    cmd = [
        "cast",
        "send",
        "--unlocked",
        "--from",
        "0x4E3C666F0c898a9aE1F8aBB188c6A2CC151E17fC",
        ARBITRUM.PILOT.V5.ACCESS_MANAGER,
        "grantRole(uint64,address,uint32)()",
        f"{role}",
        ANVIL_WALLET,
        "0",
    ]
    anvil.execute_in_container(cmd)


ramses_v2_new_position_fuse = RamsesV2NewPositionFuse(
    ARBITRUM.PILOT.V5.RAMSES_V2_NEW_POSITION_FUSE
)
ramses_v2_modify_position_fuse = RamsesV2ModifyPositionFuse(
    ARBITRUM.PILOT.V5.RAMSES_V2_MODIFY_POSITION_FUSE
)
ramses_v2_collect_fuse = RamsesV2CollectFuse(ARBITRUM.PILOT.V5.RAMSES_V2_COLLECT_FUSE)
uniswap_v3_swap_fuse = UniswapV3SwapFuse(ARBITRUM.PILOT.V5.UNISWAP_V3_SWAP_FUSE)
ramses_claim_fuse = RamsesClaimFuse(ARBITRUM.PILOT.V5.RAMSES_V2_CLAIM_FUSE)


@pytest.fixture(scope="module", name="transaction_executor")
def transaction_executor_fixture(web3, account) -> TransactionExecutor:
    return TransactionExecutor(web3, account)


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


@pytest.fixture(scope="module", name="vault_execute_call_factory")
def vault_execute_call_factory_fixture() -> VaultExecuteCallFactory:
    return VaultExecuteCallFactory()


@pytest.fixture(name="setup", autouse=True)
def setup_fixture(anvil):
    anvil.reset_fork(261946538)  # 261946538 - 1002 USDC on pilot V5
    grant_role(anvil, Roles.ALPHA_ROLE)
    yield


def test_should_open_new_position_ramses_v2(plasma_vault):
    # given
    swap = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=100,
        token_in_amount=int(500e6),
        min_out_amount=0,
    )

    plasma_vault.execute([swap])

    vault_usdc_balance_after_swap = plasma_vault.balance_of(ARBITRUM.USDC)
    vault_usdt_balance_after_swap = plasma_vault.balance_of(ARBITRUM.USDT)

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
    vault_usdc_balance_after_new_position = plasma_vault.balance_of(ARBITRUM.USDC)
    vault_usdt_balance_after_new_position = plasma_vault.balance_of(ARBITRUM.USDC)

    assert (
        vault_usdc_balance_after_new_position - vault_usdc_balance_after_swap
        == -int(456_205368)
    ), ("new_position_usdc_change == -int(456_205368)")
    assert (
        vault_usdt_balance_after_new_position - vault_usdt_balance_after_swap
        == -int(454_355935)
    ), ("new_position_usdt_change == -int(454_355935)")


def test_should_collect_all_after_decrease_liquidity(anvil, plasma_vault):
    # given
    anvil.reset_fork(261946538)  # 261946538 - 1002 USDC on pilot V5
    grant_role(anvil, Roles.ALPHA_ROLE)

    timestamp = int(time.time())

    swap_action = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=100,
        token_in_amount=int(500e6),
        min_out_amount=0,
    )

    plasma_vault.execute([swap_action])

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

    receipt = plasma_vault.execute([new_position])

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
    ) = extract_enter_data_form_new_position_event(receipt)

    decrease_action = ramses_v2_modify_position_fuse.decrease_position(
        token_id=new_token_id,
        liquidity=liquidity,
        amount0_min=0,
        amount1_min=0,
        deadline=timestamp + 100000,
    )

    plasma_vault.execute([decrease_action])

    vault_usdc_balance_before_collect = plasma_vault.balance_of(ARBITRUM.USDC)
    vault_usdt_balance_before_collect = plasma_vault.balance_of(ARBITRUM.USDT)

    collect_action = ramses_v2_collect_fuse.collect(
        token_ids=[new_token_id],
    )

    plasma_vault.execute([collect_action])

    # then
    vault_usdc_balance_after_collect = plasma_vault.balance_of(ARBITRUM.USDC)
    vault_usdt_balance_after_collect = plasma_vault.balance_of(ARBITRUM.USDT)

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
    ) = extract_exit_data_form_new_position_event(receipt)

    assert new_token_id == close_token_id, "new_token_id == close_token_id"


def test_should_increase_liquidity(anvil, plasma_vault):
    # given
    anvil.reset_fork(261946538)  # 261946538 - 1002 USDC on pilot V5
    grant_role(anvil, Roles.ALPHA_ROLE)

    action = uniswap_v3_swap_fuse.swap(
        token_in_address=ARBITRUM.USDC,
        token_out_address=ARBITRUM.USDT,
        fee=100,
        token_in_amount=(int(500e6)),
        min_out_amount=0,
    )

    plasma_vault.execute([action])

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

    receipt = plasma_vault.execute([new_position])

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
    ) = extract_enter_data_form_new_position_event(receipt)

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

    vault_usdc_balance_before_increase = plasma_vault.balance_of(ARBITRUM.USDC)
    vault_usdt_balance_before_increase = plasma_vault.balance_of(ARBITRUM.USDT)

    # when
    plasma_vault.execute([increase_action])

    # then
    vault_usdc_balance_after_increase = plasma_vault.balance_of(ARBITRUM.USDC)
    vault_usdt_balance_after_increase = plasma_vault.balance_of(ARBITRUM.USDT)

    assert (
        vault_usdc_balance_after_increase - vault_usdc_balance_before_increase
        == -90_509683
    ), "increase_position_change_usdc == -90_509683"
    assert (
        vault_usdt_balance_after_increase - vault_usdt_balance_before_increase
        == -99_000000
    ), "increase_position_change_usdt == -90_509683"


def test_should_claim_rewards_ramses_v2(
    anvil,
    plasma_vault,
    rewards_claim_manager,
    transaction_executor,
):
    # given
    anvil.reset_fork(261946538)  # 261946538 - 1002 USDC on pilot V5
    grant_role(anvil, Roles.ATOMIST_ROLE)
    grant_role(anvil, Roles.ALPHA_ROLE)
    grant_role(anvil, Roles.CLAIM_REWARDS_ROLE)
    grant_role(anvil, Roles.TRANSFER_REWARDS_ROLE)

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

    receipt = plasma_vault.execute([swap, new_position])

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
    ) = extract_enter_data_form_new_position_event(receipt)

    anvil.increase_time(30 * 24 * 60 * 60)

    token_rewards = [[ARBITRUM.RAMSES.V2.REM, ARBITRUM.RAMSES.V2.X_REM]]

    claim_action = ramses_claim_fuse.claim(
        token_ids=[new_token_id], token_rewards=token_rewards
    )

    # then
    rewards_claim_manager.claim_rewards([claim_action])

    rem_after_claim = rewards_claim_manager.balance_of(ARBITRUM.RAMSES.V2.REM)

    assert rem_after_claim > 0

    # transfer REM to ALPHA wallet
    rewards_claim_manager.transfer(
        ARBITRUM.RAMSES.V2.REM, ALPHA_WALLET, rem_after_claim
    )

    rem_after_transfer = transaction_executor.balance_of(
        ALPHA_WALLET, ARBITRUM.RAMSES.V2.REM
    )
    assert rem_after_transfer > 0


def extract_enter_data_form_new_position_event(
    receipt: TxReceipt,
) -> (str, int, int, int, int, str, str, int, int, int):
    event_signature_hash = Web3.keccak(
        text="RamsesV2NewPositionFuseEnter(address,uint256,uint128,uint256,uint256,address,address,uint24,int24,int24)"
    )

    for evnet_log in receipt.logs:
        if evnet_log.topics[0] == event_signature_hash:
            decoded_data = decode(
                [
                    "address",
                    "uint256",
                    "uint128",
                    "uint256",
                    "uint256",
                    "address",
                    "address",
                    "uint24",
                    "int24",
                    "int24",
                ],
                evnet_log["data"],
            )
            (
                version,
                token_id,
                liquidity,
                amount0,
                amount1,
                sender,
                recipient,
                fee,
                tick_lower,
                tick_upper,
            ) = decoded_data
            return (
                version,
                token_id,
                liquidity,
                amount0,
                amount1,
                sender,
                recipient,
                fee,
                tick_lower,
                tick_upper,
            )
    return None, None, None, None, None, None, None, None, None, None


def extract_exit_data_form_new_position_event(receipt: TxReceipt) -> (str, int):
    event_signature_hash = Web3.keccak(
        text="RamsesV2NewPositionFuseExit(address,uint256)"
    )

    for event_log in receipt.logs:
        if event_log.topics[0] == event_signature_hash:
            decoded_data = decode(
                [
                    "address",
                    "uint256",
                ],
                event_log["data"],
            )
            (
                version,
                token_id,
            ) = decoded_data
            return (
                version,
                token_id,
            )
    return None, None

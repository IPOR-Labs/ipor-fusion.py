import time

from eth_abi import decode
from web3 import Web3
from web3.types import TxReceipt

from constants import ARBITRUM, ANVIL_WALLET_PRIVATE_KEY, DAY, MONTH
from ipor_fusion.CheatingPlasmaVaultSystemFactory import (
    CheatingPlasmaVaultSystemFactory,
)
from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles


def test_should_open_new_position_ramses_v2(
    anvil,
):
    anvil.reset_fork(261946538)

    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    cheating = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    cheating.prank(ARBITRUM.PILOT.V5.OWNER)
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, system.alpha(), 0)

    # given
    swap = system.uniswap_v3().swap(
        token_in_address=system.usdc().address(),
        token_out_address=system.usdt().address(),
        fee=100,
        token_in_amount=int(500e6),
        min_out_amount=0,
    )

    system.plasma_vault().execute([swap])

    vault_usdc_balance_after_swap = system.usdc().balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )
    vault_usdt_balance_after_swap = system.usdt().balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )

    new_position = system.ramses_v2().new_position(
        token0=system.usdc().address(),
        token1=system.usdt().address(),
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
    system.plasma_vault().execute([new_position])

    # then
    vault_usdc_balance_after_new_position = system.usdc().balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )
    vault_usdt_balance_after_new_position = system.usdt().balance_of(
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


def test_should_collect_all_after_decrease_liquidity(
    anvil,
):
    # given
    anvil.reset_fork(261946538)

    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    cheating = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    cheating.prank(ARBITRUM.PILOT.V5.OWNER)
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, system.alpha(), 0)

    swap_action = system.uniswap_v3().swap(
        token_in_address=system.usdc().address(),
        token_out_address=system.usdt().address(),
        fee=100,
        token_in_amount=int(500e6),
        min_out_amount=0,
    )

    new_position = system.ramses_v2().new_position(
        token0=system.usdc().address(),
        token1=system.usdt().address(),
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

    receipt = system.plasma_vault().execute([swap_action, new_position])

    _, new_token_id, liquidity, *_ = extract_data_form_new_position_enter_event(receipt)

    decrease_action = system.ramses_v2().decrease_position(
        token_id=new_token_id,
        liquidity=liquidity,
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100000,
    )

    system.plasma_vault().execute([decrease_action])

    vault_usdc_balance_before_collect = system.usdc().balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )
    vault_usdt_balance_before_collect = system.usdt().balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )

    collect_action = system.ramses_v2().collect(
        token_ids=[new_token_id],
    )

    system.plasma_vault().execute([collect_action])

    # then
    vault_usdc_balance_after_collect = system.usdc().balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )
    vault_usdt_balance_after_collect = system.usdt().balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )

    assert (
        vault_usdc_balance_after_collect - vault_usdc_balance_before_collect
        == 456205367
    ), "collect_usdc_change == 456205367"
    assert (
        vault_usdt_balance_after_collect - vault_usdt_balance_before_collect
        == 498999999
    ), "collect_usdt_change == 456205367"

    close_position_action = system.ramses_v2().close_position(token_ids=[new_token_id])

    receipt = system.plasma_vault().execute([close_position_action])
    _, close_token_id = extract_data_form_new_position_exit_event(receipt)

    assert new_token_id == close_token_id, "new_token_id == close_token_id"


def test_should_increase_liquidity(
    anvil,
):
    # given
    anvil.reset_fork(261946538)  # 261946538 - 1002 USDC on pilot V5

    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    cheating = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    cheating.prank(ARBITRUM.PILOT.V5.OWNER)
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, system.alpha(), 0)

    swap = system.uniswap_v3().swap(
        token_in_address=system.usdc().address(),
        token_out_address=system.usdt().address(),
        fee=100,
        token_in_amount=(int(500e6)),
        min_out_amount=0,
    )

    new_position = system.ramses_v2().new_position(
        token0=system.usdc().address(),
        token1=system.usdt().address(),
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

    receipt = system.plasma_vault().execute([swap, new_position])

    _, new_token_id, *_ = extract_data_form_new_position_enter_event(receipt)

    # Increase position
    increase_action = system.ramses_v2().increase_position(
        token0=system.usdc().address(),
        token1=system.usdt().address(),
        token_id=new_token_id,
        amount0_desired=int(99e6),
        amount1_desired=int(99e6),
        amount0_min=0,
        amount1_min=0,
        deadline=int(time.time()) + 100,
    )

    vault_usdc_balance_before_increase = system.usdc().balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )
    vault_usdt_balance_before_increase = system.usdt().balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )

    # when
    system.plasma_vault().execute([increase_action])

    # then
    vault_usdc_balance_after_increase = system.usdc().balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )
    vault_usdt_balance_after_increase = system.usdt().balance_of(
        ARBITRUM.PILOT.V5.PLASMA_VAULT
    )

    assert (
        vault_usdc_balance_after_increase - vault_usdc_balance_before_increase
        == -90_509683
    ), "increase_position_change_usdc == -90_509683"
    assert (
        vault_usdt_balance_after_increase - vault_usdt_balance_before_increase
        == -99_000000
    ), "increase_position_change_usdt == -90_509683"


def test_should_claim_rewards_from_ramses_v2_swap_and_transfer_to_rewards_manager(
    anvil, uniswap_v3_universal_router
):
    # given
    anvil.reset_fork(261946538)

    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    cheating = CheatingPlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(ARBITRUM.PILOT.V5.PLASMA_VAULT)

    cheating.prank(ARBITRUM.PILOT.V5.OWNER)
    cheating.access_manager().grant_role(Roles.ATOMIST_ROLE, system.alpha(), 0)
    cheating.access_manager().grant_role(Roles.ALPHA_ROLE, system.alpha(), 0)
    cheating.access_manager().grant_role(Roles.CLAIM_REWARDS_ROLE, system.alpha(), 0)
    cheating.access_manager().grant_role(Roles.TRANSFER_REWARDS_ROLE, system.alpha(), 0)

    swap = system.uniswap_v3().swap(
        token_in_address=system.usdc().address(),
        token_out_address=system.usdt().address(),
        fee=100,
        token_in_amount=int(500e6),
        min_out_amount=0,
    )

    new_position = system.ramses_v2().new_position(
        token0=system.usdc().address(),
        token1=system.usdt().address(),
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

    tx_result = system.plasma_vault().execute([swap, new_position])

    _, new_token_id, *_ = extract_data_form_new_position_enter_event(tx_result)

    # One month later
    anvil.move_time(MONTH)

    claim_action = system.ramses_v2().claim(
        token_ids=[new_token_id],
        token_rewards=[
            [system.ramses_v2().ram().address(), system.ramses_v2().x_ram().address()]
        ],
    )

    # Claim RAM rewards
    system.rewards_claim_manager().claim_rewards([claim_action])

    ram_after_claim = (
        system.ramses_v2().ram().balance_of(system.rewards_claim_manager().address())
    )

    assert ram_after_claim > 0

    # Transfer REM to ALPHA wallet
    system.rewards_claim_manager().transfer(
        system.ramses_v2().ram().address(), system.alpha(), ram_after_claim
    )

    ram_after_transfer = system.ramses_v2().ram().balance_of(system.alpha())
    assert ram_after_transfer > 0

    usdc_before_swap_ram = system.usdc().balance_of(system.alpha())

    # swap RAM -> USDC
    path = [
        system.ramses_v2().ram().address(),
        10000,
        ARBITRUM.WETH,
        500,
        system.usdc().address(),
    ]
    uniswap_v3_universal_router.swap(ARBITRUM.RAMSES.V2.RAM, path, ram_after_transfer)

    usdc_after_swap_ram = system.usdc().balance_of(system.alpha())

    rewards_in_usdc = usdc_after_swap_ram - usdc_before_swap_ram
    assert rewards_in_usdc > 0

    # Transfer USDC to rewards_claim_manager
    system.usdc().transfer(
        to=system.rewards_claim_manager().address(), amount=rewards_in_usdc
    )

    usdc_after_transfer = system.usdc().balance_of(system.alpha())
    assert usdc_after_transfer == 0

    # Update balance on rewards_claim_manager
    system.rewards_claim_manager().update_balance()
    rewards_claim_manager_balance_before_vesting = system.usdc().balance_of(
        system.rewards_claim_manager().address()
    )
    assert rewards_claim_manager_balance_before_vesting > 0

    system.rewards_claim_manager().update_balance()

    # One month later
    anvil.move_time(DAY)
    system.rewards_claim_manager().update_balance()

    rewards_claim_manager_balance_after_vesting = system.usdc().balance_of(
        system.rewards_claim_manager().address()
    )

    assert rewards_claim_manager_balance_after_vesting == 0


def extract_data_form_new_position_enter_event(
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


def extract_data_form_new_position_exit_event(receipt: TxReceipt) -> (str, int):
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

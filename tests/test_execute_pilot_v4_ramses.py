# import logging
# import os
#
# import pytest
# from eth_abi import decode
# from web3 import Web3
# from web3.types import TxReceipt
#
# from commons import execute_transaction, read_token_balance
# from constants import (
#     ANVIL_WALLET,
#     ARBITRUM,
# )
# from ipor_fusion_sdk.VaultExecuteCallFactory import VaultExecuteCallFactory
# from ipor_fusion_sdk.fuse.RamsesClaimFuse import RamsesClaimFuse
# from ipor_fusion_sdk.fuse.RamsesV2CollectFuse import RamsesV2CollectFuse
# from ipor_fusion_sdk.fuse.RamsesV2ModifyPositionFuse import RamsesV2ModifyPositionFuse
# from ipor_fusion_sdk.fuse.RamsesV2NewPositionFuse import RamsesV2NewPositionFuse
# from ipor_fusion_sdk.fuse.UniswapV3SwapFuse import UniswapV3SwapFuse
#
# logging.basicConfig(level=logging.DEBUG)
# log = logging.getLogger(__name__)
#
# ARBITRUM_PROVIDER_URL = "ARBITRUM_PROVIDER_URL"
# FORK_URL = os.getenv(ARBITRUM_PROVIDER_URL)
# if not FORK_URL:
#     raise ValueError("Environment variable ARBITRUM_PROVIDER_URL must be set")
#
# SET_ANVIL_WALLET_AS_PILOT_V4_ALPHA_COMMAND = [
#     "cast",
#     "send",
#     "--unlocked",
#     "--from",
#     "0x4E3C666F0c898a9aE1F8aBB188c6A2CC151E17fC",
#     ARBITRUM.PILOT.V4.ACCESS_MANAGER,
#     "grantRole(uint64,address,uint32)()",
#     "200",
#     ANVIL_WALLET,
#     "0",
# ]
#
# ramses_v2_new_position_fuse = RamsesV2NewPositionFuse(
#     ARBITRUM.PILOT.V4.RAMSES_V2_NEW_POSITION_FUSE
# )
# ramses_v2_modify_position_fuse = RamsesV2ModifyPositionFuse(
#     ARBITRUM.PILOT.V4.RAMSES_V2_MODIFY_POSITION_FUSE
# )
# ramses_v2_collect_fuse = RamsesV2CollectFuse(ARBITRUM.PILOT.V4.RAMSES_V2_COLLECT_FUSE)
# uniswap_v3_swap_fuse = UniswapV3SwapFuse(ARBITRUM.PILOT.V4.UNISWAP_V3_SWAP_FUSE)
# ramses_claim_fuse = RamsesClaimFuse(ARBITRUM.PILOT.V4.RAMSES_V2_CLAIM_FUSE)
#
#
# @pytest.fixture(scope="module", name="vault_execute_call_factory")
# def vault_execute_call_factory_fixture() -> VaultExecuteCallFactory:
#     return VaultExecuteCallFactory()
#
#
# @pytest.fixture(name="setup", autouse=True)
# def setup_fixture(anvil):
#     anvil.reset_fork(254261635)
#     anvil.execute_in_container(SET_ANVIL_WALLET_AS_PILOT_V4_ALPHA_COMMAND)
#     yield
#
#
# def test_should_open_new_position_ramses_v2(web3, account, vault_execute_call_factory):
#     # given
#     timestamp = web3.eth.get_block("latest")["timestamp"]
#
#     token_in_amount = int(500e6)
#     min_out_amount = 0
#     fee = 100
#
#     swap = uniswap_v3_swap_fuse.swap(
#         token_in_address=ARBITRUM.USDC,
#         token_out_address=ARBITRUM.USDT,
#         fee=fee,
#         token_in_amount=token_in_amount,
#         min_out_amount=min_out_amount,
#     )
#
#     execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         vault_execute_call_factory.create_execute_call_from_action(swap),
#         account,
#     )
#
#     vault_usdc_balance_after_swap = read_token_balance(
#         web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
#     )
#     vault_usdt_balance_after_swap = read_token_balance(
#         web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDT
#     )
#
#     new_position = ramses_v2_new_position_fuse.new_position(
#         token0=ARBITRUM.USDC,
#         token1=ARBITRUM.USDT,
#         fee=50,
#         tick_lower=-1,
#         tick_upper=1,
#         amount0_desired=int(499e6),
#         amount1_desired=int(499e6),
#         amount0_min=0,
#         amount1_min=0,
#         deadline=timestamp + 100,
#         ve_ram_token_id=0,
#     )
#
#     # when
#     execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         vault_execute_call_factory.create_execute_call_from_action(new_position),
#         account,
#     )
#
#     # then
#     vault_usdc_balance_after_new_position = read_token_balance(
#         web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
#     )
#     vault_usdt_balance_after_new_position = read_token_balance(
#         web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
#     )
#
#     assert vault_usdc_balance_after_new_position - vault_usdc_balance_after_swap == -int(
#         157_104526
#     ), "vault_usdc_balance_after_new_position - vault_usdc_balance_after_swap == -157_104526"
#     assert vault_usdt_balance_after_new_position - vault_usdt_balance_after_swap == -int(
#         157_104526
#     ), "vault_usdt_balance_after_new_position - vault_usdt_balance_after_swap == -157_104526"
#
#
# def test_should_collect_all_after_decrease_liquidity(
#     web3, account, vault_execute_call_factory
# ):
#     # given
#     timestamp = web3.eth.get_block("latest")["timestamp"]
#
#     token_in_amount = int(500e6)
#     min_out_amount = 0
#     fee = 100
#
#     action = uniswap_v3_swap_fuse.swap(
#         token_in_address=ARBITRUM.USDC,
#         token_out_address=ARBITRUM.USDT,
#         fee=fee,
#         token_in_amount=token_in_amount,
#         min_out_amount=min_out_amount,
#     )
#
#     execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         vault_execute_call_factory.create_execute_call_from_action(action),
#         account,
#     )
#
#     new_position = ramses_v2_new_position_fuse.new_position(
#         token0=ARBITRUM.USDC,
#         token1=ARBITRUM.USDT,
#         fee=100,
#         tick_lower=-100,
#         tick_upper=101,
#         amount0_desired=int(499e6),
#         amount1_desired=int(499e6),
#         amount0_min=0,
#         amount1_min=0,
#         deadline=timestamp + 100,
#         ve_ram_token_id=0,
#     )
#
#     receipt = execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         vault_execute_call_factory.create_execute_call_from_action(new_position),
#         account,
#     )
#
#     (
#         _,
#         new_token_id,
#         liquidity,
#         _,
#         _,
#         _,
#         _,
#         _,
#         _,
#         _,
#     ) = extract_enter_data_form_new_position_event(receipt)
#
#     decrease_action = ramses_v2_modify_position_fuse.decrease_position(
#         token_id=new_token_id,
#         liquidity=liquidity,
#         amount0_min=0,
#         amount1_min=0,
#         deadline=timestamp + 100000,
#     )
#
#     execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         vault_execute_call_factory.create_execute_call_from_action(decrease_action),
#         account,
#     )
#
#     vault_usdc_balance_before_collect = read_token_balance(
#         web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
#     )
#     vault_usdt_balance_before_collect = read_token_balance(
#         web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
#     )
#
#     enter_action = ramses_v2_collect_fuse.collect(
#         token_ids=[new_token_id],
#     )
#
#     execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         vault_execute_call_factory.create_execute_call_from_action(enter_action),
#         account,
#     )
#
#     # then
#     vault_usdc_balance_after_collect = read_token_balance(
#         web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
#     )
#     vault_usdt_balance_after_collect = read_token_balance(
#         web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
#     )
#
#     collect_usdc_change = (
#         vault_usdc_balance_after_collect - vault_usdc_balance_before_collect
#     )
#     collect_usdt_change = (
#         vault_usdt_balance_after_collect - vault_usdt_balance_before_collect
#     )
#
#     assert (
#         int(498_000000) < collect_usdc_change < int(500_000000)
#     ), "int(498_000000) < collect_usdc_change < int(500_000000)"
#     assert (
#         int(489_000000) < collect_usdt_change < int(500_000000)
#     ), "int(489_000000) < collect_usdt_change < int(500_000000)"
#
#     close_position_action = ramses_v2_new_position_fuse.close_position(
#         token_ids=[new_token_id]
#     )
#
#     receipt = execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         vault_execute_call_factory.create_execute_call_from_action(
#             close_position_action
#         ),
#         account,
#     )
#
#     (
#         _,
#         close_token_id,
#     ) = extract_exit_data_form_new_position_event(receipt)
#
#     assert new_token_id == close_token_id, "new_token_id == close_token_id"
#
#
# def test_should_increase_liquidity(web3, account, vault_execute_call_factory):
#     # given
#     timestamp = web3.eth.get_block("latest")["timestamp"]
#
#     token_in_amount = int(500e6)
#     min_out_amount = 0
#     fee = 100
#
#     action = uniswap_v3_swap_fuse.swap(
#         token_in_address=ARBITRUM.USDC,
#         token_out_address=ARBITRUM.USDT,
#         fee=fee,
#         token_in_amount=token_in_amount,
#         min_out_amount=min_out_amount,
#     )
#
#     function_swap = vault_execute_call_factory.create_execute_call_from_action(action)
#
#     execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         function_swap,
#         account,
#     )
#
#     position_action = ramses_v2_new_position_fuse.new_position(
#         token0=ARBITRUM.USDC,
#         token1=ARBITRUM.USDT,
#         fee=100,
#         tick_lower=-100,
#         tick_upper=101,
#         amount0_desired=int(400e6),
#         amount1_desired=int(400e6),
#         amount0_min=0,
#         amount1_min=0,
#         deadline=timestamp + 100,
#         ve_ram_token_id=0,
#     )
#
#     receipt = execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         vault_execute_call_factory.create_execute_call_from_action(position_action),
#         account,
#     )
#
#     (
#         _,
#         new_token_id,
#         _,
#         _,
#         _,
#         _,
#         _,
#         _,
#         _,
#         _,
#     ) = extract_enter_data_form_new_position_event(receipt)
#
#     # Increase position
#     increase_action = ramses_v2_modify_position_fuse.increase_position(
#         token0=ARBITRUM.USDC,
#         token1=ARBITRUM.USDT,
#         token_id=new_token_id,
#         amount0_desired=int(99e6),
#         amount1_desired=int(99e6),
#         amount0_min=0,
#         amount1_min=0,
#         deadline=timestamp + 100,
#     )
#
#     vault_usdc_balance_before_increase = read_token_balance(
#         web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
#     )
#     vault_usdt_balance_before_increase = read_token_balance(
#         web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
#     )
#
#     # when
#     execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         vault_execute_call_factory.create_execute_call_from_action(increase_action),
#         account,
#     )
#
#     # then
#     vault_usdc_balance_after_increase = read_token_balance(
#         web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
#     )
#     vault_usdt_balance_after_increase = read_token_balance(
#         web3, ARBITRUM.PILOT.V4.PLASMA_VAULT, ARBITRUM.USDC
#     )
#
#     increase_position_change_usdc = (
#         vault_usdc_balance_after_increase - vault_usdc_balance_before_increase
#     )
#     increase_position_change_usdt = (
#         vault_usdt_balance_after_increase - vault_usdt_balance_before_increase
#     )
#
#     assert (
#         increase_position_change_usdc == -99_000000
#     ), "increase_position_change_usdc == -99_000000"
#     assert (
#         increase_position_change_usdt == -99_000000
#     ), "increase_position_change_usdt == -97_046288"
#
#
# def test_should_claim_rewards_ramses_v2(web3, account, vault_execute_call_factory):
#     # given
#     timestamp = web3.eth.get_block("latest")["timestamp"]
#
#     token_in_amount = int(500e6)
#     min_out_amount = 0
#     fee = 100
#
#     swap = uniswap_v3_swap_fuse.swap(
#         token_in_address=ARBITRUM.USDC,
#         token_out_address=ARBITRUM.USDT,
#         fee=fee,
#         token_in_amount=token_in_amount,
#         min_out_amount=min_out_amount,
#     )
#
#     execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         vault_execute_call_factory.create_execute_call_from_action(swap),
#         account,
#     )
#
#     new_position = ramses_v2_new_position_fuse.new_position(
#         token0=ARBITRUM.USDC,
#         token1=ARBITRUM.USDT,
#         fee=50,
#         tick_lower=-1,
#         tick_upper=1,
#         amount0_desired=int(499e6),
#         amount1_desired=int(499e6),
#         amount0_min=0,
#         amount1_min=0,
#         deadline=timestamp + 100,
#         ve_ram_token_id=0,
#     )
#
#     # when
#     receipt = execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         vault_execute_call_factory.create_execute_call_from_action(new_position),
#         account,
#     )
#
#     (
#         _,
#         new_token_id,
#         _,
#         _,
#         _,
#         _,
#         _,
#         _,
#         _,
#         _,
#     ) = extract_enter_data_form_new_position_event(receipt)
#
#     token_rewards = [[ARBITRUM.RAMSES.V2.REM, ARBITRUM.RAMSES.V2.X_REM]]
#
#     claim = ramses_claim_fuse.claim(
#         token_ids=[new_token_id], token_rewards=token_rewards
#     )
#
#     execute_transaction(
#         web3,
#         ARBITRUM.PILOT.V4.PLASMA_VAULT,
#         vault_execute_call_factory.create_claim_rewards_call([claim]),
#         account,
#     )
#
#
# def extract_enter_data_form_new_position_event(
#     receipt: TxReceipt,
# ) -> (str, int, int, int, int, str, str, int, int, int):
#     event_signature_hash = Web3.keccak(
#         text="RamsesV2NewPositionFuseEnter(address,uint256,uint128,uint256,uint256,address,address,uint24,int24,int24)"
#     )
#
#     for evnet_log in receipt.logs:
#         if evnet_log.topics[0] == event_signature_hash:
#             decoded_data = decode(
#                 [
#                     "address",
#                     "uint256",
#                     "uint128",
#                     "uint256",
#                     "uint256",
#                     "address",
#                     "address",
#                     "uint24",
#                     "int24",
#                     "int24",
#                 ],
#                 evnet_log["data"],
#             )
#             (
#                 version,
#                 token_id,
#                 liquidity,
#                 amount0,
#                 amount1,
#                 sender,
#                 recipient,
#                 fee,
#                 tick_lower,
#                 tick_upper,
#             ) = decoded_data
#             return (
#                 version,
#                 token_id,
#                 liquidity,
#                 amount0,
#                 amount1,
#                 sender,
#                 recipient,
#                 fee,
#                 tick_lower,
#                 tick_upper,
#             )
#     return None, None, None, None, None, None, None, None, None, None
#
#
# def extract_exit_data_form_new_position_event(receipt: TxReceipt) -> (str, int):
#     event_signature_hash = Web3.keccak(
#         text="RamsesV2NewPositionFuseExit(address,uint256)"
#     )
#
#     for event_log in receipt.logs:
#         if event_log.topics[0] == event_signature_hash:
#             decoded_data = decode(
#                 [
#                     "address",
#                     "uint256",
#                 ],
#                 event_log["data"],
#             )
#             (
#                 version,
#                 token_id,
#             ) = decoded_data
#             return (
#                 version,
#                 token_id,
#             )
#     return None, None

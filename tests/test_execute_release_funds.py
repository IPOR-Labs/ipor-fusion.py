import logging
import os

from eth_typing import BlockNumber
from web3 import Web3

from ipor_fusion.testing import AnvilTestContainerStarter, ForkedWeb3Context
from ipor_fusion import Roles, PlasmaVault, AccessManager, ERC20, WithdrawManager
from ipor_fusion.types import Amount, Period
from ipor_fusion.addresses import ARBITRUM_USDC

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

fork_url = os.getenv("ARBITRUM_PROVIDER_URL")

anvil = AnvilTestContainerStarter(fork_url)
anvil.start()


def test_should_release_funds():
    """
    Test that funds can be released appropriately from a Plasma Vault.
    """
    anvil.reset_fork(BlockNumber(285000000))

    user_account = Web3.to_checksum_address(
        "0x1714400FF23dB4aF24F9fd64e7039e6597f18C2b"
    )
    vault_address = Web3.to_checksum_address(
        "0x05231e2fB2F580F043D7760a5CFb4Ec6F01656E9"
    )

    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )
    withdraw_manager = WithdrawManager(
        forked_ctx, plasma_vault.withdraw_manager_address()
    )

    owner = access_manager.owner()
    forked_ctx.prank(owner)
    access_manager.grant_role(Roles.ALPHA_ROLE, user_account, 0)
    access_manager.grant_role(Roles.WHITELIST_ROLE, user_account, 0)

    forked_ctx.prank(user_account)

    usdc = ERC20(forked_ctx, ARBITRUM_USDC)

    amount = Amount(1000_000000)

    usdc.approve(vault_address, amount)
    plasma_vault.deposit(amount, user_account)

    to_withdraw = plasma_vault.max_withdraw(user_account)

    withdraw_manager.request(to_withdraw)

    anvil.move_time(Period.HOUR)

    requested_amount, release_timestamp = withdraw_manager.get_pending_requests_info()
    assert requested_amount == to_withdraw

    anvil.move_time(Period.MINUTE)

    withdraw_manager.release_funds(timestamp=release_timestamp)

    anvil.move_time(Period.HOUR)

    user_balance_before = usdc.balance_of(user_account)

    to_withdraw = plasma_vault.max_withdraw(user_account)

    plasma_vault.withdraw(assets=to_withdraw, receiver=user_account, owner=user_account)

    user_balance_after = usdc.balance_of(user_account)
    user_balance_change = user_balance_after - user_balance_before
    assert user_balance_change == to_withdraw


def test_should_release_funds_shares():
    anvil.reset_fork(BlockNumber(315570373))

    user_account = Web3.to_checksum_address(
        "0x1714400FF23dB4aF24F9fd64e7039e6597f18C2b"
    )
    vault_address = Web3.to_checksum_address(
        "0x272Cb09e2d6237304E4D2BcB9DEf40032aA0ebB1"
    )
    alpha_address = Web3.to_checksum_address(
        "0xB2Fc9e5c92577bE694E7EBbE76Eeb5977bec4D9A"
    )

    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )
    withdraw_manager = WithdrawManager(
        forked_ctx, plasma_vault.withdraw_manager_address()
    )

    owner = access_manager.owner()
    forked_ctx.prank(owner)
    access_manager.grant_role(Roles.ALPHA_ROLE, alpha_address, 0)
    access_manager.grant_role(Roles.WHITELIST_ROLE, user_account, 0)

    usdc = ERC20(forked_ctx, ARBITRUM_USDC)

    amount = Amount(1000_000000)

    forked_ctx.prank(user_account)
    usdc.approve(plasma_vault.address, amount)
    plasma_vault.deposit(amount, user_account)

    max_withdraw = plasma_vault.max_withdraw(user_account)

    shares = plasma_vault.convert_to_shares(max_withdraw)

    withdraw_manager.request_shares(shares)

    anvil.move_time(Period.HOUR)

    forked_ctx.prank(alpha_address)
    withdraw_manager.release_funds(
        timestamp=anvil.current_block_timestamp() - 1, shares=shares
    )

    anvil.move_time(Period.HOUR)

    user_balance_before = usdc.balance_of(user_account)

    forked_ctx.prank(user_account)
    plasma_vault.redeem_from_request(
        shares=shares, receiver=user_account, owner=user_account
    )

    user_balance_after = usdc.balance_of(user_account)
    user_balance_change = user_balance_after - user_balance_before

    assert user_balance_change > 999_000000

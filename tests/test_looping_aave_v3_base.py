import logging
import os

import pytest
from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from constants import (
    ANVIL_WALLET,
    BASE_AAVE_V3_SUPPLY_FUSE,
    BASE_AAVE_V3_BORROW_FUSE,
    BASE_MORPHO_FLASH_LOAN_FUSE,
    BASE_UNIVERSAL_SWAP_FUSE,
)
from ipor_fusion.testing import AnvilTestContainerStarter, ForkedWeb3Context
from ipor_fusion import Roles, PlasmaVault, AccessManager, ERC20, PriceOracleMiddleware
from ipor_fusion.fuses import (
    AaveV3SupplyFuse,
    AaveV3BorrowFuse,
    MorphoFlashLoanFuse,
    UniversalTokenSwapperFuse,
)
from ipor_fusion.addresses import (
    BASE_WSTETH,
    BASE_WETH,
    BASE_AAVE_V3_VARIABLE_DEBT_WETH,
    BASE_AAVE_V3_A_WSTETH,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

fork_url = os.environ["BASE_PROVIDER_URL"]


@pytest.fixture(scope="module")
def anvil():
    with AnvilTestContainerStarter(fork_url) as a:
        yield a


AERODROME_ROUTER_ADDRESS = Web3.to_checksum_address(
    "0xBE6D8F0D05cC4bE24d5167a3eF062215bE6D18a5"
)
AERODROME_EXECUTOR = Web3.to_checksum_address(
    "0x591435c065fCE9713c8B112FCBf5af98b8975cb3"
)


def _aerodrome_path(token_in: ChecksumAddress, token_out: ChecksumAddress) -> bytes:
    if (token_in == BASE_WETH and token_out == BASE_WSTETH) or (
        token_in == BASE_WSTETH and token_out == BASE_WETH
    ):
        return encode_packed(
            ["address", "uint24", "address"],
            [token_in, 1, token_out],
        )
    raise ValueError(f"Unsupported path: {token_in} -> {token_out}")


def _aerodrome_swap(
    universal, token_in, token_out, amount_in, min_amount_out, deadline
):
    targets = [token_in, AERODROME_ROUTER_ADDRESS]

    approve_selector = function_signature_to_4byte_selector("approve(address,uint256)")
    approve_data = approve_selector + encode(
        ["address", "uint256"], [AERODROME_ROUTER_ADDRESS, amount_in]
    )

    path = _aerodrome_path(token_in, token_out)
    swap_selector = function_signature_to_4byte_selector(
        "exactInput((bytes,address,uint256,uint256,uint256))"
    )
    swap_data = swap_selector + encode(
        ["(bytes,address,uint256,uint256,uint256)"],
        [[path, AERODROME_EXECUTOR, deadline, amount_in, min_amount_out]],
    )

    return universal.swap(
        token_in=token_in,
        token_out=token_out,
        amount_in=amount_in,
        targets=targets,
        data=[approve_data, swap_data],
    )


def _log_balances(forked_ctx, vault_address, msg):
    log.info("[%s]", msg)
    if (wsteth := ERC20(forked_ctx, BASE_WSTETH).balance_of(vault_address)) > 0:
        log.info("    wsteth balance: %s WStETH", wsteth / 1e18)
    log.info(
        "      weth balance: %s WETH",
        ERC20(forked_ctx, BASE_WETH).balance_of(vault_address) / 1e18,
    )
    log.info(
        "aave collateral: %s aWStETH",
        ERC20(forked_ctx, BASE_AAVE_V3_A_WSTETH).balance_of(vault_address) / 1e18,
    )
    log.info(
        "  aave borrowed: %s dWETH",
        ERC20(forked_ctx, BASE_AAVE_V3_VARIABLE_DEBT_WETH).balance_of(vault_address)
        / 1e18,
    )
    log.info("----")


def test_supply_borrow_in_flash_loan(anvil):
    anvil.reset_fork(30431901)

    atomist = Web3.to_checksum_address("0xF6a9bd8F6DC537675D499Ac1CA14f2c55d8b5569")
    vault_address = Web3.to_checksum_address(
        "0xc4c00d8b323f37527eeda27c87412378be9f68ec"
    )
    wsteth_holder = Web3.to_checksum_address(
        "0xf0bb20865277aBd641a307eCe5Ee04E79073416C"
    )

    LEVERAGE = 10
    LTV = 1 - 1 / LEVERAGE

    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )
    oracle = PriceOracleMiddleware(
        forked_ctx, plasma_vault.get_price_oracle_middleware_address()
    )

    forked_ctx.prank(atomist)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)
    access_manager.grant_role(Roles.WHITELIST_ROLE, wsteth_holder, 0)

    forked_ctx.prank(ANVIL_WALLET)

    initial_deposit = int(1e18)

    forked_ctx.prank(wsteth_holder)
    ERC20(forked_ctx, BASE_WSTETH).approve(
        spender=plasma_vault.address,
        amount=initial_deposit,
    )
    plasma_vault.deposit(
        assets=initial_deposit,
        receiver=wsteth_holder,
    )

    assert ERC20(forked_ctx, BASE_WSTETH).balance_of(vault_address) == 1e18

    forked_ctx.prank(ANVIL_WALLET)

    aave_supply = AaveV3SupplyFuse(BASE_AAVE_V3_SUPPLY_FUSE)
    aave_borrow = AaveV3BorrowFuse(BASE_AAVE_V3_BORROW_FUSE)
    morpho_flash = MorphoFlashLoanFuse(BASE_MORPHO_FLASH_LOAN_FUSE)
    universal = UniversalTokenSwapperFuse(BASE_UNIVERSAL_SWAP_FUSE)

    wsteth_balance = ERC20(forked_ctx, BASE_WSTETH).balance_of(vault_address)
    wsteth_collateral_amount = wsteth_balance * LEVERAGE

    supply = aave_supply.supply(
        asset=BASE_WSTETH,
        amount=wsteth_collateral_amount,
        e_mode=1,
    )

    wsteth_price = oracle.get_asset_price(BASE_WSTETH)
    weth_price = oracle.get_asset_price(BASE_WETH)

    weth_borrow_amount = int(
        wsteth_collateral_amount * LTV * wsteth_price.readable() / weth_price.readable()
    )

    borrow = aave_borrow.borrow(
        asset=BASE_WETH,
        amount=weth_borrow_amount,
    )

    current_block = forked_ctx.get_block("latest")
    deadline = current_block.timestamp + 1000

    swap = _aerodrome_swap(
        universal=universal,
        token_in=BASE_WETH,
        token_out=BASE_WSTETH,
        amount_in=weth_borrow_amount,
        min_amount_out=0,
        deadline=deadline,
    )

    flash_loan_amount = wsteth_collateral_amount - wsteth_balance
    flash_loan = morpho_flash.flash_loan(
        asset=BASE_WSTETH,
        amount=flash_loan_amount,
        actions=[supply, borrow, swap],
    )

    _log_balances(forked_ctx, vault_address, "before leveraging strategy")

    plasma_vault.execute([flash_loan])

    _log_balances(forked_ctx, vault_address, "after leveraging strategy")

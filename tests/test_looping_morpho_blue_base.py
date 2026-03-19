import logging
import os

import pytest
from eth_abi import encode
from eth_abi.packed import encode_packed
from eth_typing import ChecksumAddress
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3

from addresses import BASE_WSTETH, BASE_WETH
from constants import (
    ANVIL_WALLET,
    BASE_MORPHO_COLLATERAL_FUSE,
    BASE_MORPHO_BORROW_FUSE,
    BASE_MORPHO_FLASH_LOAN_FUSE,
    BASE_UNIVERSAL_SWAP_FUSE,
)
from ipor_fusion.testing import AnvilTestContainerStarter, ForkedWeb3Context
from ipor_fusion import Roles, PlasmaVault, AccessManager, ERC20, PriceOracleMiddleware
from ipor_fusion.fuses import (
    MorphoCollateralFuse,
    MorphoBorrowFuse,
    MorphoFlashLoanFuse,
    UniversalTokenSwapperFuse,
)

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

fork_url = os.environ["BASE_PROVIDER_URL"]


@pytest.fixture(scope="module")
def anvil():
    with AnvilTestContainerStarter(fork_url) as a:
        yield a


# WStETH/WETH market on Morpho Blue (Base), LLTV = 94.5%
MORPHO_BLUE_MARKET_ID = (
    "3a4048c64ba1b375330d376b1ce40e4047d03b47ab4d48af484edec9fec801ba"
)

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


def test_looping_morpho_blue(anvil):
    """Leveraged looping strategy on Morpho Blue via flash loan.

    Strategy (executed atomically inside a Morpho flash loan):
      1. Flash loan WStETH from Morpho
      2. Supply all WStETH (deposited + flash loaned) as collateral to Morpho Blue market
      3. Borrow WETH against the collateral
      4. Swap borrowed WETH -> WStETH via Aerodrome to repay the flash loan
    """
    anvil.reset_fork(35437300)

    atomist = Web3.to_checksum_address("0xF6a9bd8F6DC537675D499Ac1CA14f2c55d8b5569")
    vault_address = Web3.to_checksum_address(
        "0xc4c00d8b323f37527eeda27c87412378be9f68ec"
    )
    wsteth_holder = Web3.to_checksum_address(
        "0xf0bb20865277aBd641a307eCe5Ee04E79073416C"
    )

    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    access_manager = AccessManager(
        forked_ctx, plasma_vault.get_access_manager_address()
    )
    oracle = PriceOracleMiddleware(
        forked_ctx, plasma_vault.get_price_oracle_middleware_address()
    )

    # Grant roles
    forked_ctx.prank(atomist)
    access_manager.grant_role(Roles.ALPHA_ROLE, ANVIL_WALLET, 0)
    access_manager.grant_role(Roles.WHITELIST_ROLE, wsteth_holder, 0)

    # Deposit WStETH into vault (holder already has tokens at this block)
    deposit_amount = int(1e18)

    forked_ctx.prank(wsteth_holder)
    ERC20(forked_ctx, BASE_WSTETH).approve(
        spender=plasma_vault.address,
        amount=deposit_amount,
    )
    plasma_vault.deposit(assets=deposit_amount, receiver=wsteth_holder)

    # Set up markets
    forked_ctx.prank(ANVIL_WALLET)

    morpho_collateral = MorphoCollateralFuse(BASE_MORPHO_COLLATERAL_FUSE)
    morpho_borrow = MorphoBorrowFuse(BASE_MORPHO_BORROW_FUSE)
    morpho_flash = MorphoFlashLoanFuse(BASE_MORPHO_FLASH_LOAN_FUSE)
    universal = UniversalTokenSwapperFuse(BASE_UNIVERSAL_SWAP_FUSE)

    # 3x leverage, LTV = 66.7% — market LLTV is 94.5%
    leverage = 3
    ltv = 1 - 1 / leverage

    wsteth_balance = ERC20(forked_ctx, BASE_WSTETH).balance_of(vault_address)
    wsteth_collateral_amount = wsteth_balance * leverage

    # Step 1: Supply collateral
    supply_collateral = morpho_collateral.supply_collateral(
        market_id=MORPHO_BLUE_MARKET_ID,
        amount=wsteth_collateral_amount,
    )

    # Step 2: Calculate borrow amount using oracle prices
    wsteth_price = oracle.get_asset_price(BASE_WSTETH)
    weth_price = oracle.get_asset_price(BASE_WETH)

    # Add 0.5% buffer to cover Aerodrome swap fees so flash loan repayment succeeds
    weth_borrow_amount = int(
        wsteth_collateral_amount
        * ltv
        * wsteth_price.readable()
        / weth_price.readable()
        * 1.005
    )

    borrow = morpho_borrow.borrow(
        market_id=MORPHO_BLUE_MARKET_ID,
        amount=weth_borrow_amount,
    )

    # Step 3: Swap WETH -> WStETH via Aerodrome to repay the flash loan
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

    # Wrap everything in a flash loan
    flash_loan_amount = wsteth_collateral_amount - wsteth_balance
    flash_loan = morpho_flash.flash_loan(
        asset=BASE_WSTETH,
        amount=flash_loan_amount,
        actions=[supply_collateral, borrow, swap],
    )

    # Execute the full looping strategy in a single atomic transaction
    plasma_vault.execute([flash_loan])

    log.info(
        "Looping complete — deposited %.2f WStETH with %dx leverage",
        deposit_amount / 1e18,
        leverage,
    )

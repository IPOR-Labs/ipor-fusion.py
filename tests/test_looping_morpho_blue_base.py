import logging
import os

from web3 import Web3

from constants import ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.PlasmaVaultSystemFactory import PlasmaVaultSystemFactory
from ipor_fusion.Roles import Roles
from ipor_fusion.helpers import Addresses
from ipor_fusion.helpers.AerodromeSwapHelper import AerodromeSwapHelper
from ipor_fusion.types import Amount

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

fork_url = os.getenv("BASE_PROVIDER_URL")

anvil = AnvilTestContainerStarter(fork_url)
anvil.start()

# WStETH/WETH market on Morpho Blue (Base), LLTV = 94.5%
MORPHO_BLUE_MARKET_ID = (
    "3a4048c64ba1b375330d376b1ce40e4047d03b47ab4d48af484edec9fec801ba"
)


def _deal_erc20(web3, token, to, amount):
    """Set ERC20 balance by brute-forcing the balanceOf storage slot (slots 0-5).

    Standard ERC20 contracts store balances in a mapping(address => uint256).
    The storage slot for balances[to] is keccak256(abi.encode(to, slot_index)).
    We try slot indices 0 through 5, which covers most ERC20 implementations.
    """
    from eth_abi import encode as abi_encode

    for slot in range(6):
        storage_key = Web3.keccak(abi_encode(["address", "uint256"], [to, slot]))
        value = amount.to_bytes(32, "big")
        web3.manager.request_blocking(
            "anvil_setStorageAt",
            [token, "0x" + storage_key.hex(), "0x" + value.hex()],
        )
        balance_sig = bytes.fromhex("70a08231")
        balance_data = abi_encode(["address"], [to])
        result = web3.eth.call(
            {"to": token, "data": "0x" + (balance_sig + balance_data).hex()}
        )
        actual = int(result.hex(), 16)
        if actual == amount:
            return
    raise RuntimeError(f"Could not deal ERC20 {token} - tried slots 0-5")


def _setup_system(deposit_amount=int(50e18)):
    # Vault atomist (admin) on Base
    atomist = Web3.to_checksum_address("0xF6a9bd8F6DC537675D499Ac1CA14f2c55d8b5569")
    vault_address = Web3.to_checksum_address(
        "0xc4c00d8b323f37527eeda27c87412378be9f68ec"
    )
    # Address used as depositor — gets WStETH via _deal_erc20
    wsteth_holder = Web3.to_checksum_address(
        "0xf0bb20865277aBd641a307eCe5Ee04E79073416C"
    )

    system = PlasmaVaultSystemFactory(
        provider_url=anvil.get_anvil_http_url(),
        private_key=ANVIL_WALLET_PRIVATE_KEY,
    ).get(vault_address)

    system.cheater(atomist).access_manager().grant_role(
        Roles.ALPHA_ROLE, system.alpha(), 0
    )
    system.cheater(atomist).access_manager().grant_role(
        Roles.WHITELIST_ROLE, wsteth_holder, 0
    )

    # Deal WStETH to the holder (2x deposit so they keep some after depositing)
    _deal_erc20(
        web3=system.transaction_executor().get_web3(),
        token=Addresses.BASE_WSTETH,
        to=wsteth_holder,
        amount=deposit_amount * 2,
    )

    system.cheater(wsteth_holder).erc20(Addresses.BASE_WSTETH).approve(
        spender=system.plasma_vault().address(),
        amount=deposit_amount,
    )
    system.cheater(wsteth_holder).plasma_vault().deposit(
        assets=deposit_amount,
        receiver=wsteth_holder,
    )

    return system, vault_address


def test_looping_morpho_blue():
    """Leveraged looping strategy on Morpho Blue via flash loan.

    Strategy (executed atomically inside a Morpho flash loan):
      1. Flash loan WStETH from Morpho
      2. Supply all WStETH (deposited + flash loaned) as collateral to Morpho Blue market
      3. Borrow WETH against the collateral
      4. Swap borrowed WETH -> WStETH via Aerodrome to repay the flash loan

    Note: The vault may already have an existing leveraged Morpho position at the
    current block. We use conservative parameters (2x leverage, 0.7 LTV) to ensure
    the additional borrow stays within the market's 94.5% LLTV when combined with
    the existing position.
    """
    system, vault_address = _setup_system(deposit_amount=int(50e18))

    # Read current Morpho position — vault may already be leveraged
    existing_position = system.morpho().position(
        chain_id=8453,
        morpho_blue_market_id=MORPHO_BLUE_MARKET_ID,
    )
    log.info(
        f"Existing position - collateral: {existing_position.collateral_amount / 1e18:.2f} WStETH, "
        f"borrow: {existing_position.borrow_amount / 1e18:.2f} WETH"
    )

    # 10x leverage, LTV = 90% — market LLTV is 94.5%
    leverage = 10
    ltv = 1 - 1 / leverage

    wsteth_balance = system.erc20(Addresses.BASE_WSTETH).balance_of(vault_address)
    # Total collateral to supply = vault's free WStETH * leverage
    wsteth_collateral_amount = wsteth_balance * leverage

    # Step 1: Supply collateral action
    supply_collateral = system.morpho().supply_collateral(
        market_id=MORPHO_BLUE_MARKET_ID,
        amount=wsteth_collateral_amount,
    )

    # Step 2: Calculate borrow amount using oracle prices
    wsteth_price = system.price_oracle_middleware().get_asset_price(
        Addresses.BASE_WSTETH
    )
    weth_price = system.price_oracle_middleware().get_asset_price(Addresses.BASE_WETH)

    weth_borrow_amount = int(
        wsteth_collateral_amount * ltv * wsteth_price.readable() / weth_price.readable()
    )

    borrow = system.morpho().borrow(
        market_id=MORPHO_BLUE_MARKET_ID,
        amount=weth_borrow_amount,
    )

    # Step 3: Swap WETH -> WStETH via Aerodrome to repay the flash loan
    web3 = system.transaction_executor().get_web3()
    latest_block = web3.eth.get_block(web3.eth.block_number)

    swap = AerodromeSwapHelper.create_aerodrome_swap_exact_input(
        system=system,
        token_in=Addresses.BASE_WETH,
        token_out=Addresses.BASE_WSTETH,
        amount_in=weth_borrow_amount,
        min_amount_out=0,
        deadline=latest_block.timestamp + 1000,
    )

    # Wrap everything in a flash loan — borrow (collateral_amount - vault_balance) WStETH
    flash_loan = system.morpho().flash_loan(
        amount=Amount(wsteth_collateral_amount - wsteth_balance),
        asset_address=Addresses.BASE_WSTETH,
        actions=[
            supply_collateral,
            borrow,
            swap,
        ],
    )

    # Execute the full looping strategy in a single atomic transaction
    system.plasma_vault().execute([flash_loan])

    morpho_position = system.morpho().position(
        chain_id=8453,
        morpho_blue_market_id=MORPHO_BLUE_MARKET_ID,
    )

    log.info(
        f"Final position - collateral: {morpho_position.collateral_amount / 1e18:.2f} WStETH, "
        f"borrow: {morpho_position.borrow_amount / 1e18:.2f} WETH"
    )

    assert (
        morpho_position.collateral_amount > existing_position.collateral_amount
    ), "Should have more collateral after looping"
    assert (
        morpho_position.borrow_amount > existing_position.borrow_amount
    ), "Should have more debt after looping"

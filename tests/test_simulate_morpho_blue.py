import logging

from web3 import Web3

from _simulate import assert_all_success
from addresses import ETHEREUM_USDC
from constants import ETHEREUM_MORPHO_SUPPLY_FUSE
from ipor_fusion import (
    Web3Context,
    PlasmaVault,
    ERC20,
    VaultSimulator,
)
from ipor_fusion.fuses import MorphoSupplyFuse
from ipor_fusion.types import ChainId, MorphoBlueMarketId, Amount

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

VAULT = Web3.to_checksum_address("0x43Ee0243eA8CF02f7087d8B16C8D2007CC9c7cA2")
ALPHA = Web3.to_checksum_address("0x6d3BE3f86FB1139d0c9668BD552f05fcB643E6e6")
MARKET_ID = MorphoBlueMarketId(
    "3a85e619751152991742810df6ec69ce473daef99e28a64ab2340d7b7ccfee49"
)
SUPPLY_AMOUNT = Amount(1000_000000)
PINNED_BLOCK = 22066578  # mirrors anvil.reset_fork(...) in test_morpho_blue.py


def test_simulate_supply_and_withdraw_morpho_blue(web3_eth):
    ctx = Web3Context(web3=web3_eth, chain_id=ChainId(web3_eth.eth.chain_id))
    ctx.default_block = PINNED_BLOCK

    plasma_vault = PlasmaVault(ctx, VAULT)
    usdc = ERC20(ctx, ETHEREUM_USDC)

    morpho = MorphoSupplyFuse(ETHEREUM_MORPHO_SUPPLY_FUSE)
    supply_action = morpho.supply(market_id=MARKET_ID, amount=SUPPLY_AMOUNT)
    withdraw_action = morpho.withdraw(market_id=MARKET_ID, amount=SUPPLY_AMOUNT)

    sim = VaultSimulator(
        web3=web3_eth, vault=VAULT, alpha=ALPHA, block=hex(PINNED_BLOCK)
    )

    sim.observe("usdc_before", usdc.balance_of(VAULT))
    sim.execute([supply_action])
    sim.observe("usdc_after_supply", usdc.balance_of(VAULT))
    sim.execute([withdraw_action])
    sim.observe("usdc_after_withdraw", usdc.balance_of(VAULT))
    sim.observe("total_assets", plasma_vault.total_assets())

    result = sim.run()

    log.info(
        "simulation result: success=%s gas_used=%s", result.success, result.gas_used
    )
    log.info("revert_reason: %s", result.revert_reason)
    log.info("observations: %s", result.observations)

    assert_all_success(result)

    usdc_before = result.get("usdc_before")
    usdc_after_supply = result.get("usdc_after_supply")
    usdc_after_withdraw = result.get("usdc_after_withdraw")

    assert usdc_before - usdc_after_supply == SUPPLY_AMOUNT
    assert usdc_after_withdraw - usdc_after_supply > 999_000000
    assert result.get("total_assets") > 0


def test_simulate_revert_surfaces_reason(web3_eth):
    """Calling execute() from a non-alpha address must revert; reason is decoded."""
    morpho = MorphoSupplyFuse(ETHEREUM_MORPHO_SUPPLY_FUSE)
    supply_action = morpho.supply(market_id=MARKET_ID, amount=SUPPLY_AMOUNT)

    not_alpha = Web3.to_checksum_address("0x000000000000000000000000000000000000dEaD")
    sim = VaultSimulator(
        web3=web3_eth, vault=VAULT, alpha=not_alpha, block=hex(PINNED_BLOCK)
    )
    sim.execute([supply_action])
    result = sim.run()

    log.info("decoded revert: %s", result.revert_reason)
    assert not result.success
    assert result.revert_reason is not None

import logging
import os

import pytest
from eth_typing import BlockNumber
from web3 import Web3

from addresses import ETHEREUM_USDC
from constants import ETHEREUM_MORPHO_SUPPLY_FUSE
from ipor_fusion.testing import AnvilTestContainerStarter, ForkedWeb3Context
from ipor_fusion import PlasmaVault, ERC20
from ipor_fusion.fuses import MorphoSupplyFuse
from ipor_fusion.types import MorphoBlueMarketId, Amount

logging.basicConfig(level=logging.INFO)
log = logging.getLogger(__name__)

fork_url = os.environ["ETHEREUM_PROVIDER_URL"]


@pytest.fixture(scope="module")
def anvil():
    with AnvilTestContainerStarter(fork_url) as a:
        yield a


def test_should_deposit_and_withdraw_from_morpho_blue(anvil):
    anvil.reset_fork(BlockNumber(22066578))

    vault_address = Web3.to_checksum_address(
        "0x43Ee0243eA8CF02f7087d8B16C8D2007CC9c7cA2"
    )
    alpha_address = Web3.to_checksum_address(
        "0x6d3BE3f86FB1139d0c9668BD552f05fcB643E6e6"
    )
    morpho_blue_market_id = MorphoBlueMarketId(
        "3a85e619751152991742810df6ec69ce473daef99e28a64ab2340d7b7ccfee49"
    )

    forked_ctx = ForkedWeb3Context.from_url(anvil.get_anvil_http_url())
    plasma_vault = PlasmaVault(forked_ctx, vault_address)
    forked_ctx.prank(alpha_address)

    morpho = MorphoSupplyFuse(ETHEREUM_MORPHO_SUPPLY_FUSE)

    amount = Amount(1000_000000)

    supply = morpho.supply(market_id=morpho_blue_market_id, amount=amount)

    usdc = ERC20(forked_ctx, ETHEREUM_USDC)
    usdc_balance_of_before_supply = usdc.balance_of(vault_address)

    plasma_vault.execute([supply])

    usdc_balance_of_after_supply = usdc.balance_of(vault_address)

    assert usdc_balance_of_before_supply - usdc_balance_of_after_supply == amount

    withdraw = morpho.withdraw(market_id=morpho_blue_market_id, amount=amount)

    plasma_vault.execute([withdraw])

    usdc_balance_of_after_withdraw = usdc.balance_of(vault_address)

    assert usdc_balance_of_after_withdraw - usdc_balance_of_after_supply > 999_000000

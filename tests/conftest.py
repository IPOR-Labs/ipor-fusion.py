import logging

import pytest
from eth_account import Account

from UniswapV3UniversalRouter import UniswapV3UniversalRouter
from constants import ARBITRUM, ANVIL_WALLET_PRIVATE_KEY
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.TransactionExecutor import TransactionExecutor

logger = logging.getLogger(__name__)


@pytest.fixture(scope="module", name="anvil")
def anvil_fixture():
    logging.basicConfig(level=logging.DEBUG)
    container = AnvilTestContainerStarter()
    container.start()
    return container


@pytest.fixture(scope="module", name="web3")
def web3_fixture(anvil):
    client = anvil.get_client()
    logger.info("Connected to Ethereum network with chain ID: %s", anvil.get_chain_id())
    logger.info("Anvil HTTP URL: %s", anvil.get_anvil_http_url())
    return client


@pytest.fixture(scope="module", name="account")
def account_fixture():
    # pylint: disable=no-value-for-parameter
    return Account.from_key(ANVIL_WALLET_PRIVATE_KEY)


@pytest.fixture(scope="module", name="transaction_executor")
def transaction_executor_fixture(web3, account) -> TransactionExecutor:
    return TransactionExecutor(web3, account)


@pytest.fixture(scope="module", name="uniswap_v3_universal_router")
def uniswap_v3_universal_router_fixture(
    transaction_executor,
) -> UniswapV3UniversalRouter:
    return UniswapV3UniversalRouter(
        transaction_executor=transaction_executor,
        universal_router_address=ARBITRUM.UNISWAP.V3.UNIVERSAL_ROUTER,
    )

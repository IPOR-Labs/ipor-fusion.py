import logging

import pytest

import constants
import ipor_fusion.ERC20
from ipor_fusion.AnvilTestContainerStarter import AnvilTestContainerStarter
from ipor_fusion.TestTransactionExecutor import TestTransactionExecutor
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
    return constants.ANVIL_WALLET_PRIVATE_KEY


@pytest.fixture(scope="module", name="transaction_executor")
def transaction_executor_fixture(web3, account) -> TransactionExecutor:
    return TransactionExecutor(web3, account)


@pytest.fixture(scope="module", name="test_transaction_executor")
def test_transaction_executor_fixture(web3, account) -> TestTransactionExecutor:
    return TestTransactionExecutor(web3, account)


@pytest.fixture(scope="module", name="usdc")
def usdc_fixture(transaction_executor):
    return ipor_fusion.ERC20.ERC20(transaction_executor, constants.ARBITRUM.USDC)


@pytest.fixture(scope="module", name="usdt")
def usdt_fixture(transaction_executor):
    return ipor_fusion.ERC20.ERC20(transaction_executor, constants.ARBITRUM.USDT)


@pytest.fixture(scope="module", name="ram")
def ram_fixture(transaction_executor):
    return ipor_fusion.ERC20.ERC20(
        transaction_executor, constants.ARBITRUM.RAMSES.V2.RAM
    )

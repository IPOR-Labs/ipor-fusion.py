import logging

import pytest

from anvil_container import AnvilTestContainerStarter
from constants import (
    ANVIL_WALLET_PRIVATE_KEY,
    ARBITRUM,
)
from ipor_fusion.ERC20 import ERC20
from ipor_fusion.TransactionExecutor import TransactionExecutor


@pytest.fixture(scope="module", name="anvil")
def anvil_fixture():
    logging.basicConfig(level=logging.DEBUG)
    container = AnvilTestContainerStarter()
    container.start()
    return container


@pytest.fixture(scope="module", name="web3")
def web3_fixture(anvil):
    client = anvil.get_client()
    print(f"Connected to Ethereum network with chain ID: {anvil.get_chain_id()}")
    print(f"Anvil HTTP URL: {anvil.get_anvil_http_url()}")
    return client


@pytest.fixture(scope="module", name="account")
def account_fixture():
    return ANVIL_WALLET_PRIVATE_KEY


@pytest.fixture(scope="module", name="transaction_executor")
def transaction_executor_fixture(web3, account) -> TransactionExecutor:
    return TransactionExecutor(web3, account)


@pytest.fixture(scope="module", name="usdc")
def usdc_fixture(transaction_executor):
    return ERC20(transaction_executor, ARBITRUM.USDC)


@pytest.fixture(scope="module", name="ram")
def ram_fixture(transaction_executor):
    return ERC20(transaction_executor, ARBITRUM.RAMSES.V2.RAM)

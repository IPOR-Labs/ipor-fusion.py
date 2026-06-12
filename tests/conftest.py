import os

import pytest
from dotenv import load_dotenv
from web3 import Web3

from ipor_fusion import is_simulate_v1_supported

load_dotenv()


def pytest_collection_modifyitems(items):
    for item in items:
        path = str(item.fspath)
        if "test_cli_" in path:
            item.add_marker(pytest.mark.cli)
        elif "test_mcp_" in path:
            item.add_marker(pytest.mark.mcp)
        else:
            item.add_marker(pytest.mark.sdk)


@pytest.fixture(autouse=True)
def _isolate_keeper_env(monkeypatch):
    """Disable keeper auth by default so offline tests never hit the network.

    `vault info` fetches alpha config via `KeeperClient.from_env()`, which only
    attempts a network call when `FUSION_PRIVATE_KEY` is set. `load_dotenv()`
    above can pull a developer's key into the session, which would make the
    offline CLI/MCP vault-info tests reach the real keeper. Removing it here
    makes "keeper off" the default; tests that exercise auth opt back in with
    `monkeypatch.setenv(...)`, which runs after this fixture and wins.
    """
    monkeypatch.delenv("FUSION_PRIVATE_KEY", raising=False)


def _connected_web3(env_var: str) -> Web3:
    """Build a Web3 client from `env_var`; skip the test if missing/unreachable."""
    url = os.environ.get(env_var)
    if not url:
        pytest.skip(f"{env_var} not set")
    w3 = Web3(Web3.HTTPProvider(url))
    if not w3.is_connected():
        pytest.skip(f"cannot reach RPC at {env_var}")
    return w3


def _ensure_simulate_v1(web3: Web3) -> None:
    """Skip if the provider does not implement `eth_simulateV1`."""
    if not is_simulate_v1_supported(web3):
        pytest.skip("provider does not support eth_simulateV1")


@pytest.fixture(scope="session")
def web3_eth() -> Web3:
    w3 = _connected_web3("ETHEREUM_PROVIDER_URL")
    _ensure_simulate_v1(w3)
    return w3


@pytest.fixture(scope="session")
def web3_base() -> Web3:
    w3 = _connected_web3("BASE_PROVIDER_URL")
    _ensure_simulate_v1(w3)
    return w3


@pytest.fixture(scope="session")
def web3_arb() -> Web3:
    w3 = _connected_web3("ARBITRUM_PROVIDER_URL")
    _ensure_simulate_v1(w3)
    return w3

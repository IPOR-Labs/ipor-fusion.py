import importlib.util
from collections.abc import Callable
from pathlib import Path
from types import ModuleType
from unittest.mock import patch

import pytest
from _live import provider_url, unavailable
from _multicall import SequentialMulticall
from _rpc_cache import http_provider
from dotenv import load_dotenv
from web3 import Web3

from ipor_fusion import is_simulate_v1_supported

load_dotenv()

_EXAMPLES = Path(__file__).resolve().parent.parent / "examples"


@pytest.fixture
def load_example() -> Callable[[str], ModuleType]:
    """Import an example module by filename, straight from examples/."""

    def _load(module_filename: str) -> ModuleType:
        path = _EXAMPLES / module_filename
        spec = importlib.util.spec_from_file_location(path.stem, path)
        assert spec and spec.loader
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        return module

    return _load


@pytest.fixture(autouse=True, scope="session")
def _fuse_signatures_are_declared():
    """Every call an encoder builds anywhere in the suite must be one its class
    declares in ``SIGNATURES``, so the action decoder can name it."""
    from ipor_fusion.core.fuse import Fuse

    encode = Fuse._action_raw

    def checked(self, signature, values):
        assert signature in type(self).signatures(), (
            f"{type(self).__name__} encodes {signature}, missing from its SIGNATURES"
        )
        return encode(self, signature, values)

    with patch.object(Fuse, "_action_raw", checked):
        yield


@pytest.fixture
def sequential_multicall():
    """Run the vault fetch's batched reads one `Call` at a time, for tests that
    mock contract wrappers (`.call.return_value`) instead of raw eth_call data."""
    with (
        patch("ipor_fusion.vault_managers.Multicall3", SequentialMulticall),
        patch("ipor_fusion.vault_positions.Multicall3", SequentialMulticall),
        patch("ipor_fusion.vault_data.Multicall3", SequentialMulticall),
        patch("ipor_fusion.vault_erc20.Multicall3", SequentialMulticall),
    ):
        yield


def pytest_collection_modifyitems(config, items):
    for item in items:
        path = str(item.fspath)
        if "test_cli_" in path:
            item.add_marker(pytest.mark.cli)
        elif "test_mcp_" in path:
            item.add_marker(pytest.mark.mcp)
        elif "test_examples_" in path:
            item.add_marker(pytest.mark.examples)
        else:
            item.add_marker(pytest.mark.sdk)


def _connected_web3(env_var: str) -> Web3:
    """Build a Web3 client from `env_var`; skip the test if missing/unreachable
    (fail when the run requires it, see `_live`)."""
    w3 = Web3(http_provider(provider_url(env_var)))
    # web3's validation middleware reads eth_chainId before and after every
    # eth_call to check a transaction's chainId; these clients only read and
    # simulate, so drop it, as Web3Context.from_url does.
    w3.middleware_onion.remove("validation")
    if not w3.is_connected():
        unavailable(env_var, f"cannot reach RPC at {env_var}")
    return w3


def _ensure_simulate_v1(web3: Web3, env_var: str) -> None:
    """Skip if the provider does not implement `eth_simulateV1`."""
    if not is_simulate_v1_supported(web3):
        unavailable(env_var, f"{env_var} does not support eth_simulateV1")


@pytest.fixture(scope="session")
def web3_eth() -> Web3:
    w3 = _connected_web3("ETHEREUM_PROVIDER_URL")
    _ensure_simulate_v1(w3, "ETHEREUM_PROVIDER_URL")
    return w3


@pytest.fixture(scope="session")
def web3_base() -> Web3:
    w3 = _connected_web3("BASE_PROVIDER_URL")
    _ensure_simulate_v1(w3, "BASE_PROVIDER_URL")
    return w3


@pytest.fixture(scope="session")
def web3_arb() -> Web3:
    w3 = _connected_web3("ARBITRUM_PROVIDER_URL")
    _ensure_simulate_v1(w3, "ARBITRUM_PROVIDER_URL")
    return w3


@pytest.fixture(scope="session")
def web3_hyperevm() -> Web3:
    """HyperEVM simulation fixture; general PR CI does not supply its RPC secret."""
    w3 = _connected_web3("HYPEREVM_PROVIDER_URL")
    _ensure_simulate_v1(w3, "HYPEREVM_PROVIDER_URL")
    return w3

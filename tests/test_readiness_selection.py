"""Readiness cannot leak into regressions through explicit marker selection."""

from pathlib import Path
from unittest.mock import MagicMock, PropertyMock

import _crosschain as readiness
import pytest

pytest_plugins = ["pytester"]


@pytest.mark.parametrize(
    ("args", "passed", "deselected"),
    [
        ([], 3, 1),
        (["-m", "sdk"], 1, 3),
        (["-m", "cli or mcp"], 2, 2),
        (["--run-readiness", "-m", "readiness"], 1, 3),
        (["--run-readiness"], 4, 0),
    ],
)
def test_readiness_requires_explicit_opt_in(pytester, args, passed, deselected):
    pytester.makeconftest(Path(__file__).with_name("conftest.py").read_text())
    pytester.makeini("[pytest]\nmarkers =\n    sdk\n    cli\n    mcp\n    readiness\n")
    pytester.makepyfile(
        test_sdk_example="def test_sdk(): pass",
        test_cli_example="def test_cli(): pass",
        test_mcp_example="def test_mcp(): pass",
        test_readiness_example=(
            "import pytest\npytestmark = pytest.mark.readiness\ndef test_live(): pass\n"
        ),
    )
    result = pytester.runpytest_subprocess("-q", *args)
    result.assert_outcomes(passed=passed, deselected=deselected)


def test_readiness_missing_provider_fails_instead_of_skipping(monkeypatch):
    monkeypatch.delenv("ARBITRUM_PROVIDER_URL", raising=False)
    with pytest.raises(
        pytest.fail.Exception, match="readiness requires ARBITRUM_PROVIDER_URL"
    ):
        readiness.connect_readiness(readiness.CHAINS["arbitrum"])


def _mock_provider(monkeypatch):
    monkeypatch.setenv("ARBITRUM_PROVIDER_URL", "https://rpc.invalid/private-key")
    web3 = MagicMock()
    constructor = MagicMock(return_value=web3)
    monkeypatch.setattr(readiness, "Web3", constructor)
    return web3


def test_readiness_rpc_failure_does_not_expose_provider(monkeypatch):
    web3 = _mock_provider(monkeypatch)
    web3.eth.chain_id = 42161
    type(web3.eth).block_number = PropertyMock(
        side_effect=RuntimeError("https://rpc.invalid/private-key")
    )
    with pytest.raises(pytest.fail.Exception) as caught:
        readiness.connect_readiness(readiness.CHAINS["arbitrum"])
    assert "RuntimeError" in str(caught.value)
    assert "private-key" not in str(caught.value)


def test_readiness_rejects_wrong_chain(monkeypatch):
    web3 = _mock_provider(monkeypatch)
    web3.eth.chain_id = 1
    web3.eth.block_number = 123
    with pytest.raises(pytest.fail.Exception, match="serves chain 1, expected 42161"):
        readiness.connect_readiness(readiness.CHAINS["arbitrum"])


def test_readiness_pins_reads_without_simulate_v1_probe(monkeypatch):
    web3 = _mock_provider(monkeypatch)
    web3.eth.chain_id = 42161
    web3.eth.block_number = 123
    ctx = readiness.connect_readiness(readiness.CHAINS["arbitrum"])
    assert ctx.default_block == 123
    assert ctx.web3 is web3
    web3.provider.make_request.assert_not_called()

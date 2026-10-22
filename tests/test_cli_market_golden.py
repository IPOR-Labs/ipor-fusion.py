"""`fusion market ... --json` and the MCP market tools pinned byte for byte.

The Morpho API answers are recorded from the public endpoint (trimmed to a few
vaults, allocations and caps) in `tests/fixtures/market_golden/morpho_api/`;
the Morpho Blue reads answer from a fixed table. The CLI's `--json` output
(`<scenario>.json`) and the MCP tool's text (`<scenario>.mcp.json`) live in
`tests/fixtures/market_golden/`; regenerate them with
`UPDATE_GOLDEN=1 uv run pytest tests/test_cli_market_golden.py` only when an
output change is intended (see `tests/_golden.py`).
"""

from __future__ import annotations

import json
from collections.abc import Iterator
from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest
from _golden import FIXTURES as GOLDEN_FIXTURES
from _golden import assert_golden, mcp_tool_text
from _multicall import multicall_aware
from click.testing import CliRunner
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector
from pydantic import BaseModel
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion.cache import NullCache
from ipor_fusion.cli import config_store
from ipor_fusion.cli.config_store import FusionConfig, save_config
from ipor_fusion.cli.main import cli
from ipor_fusion.core.context import Web3Context
from ipor_fusion.mcp.models import MetaMorphoVaultResponse, MorphoBlueMarketResponse
from ipor_fusion.readers.morpho import morpho_blue_address
from ipor_fusion.types import ChainId

FIXTURES = GOLDEN_FIXTURES / "market_golden"
API = FIXTURES / "morpho_api"

CHAIN = ChainId(1)
MARKET_ID = "0xb323495f7e4148be5643a4ea4a8221eef163e4bccfdedc2a6f4696baacbc86cc"
VAULT_V1 = "0xBEEF01735c132Ada46AA9aA4c54623cAA92A64CB"
VAULT_V2 = "0xB885F6d448dA7E2C642Ec31190B629E40E87B069"

LOAN = "0xA0b86991c6218b36c1d19D4a2e9Eb0cE3606eB48"
COLLATERAL = "0x7f39C581F595B53c5cb19bD0b3f8dA6c935E2Ca0"
ORACLE = "0x48F7E36EB6B826B2dF4B2E630B62Cd25e89E40e2"
IRM = "0x870aC11D48B15DB9a138Cf899d20F13F79Ba00BC"
LLTV = 860_000_000_000_000_000
# Market state consistent with the recorded API answer; the IRM rate gives the
# API's ~4.98% borrow APY.
MARKET_STATE = (
    32_599_187_714_224,
    31_412_838_216_773_190_381,
    29_365_507_783_557,
    27_812_093_005_448_210_559,
    1_792_584_383,
    0,
)
BORROW_RATE_PER_SECOND_WAD = 1_540_542_212


def _selector(signature: str) -> bytes:
    return function_signature_to_4byte_selector(signature)


def _morpho_blue(to: str, data: bytes) -> bytes:
    """The Morpho Blue singleton and the market's IRM, at the recorded state."""
    to = Web3.to_checksum_address(to)
    selector = bytes(data[:4])
    if to == morpho_blue_address(CHAIN):
        if selector == _selector("idToMarketParams(bytes32)"):
            return encode(
                ["address", "address", "address", "address", "uint256"],
                [LOAN, COLLATERAL, ORACLE, IRM, LLTV],
            )
        if selector == _selector("market(bytes32)"):
            return encode(["uint128"] * 6, list(MARKET_STATE))
    if to == IRM and selector == _selector(
        "borrowRateView((address,address,address,address,uint256),"
        "(uint128,uint128,uint128,uint128,uint128,uint128))"
    ):
        return encode(["uint256"], [BORROW_RATE_PER_SECOND_WAD])
    raise ContractLogicError(f"unexpected call to {to} 0x{selector.hex()}")


def _answer(body: bytes) -> MagicMock:
    response = MagicMock()
    response.read.return_value = body
    opened = MagicMock()
    opened.__enter__.return_value = response
    return opened


@dataclass(frozen=True)
class Scenario:
    """One golden: the CLI arguments, the MCP tool call, and the Morpho API
    answers both receive in order (``None`` is an unreachable API)."""

    cli_args: tuple[str, ...]
    tool: str
    arguments: dict[str, object]
    model: type[BaseModel]
    api: tuple[str | None, ...]


SCENARIOS = {
    "morpho_blue": Scenario(
        ("morpho-blue", MARKET_ID, "--chain", "1", "--json"),
        "market_morpho_blue",
        {"market_id": MARKET_ID, "chain_id": 1},
        MorphoBlueMarketResponse,
        ("market",),
    ),
    "morpho_blue_api_error": Scenario(
        ("morpho-blue", MARKET_ID, "--chain", "1", "--json"),
        "market_morpho_blue",
        {"market_id": MARKET_ID, "chain_id": 1},
        MorphoBlueMarketResponse,
        (None,),
    ),
    "morpho_blue_no_api": Scenario(
        ("morpho-blue", MARKET_ID, "--chain", "1", "--no-api", "--json"),
        "market_morpho_blue",
        {"market_id": MARKET_ID, "chain_id": 1, "no_api": True},
        MorphoBlueMarketResponse,
        (),
    ),
    "meta_morpho_v1": Scenario(
        ("meta-morpho", VAULT_V1, "--chain", "1", "--json"),
        "market_meta_morpho",
        {"vault_address": VAULT_V1, "chain_id": 1},
        MetaMorphoVaultResponse,
        ("vault_v2_not_found", "vault_v1"),
    ),
    "meta_morpho_v2": Scenario(
        ("meta-morpho", VAULT_V2, "--chain", "1", "--json"),
        "market_meta_morpho",
        {"vault_address": VAULT_V2, "chain_id": 1},
        MetaMorphoVaultResponse,
        ("vault_v2",),
    ),
}


@pytest.fixture(autouse=True)
def recorded_chain(tmp_path, monkeypatch) -> Iterator[None]:
    """A saved provider whose context answers from `_morpho_blue`."""
    monkeypatch.setattr(config_store, "CONFIG_DIR", tmp_path / ".fusion")
    monkeypatch.setattr(config_store, "CONFIG_FILE", tmp_path / ".fusion" / "c.json")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / ".cache"))
    save_config(FusionConfig(providers={"1": "https://rpc.example.invalid"}))
    ctx = Web3Context(web3=MagicMock(), chain_id=CHAIN, cache=NullCache())
    ctx.call = multicall_aware(_morpho_blue)  # type: ignore[method-assign]
    monkeypatch.setattr(Web3Context, "from_url", staticmethod(lambda *_, **__: ctx))
    yield


def _serve_api(monkeypatch: pytest.MonkeyPatch, answers: tuple[str | None, ...]):
    """Answer the Morpho API requests in order from the recorded bodies."""
    replies: list[MagicMock | OSError] = [
        OSError("connection refused")
        if name is None
        else _answer((API / f"{name}.json").read_bytes())
        for name in answers
    ]
    urlopen = MagicMock(side_effect=replies)
    monkeypatch.setattr("ipor_fusion.morpho_api.urlopen", urlopen)
    return urlopen


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_cli_json_matches_fixture(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario = SCENARIOS[name]
    urlopen = _serve_api(monkeypatch, scenario.api)

    result = CliRunner().invoke(cli, ["market", *scenario.cli_args])

    assert result.exit_code == 0, result.output
    assert urlopen.call_count == len(scenario.api)
    assert_golden(FIXTURES / f"{name}.json", result.output)
    json.loads(result.output)


@pytest.mark.parametrize("name", sorted(SCENARIOS))
def test_mcp_text_matches_fixture(name: str, monkeypatch: pytest.MonkeyPatch) -> None:
    scenario = SCENARIOS[name]
    urlopen = _serve_api(monkeypatch, scenario.api)

    text = mcp_tool_text(scenario.tool, scenario.arguments, scenario.model)

    assert urlopen.call_count == len(scenario.api)
    assert_golden(FIXTURES / f"{name}.mcp.json", text)
    cli_document = json.loads((FIXTURES / f"{name}.json").read_text("utf-8"))
    assert scenario.model.model_validate(cli_document).model_dump_json(indent=2) == (
        text
    )

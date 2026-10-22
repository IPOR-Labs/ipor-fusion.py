"""`fusion vault info --json`, the MCP `vault_info` tool and
`build_vault_info_json` end to end: `fetch_vault_data` runs unpatched over a
fake JSON-RPC chain (`_fake_rpc.FakeRpc`), so the golden pins what the real
fetch orchestration assembles, not a hand-built `VaultData`.

The vault holds idle USDC and one ERC4626 market, has a fee account and a
WithdrawManager; every other read answers as a contract without it would
(revert), and the log scans find no events. Regenerate the goldens with
`UPDATE_GOLDEN=1` only when an output change is intended (`tests/_golden.py`).
"""

from __future__ import annotations

import json
from collections.abc import Callable
from unittest.mock import patch

import pytest
from _fake_rpc import FakeRpc
from _golden import FIXTURES, FrozenDatetime, assert_golden, mcp_tool_text
from click.testing import CliRunner
from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.exceptions import ContractLogicError

from ipor_fusion import (
    IporFusionMarkets,
    NullCache,
    Web3Context,
    build_vault_info_json,
    fetch_vault_data,
    vault_info,
)
from ipor_fusion.cli import config_store, vault_rendering
from ipor_fusion.cli.config_store import FusionConfig, save_config
from ipor_fusion.cli.main import cli
from ipor_fusion.mcp.models import VaultInfoResponse
from ipor_fusion.types import ChainId, parse_plasma_vault_address

GOLDEN = FIXTURES / "vault_info_golden"
BLOCK = 1_000
TIMESTAMP = 1_790_000_000
USDC = 10**6


def _addr(suffix: str) -> str:
    return Web3.to_checksum_address("0x" + suffix.rjust(40, "0"))


VAULT = parse_plasma_vault_address(_addr("7a017"))
ASSET = _addr("05dc")
ORACLE = _addr("0c1e")
ACCESS = _addr("acce55")
FUSE = _addr("f00e")
BALANCE_FUSE = _addr("ba1f")
TARGET_VAULT = _addr("4626")
FEE_ACCOUNT = _addr("feeacc")
FEE_MANAGER = _addr("feeaa9")
DAO = _addr("da0")
WITHDRAW_MANAGER = _addr("3d")
MARKET = IporFusionMarkets.ERC4626_0001

# PlasmaVault storage (ERC-7201): the balance fuse per market and the
# WithdrawManager, as `PlasmaVault.read_balance_fuses` and
# `read_withdraw_manager_address` read them.
_BALANCE_FUSES_SLOT = 0x150144DD6AF711BAC4392499881EC6649090601BD196A5ECE5174C1400B1F700
_WITHDRAW_MANAGER_SLOT = (
    0x465D2FF0062318FE6F4C7E9AC78CFCD70BC86A1D992722875EF83A9770513100
)


def _word(address: str) -> bytes:
    return bytes(12) + bytes.fromhex(address[2:])


STORAGE = {
    (
        VAULT,
        int.from_bytes(
            Web3.keccak(encode(["uint256", "uint256"], [MARKET, _BALANCE_FUSES_SLOT]))
        ),
    ): _word(BALANCE_FUSE),
    (VAULT, _WITHDRAW_MANAGER_SLOT): _word(WITHDRAW_MANAGER),
}


def _uint(value: int) -> bytes:
    return encode(["uint256"], [value])


def _address(value: str) -> bytes:
    return encode(["address"], [value])


Answer = bytes | Callable[[bytes], bytes]

CHAIN: dict[tuple[str, str], Answer] = {
    (VAULT, "decimals()"): _uint(8),
    (VAULT, "totalAssets()"): _uint(1_250 * USDC),
    (VAULT, "totalSupply()"): _uint(1_200 * 10**8),
    (VAULT, "asset()"): _address(ASSET),
    (VAULT, "getAccessManagerAddress()"): _address(ACCESS),
    (VAULT, "getFuses()"): encode(["address[]"], [[FUSE]]),
    (VAULT, "getInstantWithdrawalFuses()"): encode(["address[]"], [[FUSE]]),
    (VAULT, "name()"): encode(["string"], ["IPOR USDC Fetched"]),
    (VAULT, "getRewardsClaimManagerAddress()"): _address("0x" + "00" * 20),
    (VAULT, "getTotalSupplyCap()"): _uint(10**30),
    (VAULT, "getPriceOracleMiddleware()"): _address(ORACLE),
    (VAULT, "getActiveMarketsInBalanceFuses()"): encode(["uint256[]"], [[MARKET]]),
    (VAULT, "getMarketSubstrates(uint256)"): encode(
        ["bytes32[]"], [[_word(TARGET_VAULT)]]
    ),
    (VAULT, "getDependencyBalanceGraph(uint256)"): encode(["uint256[]"], [[]]),
    (VAULT, "totalAssetsInMarket(uint256)"): _uint(1_000 * USDC),
    (VAULT, "convertToAssets(uint256)"): lambda args: _uint(
        decode(["uint256"], args)[0] // 100
    ),
    (VAULT, "getPerformanceFeeData()"): encode(
        ["(address,uint16)"], [(FEE_ACCOUNT, 1_000)]
    ),
    (VAULT, "getManagementFeeData()"): encode(
        ["(address,uint16,uint32)"], [(FEE_ACCOUNT, 200, TIMESTAMP - 86_400)]
    ),
    (VAULT, "getUnrealizedManagementFee()"): _uint(3 * USDC),
    (ASSET, "decimals()"): _uint(6),
    (ASSET, "symbol()"): encode(["string"], ["USDC"]),
    (ASSET, "balanceOf(address)"): _uint(250 * USDC),
    (ORACLE, "getAssetPrice(address)"): encode(["uint256", "uint256"], [10**8, 8]),
    (FUSE, "MARKET_ID()"): _uint(MARKET),
    (BALANCE_FUSE, "MARKET_ID()"): _uint(MARKET),
    (FEE_ACCOUNT, "FEE_MANAGER()"): _address(FEE_MANAGER),
    (WITHDRAW_MANAGER, "getWithdrawWindow()"): _uint(86_400),
    (WITHDRAW_MANAGER, "getRequestFee()"): _uint(10**15),
    (WITHDRAW_MANAGER, "getWithdrawFee()"): _uint(0),
    (WITHDRAW_MANAGER, "getSharesToRelease()"): _uint(5 * 10**8),
    (WITHDRAW_MANAGER, "getLastReleaseFundsTimestamp()"): _uint(TIMESTAMP - 3_600),
}
_BY_SELECTOR = {
    (to, function_signature_to_4byte_selector(signature)): answer
    for (to, signature), answer in CHAIN.items()
}


def _handler(to: str, data: bytes) -> bytes:
    answer = _BY_SELECTOR.get((Web3.to_checksum_address(to), data[:4]))
    if answer is None:
        raise ContractLogicError("not implemented")
    return answer(data[4:]) if callable(answer) else answer


def _ctx() -> Web3Context:
    provider = FakeRpc(
        chain_id=1, block=BLOCK, timestamp=TIMESTAMP, handler=_handler, storage=STORAGE
    )
    return Web3Context(Web3(provider), ChainId(1), cache=NullCache())


@pytest.fixture(autouse=True)
def _isolated(tmp_path, monkeypatch):
    """A frozen clock, a config naming a provider for chain 1, no cache."""
    monkeypatch.setattr(vault_info, "datetime", FrozenDatetime)
    monkeypatch.setattr(vault_rendering, "datetime", FrozenDatetime)
    monkeypatch.setattr(config_store, "CONFIG_DIR", tmp_path / "config")
    monkeypatch.setattr(config_store, "CONFIG_FILE", tmp_path / "config" / "c.json")
    monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path / "cache"))
    save_config(FusionConfig(providers={"1": "https://rpc.invalid"}))


def _document() -> str:
    data = fetch_vault_data(_ctx(), VAULT, block=BLOCK)
    return json.dumps(build_vault_info_json(data), indent=2) + "\n"


def test_fetched_document_matches_golden():
    assert_golden(GOLDEN / "fetched.json", _document())


def test_cli_json_matches_golden():
    with (
        patch.object(Web3Context, "from_url", return_value=_ctx()),
        patch("ipor_fusion.cli.vault_cmd._auto_save_vault", autospec=True),
    ):
        result = CliRunner().invoke(
            cli,
            ["vault", "info", VAULT, "--chain-id", "1"]
            + ["--block-number", str(BLOCK), "--json"],
        )
    assert result.exit_code == 0, result.output
    assert_golden(GOLDEN / "fetched.cli.json", result.output)
    # The command adds only the deployment lookup to the library's document.
    printed, built = json.loads(result.output), json.loads(_document())
    assert printed.pop("deployment") == {"error": "no-api-key"}
    assert built.pop("deployment") is None
    assert printed == built


def test_mcp_vault_info_matches_golden():
    with patch.object(Web3Context, "from_url", return_value=_ctx()):
        text = mcp_tool_text(
            "vault_info",
            {"vault_address": VAULT, "chain_id": 1, "block_number": BLOCK},
            VaultInfoResponse,
        )
    assert_golden(GOLDEN / "fetched.mcp.json", text)
    cli_document = (GOLDEN / "fetched.cli.json").read_text("utf-8")
    assert text == VaultInfoResponse.model_validate_json(cli_document).model_dump_json(
        indent=2
    )

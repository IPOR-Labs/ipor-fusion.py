"""The vault_preflight MCP tool over a faked preflight."""

import asyncio
from unittest.mock import MagicMock, patch

import pytest
from _addresses import miscased
from mcp.server.fastmcp.exceptions import ToolError
from web3 import Web3

from ipor_fusion import (
    AaveV3SupplyFuse,
    CallPermission,
    ExecutePreflight,
    PlasmaVault,
    PreflightAction,
)
from ipor_fusion.mcp import server
from ipor_fusion.types import (
    Amount,
    ChainId,
    FuseAddress,
    MarketId,
    Period,
    PlasmaVaultAddress,
)

VAULT = PlasmaVaultAddress(Web3.to_checksum_address("0x" + "11" * 20))
SENDER = Web3.to_checksum_address("0x" + "33" * 20)
# Hex letters, so it can carry a wrong checksum; SENDER has none.
SENDER_HEX = "0x" + "ab" * 20
FUSE = FuseAddress(Web3.to_checksum_address("0x" + "55" * 20))
ACTION = AaveV3SupplyFuse(FUSE).supply(
    asset=Web3.to_checksum_address("0x" + "44" * 20), amount=Amount(10)
)
CALLDATA = "0x" + PlasmaVault(VAULT).execute([ACTION]).calldata.hex()


@pytest.fixture
def preflight():
    with (
        patch.object(server, "load_config", autospec=True),
        patch.object(
            server, "_resolve_chain_id", autospec=True, return_value=ChainId(8453)
        ),
        patch.object(
            server, "_build_ctx", autospec=True, return_value=(MagicMock(), None)
        ),
        patch.object(server, "preflight_execute", autospec=True) as run,
    ):
        run.return_value = ExecutePreflight(
            vault=VAULT,
            chain_id=ChainId(8453),
            block_number=99,
            sender=SENDER,
            permission=CallPermission(immediate=False, delay=Period(60)),
            scheduled_at=1000,
            actions=(PreflightAction(0, FUSE, True, MarketId(1), ()),),
            findings=(),
            simulation=None,
        )
        yield run


def _call(**arguments: object) -> dict[str, object]:
    _content, structured = asyncio.run(
        server.mcp.call_tool("vault_preflight", arguments)
    )
    assert isinstance(structured, dict)
    return structured


def test_returns_the_document(preflight):
    result = _call(vault_address=VAULT, calldata=CALLDATA, sender=SENDER.lower())
    assert result["execute_immediate"] is False
    assert result["execute_delay"] == 60
    assert result["execute_scheduled_at"] == 1000
    assert result["simulation"] is None
    assert preflight.call_args.args[2] == [ACTION]
    assert preflight.call_args.kwargs == {"sender": SENDER, "simulate": True}


def test_bad_calldata_is_a_tool_error(preflight):
    with pytest.raises(ToolError, match="calldata: calldata is not hex"):
        _call(vault_address=VAULT, calldata="0xzz", sender=SENDER)
    empty = "0x" + PlasmaVault(VAULT).execute([]).calldata.hex()
    with pytest.raises(ToolError, match="no actions"):
        _call(vault_address=VAULT, calldata=empty, sender=SENDER)
    preflight.assert_not_called()


def test_wrong_sender_checksum_is_a_tool_error(preflight):
    with pytest.raises(ToolError, match="sender: invalid address checksum"):
        _call(vault_address=VAULT, calldata=CALLDATA, sender=miscased(SENDER_HEX))
    preflight.assert_not_called()

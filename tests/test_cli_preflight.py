"""`fusion vault preflight` over a faked preflight."""

import json
from unittest.mock import MagicMock, patch

import pytest
from click.testing import CliRunner
from web3 import Web3

from ipor_fusion import (
    AaveV3SupplyFuse,
    CallPermission,
    ExecutePreflight,
    PlasmaVault,
    PreflightAction,
    PreflightFinding,
    PreflightSimulation,
)
from ipor_fusion.cli.main import cli
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
FUSE = FuseAddress(Web3.to_checksum_address("0x" + "55" * 20))
ACTION = AaveV3SupplyFuse(FUSE).supply(
    asset=Web3.to_checksum_address("0x" + "44" * 20), amount=Amount(10)
)
CALLDATA = "0x" + PlasmaVault(VAULT).execute([ACTION]).calldata.hex()


def _result(findings=()):
    return ExecutePreflight(
        vault=VAULT,
        chain_id=ChainId(8453),
        block_number=99,
        sender=SENDER,
        permission=CallPermission(immediate=True, delay=Period(0)),
        actions=(PreflightAction(0, FUSE, True, MarketId(1), ("AaveV3SupplyFuse",)),),
        findings=tuple(findings),
        simulation=PreflightSimulation(True, None, 21000, Amount(5), Amount(6)),
    )


@pytest.fixture
def preflight():
    with (
        patch(
            "ipor_fusion.cli.preflight_cmd._build_ctx",
            autospec=True,
            return_value=(ChainId(8453), MagicMock()),
        ),
        patch("ipor_fusion.cli.preflight_cmd.load_config", autospec=True),
        patch("ipor_fusion.cli.preflight_cmd.preflight_execute", autospec=True) as run,
    ):
        run.return_value = _result()
        yield run


def _run(*args: str):
    return CliRunner().invoke(cli, ["vault", "preflight", VAULT, *args])


def test_json_is_the_sdk_document(preflight):
    result = _run("--calldata", CALLDATA, "--sender", SENDER, "--json")
    assert result.exit_code == 0, result.output
    document = json.loads(result.output)
    assert document["verdict"] == "ok"
    assert document["simulation"]["total_assets_after"] == 6
    _, actions = preflight.call_args.args[1:3]
    assert actions == [ACTION]
    assert preflight.call_args.kwargs == {"sender": SENDER, "simulate": True}


def test_the_table_and_a_critical_exit(preflight, tmp_path):
    preflight.return_value = _result(
        [PreflightFinding("simulation_failed", "critical", "the batch reverts: X()")]
    )
    path = tmp_path / "calldata.txt"
    path.write_text(CALLDATA)
    result = _run("--calldata", f"@{path}", "--sender", SENDER, "--no-simulate")
    assert result.exit_code == 1
    assert "the batch reverts: X()" in result.output
    assert "AaveV3SupplyFuse" in result.output
    assert preflight.call_args.kwargs["simulate"] is False


@pytest.mark.parametrize(
    ("calldata", "message"),
    [
        ("0xzz", "not hex"),
        ("0x" + PlasmaVault(VAULT).total_assets().calldata.hex(), "not a PlasmaVault"),
        ("0x" + PlasmaVault(VAULT).execute([]).calldata.hex(), "no actions"),
        ("@/nonexistent/file", "No such file"),
    ],
)
def test_bad_calldata_is_a_usage_error(preflight, calldata, message):
    result = _run("--calldata", calldata, "--sender", SENDER)
    assert result.exit_code == 2
    assert message in result.output
    preflight.assert_not_called()

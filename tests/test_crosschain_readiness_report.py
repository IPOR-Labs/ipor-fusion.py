"""Offline tests for ``scripts/crosschain_readiness.py``: the report shape, the
readiness verdicts and every false-positive guard, the snapshot discipline
and the exit codes, against mocked contexts. No network."""

from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path
from types import ModuleType
from unittest.mock import MagicMock

import pytest
from eth_abi import encode
from eth_utils import function_signature_to_4byte_selector as selector
from web3 import Web3

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "crosschain_readiness.py"

ON_RAMP = Web3.to_checksum_address("0x" + "aa" * 20)
REGISTRY = Web3.to_checksum_address("0x" + "bb" * 20)
POOL = Web3.to_checksum_address("0x" + "cc" * 20)
OWNER = Web3.to_checksum_address("0x" + "dd" * 20)
OTHER = Web3.to_checksum_address("0x" + "ee" * 20)
ZERO = Web3.to_checksum_address("0x" + "00" * 20)
HASH_A = "0x" + "11" * 32
HASH_B = "0x" + "22" * 32
REPORT_KEYS = {
    "schema",
    "generated_at",
    "pair",
    "complete",
    "ready",
    "transport_ready",
    "factories_ready",
    "blocked_by",
    "chains",
    "observed_blocks",
    "repin_candidates",
    "repin_status",
    "simulation_governance",
}


@pytest.fixture(scope="module")
def mod() -> ModuleType:
    spec = importlib.util.spec_from_file_location("crosschain_readiness", SCRIPT)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    # Dataclasses under ``from __future__ import annotations`` resolve their
    # field types through ``sys.modules``, so register before executing.
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def _answers(
    mod: ModuleType,
    spec,
    peer,
    *,
    token_lane: bool,
    asset_enabled: bool,
    native_usdc: bool,
    router_ok: bool,
    route_peer_ok: bool,
    interface_version: int,
    creation_codes_ok: bool,
) -> dict[tuple[str, bytes], bytes]:
    """Return data for every selector the snapshot reads on one chain."""
    hashes = mod.EXPECTED_CREATION_CODE_HASHES
    executor_hash = bytes.fromhex(
        hashes["executor"][2:] if creation_codes_ok else "33" * 32
    )
    route = (
        peer.chain_selector,
        mod.FACTORY if route_peer_ok else OTHER,
        ZERO,
        6_000_000,
        1_000_000,
        10**16,
        True,
    )
    asset = (spec.usdc, 6, True) if asset_enabled else (ZERO, 0, False)
    testtr = (spec.testtr, 18, True) if asset_enabled else (ZERO, 0, False)
    symbol, decimals = ("USDC", 6) if native_usdc else ("USDX", 18)
    return {
        (spec.usdc, selector("symbol()")): encode(["string"], [symbol]),
        (spec.usdc, selector("decimals()")): encode(["uint8"], [decimals]),
        (spec.router, selector("isChainSupported(uint64)")): encode(["bool"], [True]),
        (spec.router, selector("getOnRamp(uint64)")): encode(["address"], [ON_RAMP]),
        (ON_RAMP, selector("typeAndVersion()")): encode(["string"], ["OnRamp 2.0.0"]),
        (ON_RAMP, selector("getStaticConfig()")): encode(
            ["uint64", "address", "uint256", "address"],
            [spec.chain_selector, ON_RAMP, 1, REGISTRY],
        ),
        (REGISTRY, selector("getPool(address)")): encode(["address"], [POOL]),
        (POOL, selector("typeAndVersion()")): encode(
            ["string"], ["USDCTokenPoolProxy 2.0.0"]
        ),
        (POOL, selector("isSupportedChain(uint64)")): encode(["bool"], [token_lane]),
        (mod.FACTORY, selector("ccipRoute(uint256)")): encode(
            ["(uint64,address,address,uint96,uint96,uint256,bool)"], [route]
        ),
        (mod.FACTORY, selector("assetConfig(bytes32)") + mod.USDC_ASSET_ID): encode(
            ["address", "uint8", "bool"], list(asset)
        ),
        (mod.FACTORY, selector("assetConfig(bytes32)") + mod.TESTTR_ASSET_ID): encode(
            ["address", "uint8", "bool"], list(testtr)
        ),
        (mod.FACTORY, selector("chainIdOfSelector(uint64)")): encode(
            ["uint256"], [peer.chain_id]
        ),
        (mod.FACTORY, selector("OWNER()")): encode(["address"], [OWNER]),
        (mod.FACTORY, selector("CONFIG_DELAY()")): encode(["uint256"], [300]),
        (mod.FACTORY, selector("CCIP_ROUTER()")): encode(
            ["address"], [spec.router if router_ok else OTHER]
        ),
        (mod.FACTORY, selector("creationRestricted()")): encode(["bool"], [True]),
        (mod.FACTORY, selector("isAllowedCreator(address)")): encode(["bool"], [True]),
        (mod.FACTORY, selector("creationCodesConfigured()")): encode(["bool"], [True]),
        (mod.FACTORY, selector("executorCreationCodeHash()")): encode(
            ["bytes32"], [executor_hash]
        ),
        (mod.FACTORY, selector("dispatcherCreationCodeHash()")): encode(
            ["bytes32"], [bytes.fromhex(hashes["dispatcher"][2:])]
        ),
        (mod.FACTORY, selector("factoryInterfaceVersion()")): encode(
            ["uint32"], [interface_version]
        ),
        (spec.usdc_usd_feed, selector("latestRoundData()")): encode(
            ["uint80", "int256", "uint256", "uint256", "uint80"],
            [1, 99_998_000, 1_000, 1_700_000_000, 1],
        ),
        (spec.usdc_usd_feed, selector("description()")): encode(
            ["string"], ["USDC / USD"]
        ),
        (spec.usdc_usd_feed, selector("decimals()")): encode(["uint8"], [8]),
    }


def _ctx(
    mod: ModuleType,
    spec,
    peer,
    *,
    token_lane: bool = True,
    asset_enabled: bool = True,
    native_usdc: bool = True,
    router_ok: bool = True,
    route_peer_ok: bool = True,
    interface_version: int = 1,
    creation_codes_ok: bool = True,
    big_blocks: bool = True,
    block: int = 100,
    timestamp: int = 1_700_000_500,
    hashes: tuple[str, str] = (HASH_A, HASH_A),
) -> MagicMock:
    answers = _answers(
        mod,
        spec,
        peer,
        token_lane=token_lane,
        asset_enabled=asset_enabled,
        native_usdc=native_usdc,
        router_ok=router_ok,
        route_peer_ok=route_peer_ok,
        interface_version=interface_version,
        creation_codes_ok=creation_codes_ok,
    )
    ctx = MagicMock()
    ctx.chain_id = spec.chain_id
    ctx.default_block = block
    ctx.web3.eth.get_balance.return_value = 10**17
    ctx.web3.provider.make_request.return_value = {"result": big_blocks}

    def call(to, data, block=None):
        # Answers keyed by selector, or by selector plus the first argument
        # where one selector is read for several assets.
        raw = bytes(data)
        for key in ((to, raw[:36]), (to, raw[:4])):
            if key in answers:
                return answers[key]
        raise AssertionError(f"unexpected call {to} {raw[:4].hex()}")

    ctx.call.side_effect = call
    ctx.web3.eth.get_block.side_effect = [
        {"number": block, "hash": bytes.fromhex(h[2:]), "timestamp": timestamp}
        for h in hashes
    ]
    return ctx


def _report(mod: ModuleType, hub_ctx: MagicMock, spoke_ctx: MagicMock) -> dict:
    return mod.build_report(
        mod.snapshot_chain(hub_ctx, mod.HUB, mod.SPOKE),
        mod.snapshot_chain(spoke_ctx, mod.SPOKE, mod.HUB),
    )


def test_blocked_pair_is_complete_but_not_ready(mod):
    report = _report(
        mod,
        _ctx(mod, mod.HUB, mod.SPOKE, token_lane=False, asset_enabled=False),
        _ctx(mod, mod.SPOKE, mod.HUB, token_lane=True, asset_enabled=False),
    )

    assert report["complete"] is True
    assert report["ready"] is False
    assert report["transport_ready"] is False
    assert report["factories_ready"] is False
    assert report["repin_candidates"] is None
    assert report["repin_status"] == "not eligible: transport not ready"
    assert report["observed_blocks"]["hub"]["number"] == 100
    assert report["chains"]["arbitrum"]["token_lane_out"]["token_lane"] is False
    assert report["chains"]["hyperevm"]["token_lane_out"]["token_lane"] is True
    assert [b.split(" (")[0] for b in report["blocked_by"]] == [
        "Chainlink USDC token lane arbitrum->hyperevm not serving the pair",
        "factory on arbitrum: USDC asset not enabled",
        "factory on arbitrum: TESTTR asset not enabled",
        "factory on hyperevm: USDC asset not enabled",
        "factory on hyperevm: TESTTR asset not enabled",
    ]
    json.dumps(report)


def test_ready_pair_exposes_per_chain_repin_candidates(mod):
    report = _report(
        mod,
        _ctx(mod, mod.HUB, mod.SPOKE, block=500, timestamp=1_700_000_600),
        _ctx(mod, mod.SPOKE, mod.HUB, block=7, timestamp=1_700_000_590),
    )

    assert report["ready"] is True
    assert report["transport_ready"] and report["factories_ready"]
    assert report["blocked_by"] == []
    assert report["repin_status"] == "eligible"
    candidates = report["repin_candidates"]
    assert candidates["arbitrum"] == {
        "number": 500,
        "hash": HASH_A,
        "timestamp": 1_700_000_600,
    }
    assert candidates["hyperevm"]["number"] == 7
    assert candidates["hub_after_spoke_seconds"] == 10
    factory = report["chains"]["arbitrum"]["factory"]
    assert factory["gates"] == {
        "interface_version_supported": True,
        "router_matches": True,
        "creation_codes_configured": True,
        "creation_codes_expected": True,
        "creator_authorized": True,
        "usdc_asset_ready": True,
        "testtr_asset_ready": True,
        "route_to_peer_ready": True,
    }
    assert factory["interface_version"] == 1
    assert factory["testtr_asset"]["token"] == mod.HUB.testtr
    assert report["chains"]["hyperevm"]["testtr_lane_out"]["ready"] is True
    assert factory["creator"] == {
        "address": mod.CREATOR,
        "is_allowed_creator": True,
        "native_balance_wei": 10**17,
    }
    spoke_factory = report["chains"]["hyperevm"]["factory"]
    assert spoke_factory["creator"]["using_big_blocks"] is True
    assert spoke_factory["gates"]["creator_big_blocks"] is True
    assert report["chains"]["arbitrum"]["usdc_usd_feed"]["age_seconds"] == 600


@pytest.mark.parametrize(
    ("hub_timestamp", "spoke_timestamp", "status"),
    [
        (
            1_700_000_000,
            1_700_000_001,
            "not eligible: hub snapshot precedes spoke snapshot by 1 s",
        ),
        (
            1_700_001_000,
            1_700_000_000,
            "not eligible: snapshot skew 1000 s exceeds 900 s",
        ),
    ],
)
def test_repin_candidates_need_hub_after_spoke_within_bounded_skew(
    mod, hub_timestamp, spoke_timestamp, status
):
    report = _report(
        mod,
        _ctx(mod, mod.HUB, mod.SPOKE, timestamp=hub_timestamp),
        _ctx(mod, mod.SPOKE, mod.HUB, timestamp=spoke_timestamp),
    )

    assert report["ready"] is True  # readiness and pin eligibility are distinct
    assert report["repin_candidates"] is None
    assert report["repin_status"] == status
    assert report["observed_blocks"] == {
        "hub": report["chains"]["arbitrum"]["block"],
        "spoke": report["chains"]["hyperevm"]["block"],
    }


def test_wrong_token_identity_blocks_transport_even_with_open_lanes(mod):
    report = _report(
        mod,
        _ctx(mod, mod.HUB, mod.SPOKE),
        _ctx(mod, mod.SPOKE, mod.HUB, native_usdc=False),
    )

    assert report["transport_ready"] is False
    assert report["ready"] is False
    assert report["repin_candidates"] is None
    assert report["chains"]["hyperevm"]["usdc"]["is_native_usdc"] is False
    assert report["blocked_by"] == [
        f"token {mod.HYPEREVM.usdc} on hyperevm is not native USDC "
        "(symbol='USDX', decimals=18)"
    ]


def test_every_factory_gate_explains_itself(mod):
    report = _report(
        mod,
        _ctx(mod, mod.HUB, mod.SPOKE, router_ok=False),
        _ctx(mod, mod.SPOKE, mod.HUB, route_peer_ok=False),
    )

    assert report["transport_ready"] is True
    assert report["factories_ready"] is False
    assert report["ready"] is False
    assert report["chains"]["arbitrum"]["factory"]["gates"]["router_matches"] is False
    spoke_route = report["chains"]["hyperevm"]["factory"]["route_to_peer"]
    assert spoke_route["peer_is_factory"] is False and spoke_route["ready"] is False
    assert report["blocked_by"] == [
        "factory on arbitrum: factory CCIP_ROUTER differs from the chain's Router",
        "factory on hyperevm: route to peer not enabled",
    ]


def test_block_hash_change_marks_the_observation_incomplete(mod):
    hub_ctx = _ctx(mod, mod.HUB, mod.SPOKE, hashes=(HASH_A, HASH_B))
    spoke_ctx = _ctx(mod, mod.SPOKE, mod.HUB)
    report, code, summary = mod.run(
        lambda spec: hub_ctx if spec is mod.HUB else spoke_ctx
    )

    assert code == mod.EXIT_INCOMPLETE
    assert report["complete"] is False
    assert report["ready"] is False
    assert report["repin_candidates"] is None
    assert report["chains"]["arbitrum"]["errors"] == [
        {"stage": "block_recheck", "type": "BlockHashChanged"}
    ]
    assert "hub observation incomplete: block_recheck (BlockHashChanged)" in summary


def test_read_failures_are_reported_by_stage_and_class_only(mod):
    ctx = _ctx(mod, mod.HUB, mod.SPOKE)
    answers = ctx.call.side_effect

    def failing(to, data, block=None):
        if bytes(data)[:4] == selector("OWNER()"):
            raise RuntimeError("provider said no: https://rpc.example/v2/SECRET-KEY")
        return answers(to, data, block)

    ctx.call.side_effect = failing
    snapshot = mod.snapshot_chain(ctx, mod.HUB, mod.SPOKE)

    assert snapshot["complete"] is False
    assert snapshot["errors"] == [{"stage": "factory", "type": "RuntimeError"}]
    assert "factory" not in snapshot
    assert "SECRET" not in json.dumps(snapshot)
    assert "https" not in json.dumps(snapshot)


def test_snapshot_requires_a_pinned_block(mod):
    ctx = _ctx(mod, mod.HUB, mod.SPOKE)
    ctx.default_block = "latest"
    with pytest.raises(mod.ReadinessConfigError, match="pinned"):
        mod.snapshot_chain(ctx, mod.HUB, mod.SPOKE)


def test_connect_rejects_missing_url_and_wrong_chain_without_echoing_the_url(
    mod, monkeypatch
):
    monkeypatch.delenv("ARBITRUM_PROVIDER_URL", raising=False)
    with pytest.raises(
        mod.ReadinessConfigError, match="ARBITRUM_PROVIDER_URL is not set"
    ):
        mod.connect(mod.ARBITRUM)

    monkeypatch.setenv("ARBITRUM_PROVIDER_URL", "https://rpc.example/v2/SECRET-KEY")
    fake_web3 = MagicMock()
    fake_web3.return_value.eth.chain_id = 1
    fake_web3.return_value.eth.block_number = 42
    monkeypatch.setattr(mod, "Web3", fake_web3)
    with pytest.raises(mod.ReadinessConfigError) as excinfo:
        mod.connect(mod.ARBITRUM)
    assert "chain id 1, expected 42161" in str(excinfo.value)
    assert "SECRET" not in str(excinfo.value)

    fake_web3.return_value.eth.chain_id = 42161
    ctx = mod.connect(mod.ARBITRUM)
    assert ctx.default_block == 42
    assert ctx.chain_id == 42161


def test_main_writes_the_report_and_prints_no_urls(mod, monkeypatch, tmp_path, capsys):
    contexts = {
        mod.HUB.name: _ctx(
            mod, mod.HUB, mod.SPOKE, token_lane=False, asset_enabled=False
        ),
        mod.SPOKE.name: _ctx(mod, mod.SPOKE, mod.HUB, asset_enabled=False),
    }
    monkeypatch.setattr(mod, "connect", lambda spec: contexts[spec.name])
    output = tmp_path / "readiness-report.json"

    code = mod.main(
        ["--output", str(output), "--env-file", str(tmp_path / "absent.env")]
    )

    assert code == mod.EXIT_OK
    report = json.loads(output.read_text())
    assert set(report) == REPORT_KEYS
    assert report["schema"] == mod.SCHEMA
    assert report["complete"] is True and report["ready"] is False
    printed = capsys.readouterr().out
    assert "complete=True ready=False" in printed
    assert "arbitrum: block 100" in printed
    assert "repin: not eligible: transport not ready" in printed
    assert "http" not in printed


def test_main_overwrites_a_stale_ready_report_on_configuration_error(
    mod, monkeypatch, tmp_path, capsys
):
    monkeypatch.delenv("ARBITRUM_PROVIDER_URL", raising=False)
    monkeypatch.delenv("HYPEREVM_PROVIDER_URL", raising=False)
    output = tmp_path / "readiness-report.json"
    output.write_text(json.dumps({"ready": True, "repin_candidates": {"stale": 1}}))

    code = mod.main(
        ["--output", str(output), "--env-file", str(tmp_path / "absent.env")]
    )

    assert code == mod.EXIT_CONFIG
    report = json.loads(output.read_text())
    assert set(report) == REPORT_KEYS | {"error"}
    assert report["ready"] is False and report["complete"] is False
    assert report["repin_candidates"] is None
    # The spoke is connected first (its head must not postdate the hub's).
    assert report["error"] == {
        "type": "ReadinessConfigError",
        "message": "HYPEREVM_PROVIDER_URL is not set",
    }
    printed = capsys.readouterr().out
    assert "configuration error: HYPEREVM_PROVIDER_URL is not set" in printed
    assert "http" not in printed


def test_main_overwrites_a_stale_ready_report_on_incomplete_observation(
    mod, monkeypatch, tmp_path
):
    contexts = {
        mod.HUB.name: _ctx(mod, mod.HUB, mod.SPOKE, hashes=(HASH_A, HASH_B)),
        mod.SPOKE.name: _ctx(mod, mod.SPOKE, mod.HUB),
    }
    monkeypatch.setattr(mod, "connect", lambda spec: contexts[spec.name])
    output = tmp_path / "readiness-report.json"
    output.write_text(json.dumps({"ready": True}))

    code = mod.main(
        ["--output", str(output), "--env-file", str(tmp_path / "absent.env")]
    )

    assert code == mod.EXIT_INCOMPLETE
    report = json.loads(output.read_text())
    assert report["complete"] is False and report["ready"] is False


def test_main_overwrites_a_stale_ready_report_on_unexpected_probe_error(
    mod, monkeypatch, tmp_path, capsys
):
    def broken_probe():
        raise RuntimeError("provider URL must not be printed")

    monkeypatch.setattr(mod, "run", broken_probe)
    output = tmp_path / "readiness-report.json"
    output.write_text(json.dumps({"ready": True}))

    code = mod.main(
        ["--output", str(output), "--env-file", str(tmp_path / "absent.env")]
    )

    assert code == mod.EXIT_INCOMPLETE
    report = json.loads(output.read_text())
    assert report["ready"] is False and report["complete"] is False
    assert report["error"]["type"] == "RuntimeError"
    assert "provider URL" not in output.read_text()
    assert "provider URL" not in capsys.readouterr().out


def test_creator_big_blocks_gate_applies_to_the_spoke_only(mod):
    hub_ctx = _ctx(mod, mod.HUB, mod.SPOKE, big_blocks=False)
    report = _report(mod, hub_ctx, _ctx(mod, mod.SPOKE, mod.HUB, big_blocks=False))

    assert "creator_big_blocks" not in report["chains"]["arbitrum"]["factory"]["gates"]
    hub_ctx.web3.provider.make_request.assert_not_called()
    spoke = report["chains"]["hyperevm"]["factory"]
    assert spoke["creator"]["using_big_blocks"] is False
    assert spoke["gates"]["creator_big_blocks"] is False
    assert report["factories_ready"] is False
    assert report["blocked_by"] == [
        "factory on hyperevm: the canary creator is not on HyperEVM big blocks"
    ]


def test_unexpected_creation_codes_are_a_gate(mod):
    report = _report(
        mod,
        _ctx(mod, mod.HUB, mod.SPOKE, creation_codes_ok=False),
        _ctx(mod, mod.SPOKE, mod.HUB),
    )

    assert report["factories_ready"] is False
    hub_factory = report["chains"]["arbitrum"]["factory"]
    assert hub_factory["creation_code_hashes"]["executor"] == "0x" + "33" * 32
    assert report["blocked_by"] == [
        "factory on arbitrum: stored creation codes are not the pilot-v2 build"
    ]


def test_unsupported_factory_interface_version_is_a_gate(mod):
    report = _report(
        mod,
        _ctx(mod, mod.HUB, mod.SPOKE, interface_version=2),
        _ctx(mod, mod.SPOKE, mod.HUB),
    )

    assert report["factories_ready"] is False
    assert report["chains"]["arbitrum"]["factory"]["interface_version"] == 2
    assert report["blocked_by"] == [
        "factory on arbitrum: factory interface version is not 1"
    ]


def test_run_captures_the_spoke_head_before_the_hub_head(mod):
    order: list[str] = []
    contexts = {
        mod.HUB.name: _ctx(mod, mod.HUB, mod.SPOKE, timestamp=1_700_000_510),
        mod.SPOKE.name: _ctx(mod, mod.SPOKE, mod.HUB, timestamp=1_700_000_500),
    }

    def make(spec):
        order.append(spec.name)
        return contexts[spec.name]

    report, code, _ = mod.run(make)

    assert order == ["hyperevm", "arbitrum"]
    assert code == mod.EXIT_OK
    assert report["repin_candidates"]["hub_after_spoke_seconds"] == 10


def test_block_number_mismatch_marks_the_observation_incomplete(mod):
    ctx = _ctx(mod, mod.HUB, mod.SPOKE)
    ctx.web3.eth.get_block.side_effect = None
    ctx.web3.eth.get_block.return_value = {
        "number": 99,
        "hash": bytes.fromhex(HASH_A[2:]),
        "timestamp": 1_700_000_500,
    }

    snapshot = mod.snapshot_chain(ctx, mod.HUB, mod.SPOKE)

    assert snapshot["complete"] is False
    assert "block" not in snapshot
    assert snapshot["errors"] == [{"stage": "block", "type": "BlockNumberMismatch"}]
    assert ctx.call.call_count == 0

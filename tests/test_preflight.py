"""preflight_execute over a faked chain: the static checks and how the
simulation result becomes findings."""

from unittest.mock import MagicMock

import pytest
from _multicall import multicall_aware
from eth_abi import decode, encode
from eth_utils import function_signature_to_4byte_selector
from web3 import Web3
from web3.exceptions import ContractLogicError

import ipor_fusion.preflight as preflight_module
from ipor_fusion import (
    AaveV3SupplyFuse,
    AccessManager,
    BalanceFuse,
    ExecutePreflightError,
    PlasmaVault,
    build_execute_preflight_json,
    parse_execute_actions,
    preflight_execute,
)
from ipor_fusion.types import Amount, FuseAddress, MarketId, PlasmaVaultAddress

VAULT = PlasmaVaultAddress(Web3.to_checksum_address("0x" + "11" * 20))
MANAGER = Web3.to_checksum_address("0x" + "22" * 20)
SENDER = Web3.to_checksum_address("0x" + "33" * 20)
ASSET = Web3.to_checksum_address("0x" + "44" * 20)
FUSE = FuseAddress(Web3.to_checksum_address("0x" + "55" * 20))
STRANGER = FuseAddress(Web3.to_checksum_address("0x" + "66" * 20))
BLOCK = 1234


def _sel(signature: str) -> bytes:
    return function_signature_to_4byte_selector(signature)


NOW = 50_000


def _ctx(
    *,
    immediate=True,
    delay=0,
    fuses=(FUSE,),
    markets=None,
    graph=None,
    scheduled=0,
):
    markets = {FUSE: 1} if markets is None else markets
    graph = graph or {}

    def handler(to: str, data: bytes) -> bytes:
        selector = bytes(data[:4])
        if selector == _sel("getAccessManagerAddress()"):
            return encode(["address"], [MANAGER])
        if selector == _sel("getFuses()"):
            return encode(["address[]"], [list(fuses)])
        if selector == _sel("MARKET_ID()"):
            if to not in markets:
                raise ContractLogicError("execution reverted")
            return encode(["uint256"], [markets[to]])
        if selector == _sel("canCall(address,address,bytes4)"):
            return encode(["bool", "uint32"], [immediate, delay])
        if selector == _sel("getDependencyBalanceGraph(uint256)"):
            (market,) = decode(["uint256"], data[4:])
            return encode(["uint256[]"], [graph.get(market, [])])
        if selector == _sel("getSchedule(bytes32)"):
            (operation,) = decode(["bytes32"], data[4:])
            expected = AccessManager.hash_operation(
                SENDER, VAULT, PlasmaVault(VAULT).execute([_supply()]).calldata
            )
            return encode(["uint48"], [scheduled if operation == expected else 0])
        raise AssertionError(f"unexpected call to {to}: 0x{selector.hex()}")

    ctx = MagicMock()
    ctx.default_block = BLOCK
    ctx.chain_id = 8453
    ctx.at_block.return_value = ctx
    ctx.call.side_effect = multicall_aware(handler)
    ctx.web3.eth.get_block.return_value = {"timestamp": NOW}
    return ctx


@pytest.fixture(autouse=True)
def _balance_fuses(monkeypatch):
    monkeypatch.setattr(
        PlasmaVault,
        "read_balance_fuses",
        lambda self, ctx: [BalanceFuse(market_id=MarketId(1), fuse=FUSE)],
    )


def _supply(fuse: FuseAddress = FUSE):
    return AaveV3SupplyFuse(fuse).supply(asset=ASSET, amount=Amount(10))


def test_a_clean_batch_passes_the_static_checks():
    result = preflight_execute(
        _ctx(), VAULT, [_supply()], sender=SENDER, simulate=False
    )

    assert result.verdict == "ok"
    assert result.block_number == BLOCK
    assert result.findings == ()
    (action,) = result.actions
    assert (action.registered, action.market_id) == (True, 1)
    assert "AaveV3SupplyFuse" in action.candidates
    result.raise_for_failure()


@pytest.mark.parametrize(
    ("delay", "scheduled", "message"),
    [
        (3600, 0, "no ready schedule exists"),
        (3600, NOW + 1, f"scheduled for {NOW + 1}, after this block"),
        (0, 0, "may not call execute"),
    ],
)
def test_the_sender_must_be_allowed_now(delay, scheduled, message):
    result = preflight_execute(
        _ctx(immediate=False, delay=delay, scheduled=scheduled),
        VAULT,
        [_supply()],
        sender=SENDER,
        simulate=False,
    )
    assert [f.check for f in result.findings] == ["execute_permission"]
    assert message in result.findings[0].message
    with pytest.raises(ExecutePreflightError, match=message):
        result.raise_for_failure()


def test_a_ready_scheduled_operation_may_be_executed_directly():
    result = preflight_execute(
        _ctx(immediate=False, delay=3600, scheduled=NOW),
        VAULT,
        [_supply()],
        sender=SENDER,
        simulate=False,
    )
    assert result.findings == ()
    assert result.scheduled_at == NOW
    assert build_execute_preflight_json(result)["execute_scheduled_at"] == NOW


def test_market_zero_needs_no_balance_fuse():
    result = preflight_execute(
        _ctx(markets={FUSE: 0}), VAULT, [_supply()], sender=SENDER, simulate=False
    )
    assert result.findings == ()


def test_dependency_markets_need_balance_fuses_once_each():
    graph = {1: [2, 0], 2: [3, 1], 3: [2]}
    result = preflight_execute(
        _ctx(graph=graph), VAULT, [_supply()], sender=SENDER, simulate=False
    )
    assert [f.message for f in result.findings] == [
        "market 2, a balance dependency of market 1, has no balance fuse;"
        " execute reverts AddressEmptyCode after the actions",
        "market 3, a balance dependency of market 1, has no balance fuse;"
        " execute reverts AddressEmptyCode after the actions",
    ]


def test_an_unregistered_fuse_without_market_or_balance_fuse_is_critical():
    ctx = _ctx(markets={FUSE: 1, STRANGER: 7})
    result = preflight_execute(
        ctx, VAULT, [_supply(), _supply(STRANGER)], sender=SENDER, simulate=False
    )
    assert [f.check for f in result.findings] == [
        "fuse_not_registered",
        "no_balance_fuse",
    ]
    assert all("action 1" in f.message for f in result.findings)
    assert result.verdict == "critical"

    unreadable = preflight_execute(
        _ctx(markets={}), VAULT, [_supply()], sender=SENDER, simulate=False
    )
    assert [f.check for f in unreadable.findings] == ["fuse_market_unreadable"]


class _FakeObservation:
    def __init__(self, value):
        self.value = value
        self.result = MagicMock(success=value is not None)


class _FakeSimulator:
    outcome = MagicMock(success=True, revert_reason=None, gas_used=210_000)
    values = [Amount(100), Amount(110)]

    def __init__(self, web3, *, vault, alpha, block):
        assert (vault, alpha, block) == (VAULT, SENDER, BLOCK)
        self._values = list(self.values)

    def observe(self, call, *, label):
        assert call == PlasmaVault(VAULT).total_assets()
        expected = "before" if len(self._values) == 2 else "after"
        assert label == f"totalAssets {expected}"
        return _FakeObservation(self._values.pop(0))

    def execute(self, actions):
        assert actions == [_supply()]

    def run(self):
        return self.outcome


def test_the_simulation_reports_total_assets_and_a_revert(monkeypatch):
    monkeypatch.setattr(preflight_module, "is_simulate_v1_supported", lambda w3: True)
    monkeypatch.setattr(preflight_module, "VaultSimulator", _FakeSimulator)

    passed = preflight_execute(_ctx(), VAULT, [_supply()], sender=SENDER)
    assert passed.simulation is not None
    assert passed.simulation.total_assets_after == 110
    assert passed.verdict == "ok"

    monkeypatch.setattr(
        _FakeSimulator,
        "outcome",
        MagicMock(success=False, revert_reason="UnsupportedFuse()", gas_used=1),
    )
    failed = preflight_execute(_ctx(), VAULT, [_supply()], sender=SENDER)
    assert [f.check for f in failed.findings] == ["simulation_failed"]
    assert "UnsupportedFuse()" in failed.findings[0].message
    document = build_execute_preflight_json(failed)
    assert document["verdict"] == "critical"
    assert document["simulation"] is not None
    assert document["simulation"]["revert_reason"] == "UnsupportedFuse()"


def test_no_simulate_v1_is_a_warning_not_a_failure(monkeypatch):
    monkeypatch.setattr(preflight_module, "is_simulate_v1_supported", lambda w3: False)
    result = preflight_execute(_ctx(), VAULT, [_supply()], sender=SENDER)
    assert result.simulation is None
    assert [(f.check, f.severity) for f in result.findings] == [
        ("simulation_unavailable", "warning")
    ]
    assert result.verdict == "warning"


def test_an_empty_batch_is_rejected():
    with pytest.raises(ValueError, match="at least one action"):
        preflight_execute(_ctx(), VAULT, [], sender=SENDER)


def test_execute_calldata_parses_from_hex_or_bytes():
    calldata = PlasmaVault(VAULT).execute([_supply()]).calldata
    assert parse_execute_actions(calldata) == [_supply()]
    assert parse_execute_actions(" 0x" + calldata.hex() + "\n") == [_supply()]
    with pytest.raises(ValueError, match="not hex"):
        parse_execute_actions("0xzz")
    claims = calldata.replace(calldata[:4], _sel("claimRewards((address,bytes)[])"), 1)
    with pytest.raises(ValueError, match="not a PlasmaVault.execute call"):
        parse_execute_actions(claims)
    with pytest.raises(ValueError, match="not canonically encoded"):
        parse_execute_actions(calldata + b"\x00")

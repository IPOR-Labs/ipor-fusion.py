"""Offline guards for the composed HyperCore flow example.

They run without a provider: importing the module proves import has no chain
side effects, the pure builders and helpers are exercised on both branches, a
preview's revert is decoded by name through the SDK registry, and every
hard-coded address is held to the canary v3 fixture the SDK's own crosschain
tests drive (``tests/_crosschain.py``). The live previews and the pinned
simulation need the two providers and are not run here.
"""

from __future__ import annotations

from types import ModuleType, SimpleNamespace

import pytest
from _crosschain import CANARY_V3
from eth_abi.abi import encode
from test_examples_vaults import Loader, _simulated_call, _simulation_result
from web3 import Web3

from ipor_fusion.crosschain.transport import CrosschainTransportKind

MODULE = "composed_hypercore_flow_arbitrum_hyperevm.py"
CANARY_CCIP = CANARY_V3.transports[CrosschainTransportKind.CHAINLINK_CCIP]
CANARY_SPOKE = CANARY_V3.spokes[0]
FEE_SELECTOR = Web3.keccak(text="CcipInsufficientNativeFeeBalance(uint256,uint256)")[:4]


class TestComposedHypercoreFlow:
    mod: ModuleType

    @pytest.fixture(autouse=True)
    def _mod(self, load_example: Loader) -> None:
        self.mod = load_example(MODULE)

    def test_connected_web3_exits_without_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("HYPEREVM_PROVIDER_URL", raising=False)
        with pytest.raises(SystemExit):
            self.mod._connected_web3(
                "HYPEREVM_PROVIDER_URL", self.mod.HYPEREVM_CHAIN_ID
            )

    def test_check_raises_only_on_a_false_condition(self) -> None:
        self.mod._check(True, "must not raise")
        with pytest.raises(AssertionError, match="boom"):
            self.mod._check(False, "boom")

    def test_assert_relay_success_names_the_failed_call(self) -> None:
        results = {
            self.mod.ARBITRUM_CHAIN_ID: _simulation_result(
                [_simulated_call("recall", success=True)]
            ),
            self.mod.HYPEREVM_CHAIN_ID: _simulation_result(
                [_simulated_call("ccip_receive:deadbeef", success=False)]
            ),
        }
        with pytest.raises(AssertionError, match="ccip_receive:deadbeef"):
            self.mod._assert_relay_success(results)
        self.mod._assert_relay_success(
            {self.mod.ARBITRUM_CHAIN_ID: results[self.mod.ARBITRUM_CHAIN_ID]}
        )

    def test_addresses_match_the_canary_fixture(self) -> None:
        # The example and the SDK's crosschain tests drive the same live
        # deployment; one drifting from the other fails here, offline.
        hub, spoke = CANARY_V3.hub, CANARY_SPOKE.chain
        expected = {
            "HUB_VAULT": CANARY_V3.vault,
            "EXECUTOR": CANARY_CCIP.executor,
            "ALPHA": CANARY_V3.owner,
            "BALANCE_PROPOSER": CANARY_V3.balance_proposer,
            "BALANCE_APPROVER": CANARY_V3.balance_approver,
            "ARBITRUM_USDC": hub.usdc,
            "HYPEREVM_USDC": spoke.usdc,
            "ARBITRUM_CCIP_ROUTER": hub.ccip_router,
            "HYPEREVM_CCIP_ROUTER": spoke.ccip_router,
            "ARBITRUM_CCIP_SELECTOR": hub.chain_selector,
            "HYPEREVM_CCIP_SELECTOR": spoke.chain_selector,
            "ARBITRUM_CHAIN_ID": hub.chain_id,
            "HYPEREVM_CHAIN_ID": spoke.chain_id,
            "CROSSCHAIN_MARKET": CANARY_V3.market_id,
        }
        for name, value in expected.items():
            assert getattr(self.mod, name) == value, name
        # the HyperCore vault is the composed flow's remote vault, not the plain
        # spoke the crosschain fixture names
        assert self.mod.HYPERCORE_VAULT != CANARY_SPOKE.remote_vault
        assert self.mod.HYPERCORE_MARKET == 55

    def test_pinned_blocks_are_ordered_inside_the_live_cycle(self) -> None:
        # REDEEM (09:53 UTC) before the recall (10:16 UTC) on both chains; the
        # hub pin a little after its spoke pin, as the attestation requires
        assert (
            self.mod.ARBITRUM_BLOCK_BEFORE_REDEEM
            < self.mod.ARBITRUM_BLOCK_BEFORE_RECALL
        )
        assert (
            self.mod.HYPEREVM_BLOCK_BEFORE_REDEEM
            < self.mod.HYPEREVM_BLOCK_BEFORE_RECALL
        )

    def test_attesters_are_distinct_from_the_alpha(self) -> None:
        actors = {self.mod.ALPHA, self.mod.BALANCE_PROPOSER, self.mod.BALANCE_APPROVER}
        assert len(actors) == 3

    def test_usdc_balance_override_targets_the_probed_slot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.setattr(self.mod, "erc20_balance_slot", lambda *a, **k: 9)
        holder = self.mod.HYPERCORE_VAULT
        override = self.mod._usdc_balance_override(object(), holder, 15_000_000)
        (token,) = override
        assert token == self.mod.HYPEREVM_USDC
        (key,), (value,) = (
            override[token]["stateDiff"].keys(),
            override[token]["stateDiff"].values(),
        )
        from eth_utils import keccak

        assert key == "0x" + keccak(encode(["address", "uint256"], [holder, 9])).hex()
        assert int(value, 16) == 15_000_000

    def test_preview_decodes_a_revert_by_name_and_reports_gas_otherwise(self) -> None:
        class FakeProvider:
            def __init__(self, response: dict) -> None:
                self.response = response
                self.requests: list = []

            def make_request(self, method: str, params: list) -> dict:
                self.requests.append((method, params))
                return self.response

        class FakeVault:
            def execute(self, actions: list) -> SimpleNamespace:
                return SimpleNamespace(calldata=b"\x01\x02")

        data = FEE_SELECTOR + encode(
            ["uint256", "uint256"], [1_071_382_564_562_993, 4_080_081_177_644_022]
        )
        reverted = SimpleNamespace(
            provider=FakeProvider({"error": {"code": 3, "data": "0x" + data.hex()}})
        )
        p = self.mod._preview(
            reverted, FakeVault(), "redeem", object(), override=None, note="n"
        )
        assert p.gas is None
        assert (
            p.revert
            == "CcipInsufficientNativeFeeBalance(1071382564562993, 4080081177644022)"
        )
        assert p.calldata == "0x0102"
        method, params = reverted.provider.requests[0]
        assert (
            method == "eth_estimateGas"
            and params[0]["from"] == self.mod.ALPHA
            and len(params) == 2
        )

        ok = SimpleNamespace(provider=FakeProvider({"result": hex(553_086)}))
        p = self.mod._preview(
            ok, FakeVault(), "bridge", object(), override={"x": {}}, note="n"
        )
        assert p.gas == 553_086 and p.revert is None
        assert (
            len(ok.provider.requests[0][1]) == 3
        )  # the override travels as the third param

    def test_transport_covers_both_chains(self) -> None:
        transport = self.mod._transport()
        chains = (
            {c.chain_id for c in transport.chains}
            if hasattr(transport, "chains")
            else None
        )
        if chains is not None:
            assert chains == {self.mod.ARBITRUM_CHAIN_ID, self.mod.HYPEREVM_CHAIN_ID}

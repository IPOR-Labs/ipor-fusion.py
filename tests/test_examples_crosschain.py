"""Offline guards for the crosschain CCIP lifecycle example.

They run without a provider: importing the module proves import has no chain
side effects, the pure builders are decoded back, the fail-loud helpers are
pinned on both branches, and every hard-coded address is held to the canary
fixture the SDK's own crosschain tests drive (``tests/_crosschain.py``). The
simulation itself is covered by ``test_simulate_examples_crosschain.py``.
"""

from __future__ import annotations

from types import ModuleType

import pytest
from _crosschain import CANARY
from eth_abi.abi import decode
from test_examples_vaults import Loader, _simulated_call, _simulation_result

from ipor_fusion.crosschain.messages import BusinessAction
from ipor_fusion.crosschain.transport import CrosschainTransportKind
from ipor_fusion.types import Amount, Shares

MODULE = "crosschain_ccip_usdc_arbitrum_hyperevm.py"
CANARY_CCIP = CANARY.transports[CrosschainTransportKind.CHAINLINK_CCIP]
CANARY_SPOKE = CANARY.spokes[0]


class TestCrosschainCcipUsdcArbitrumHyperevm:
    mod: ModuleType

    @pytest.fixture(autouse=True)
    def _mod(self, load_example: Loader) -> None:
        self.mod = load_example(MODULE)

    def test_connected_web3_exits_without_url(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        monkeypatch.delenv("ARBITRUM_PROVIDER_URL", raising=False)
        with pytest.raises(SystemExit):
            self.mod._connected_web3(
                "ARBITRUM_PROVIDER_URL", self.mod.ARBITRUM_CHAIN_ID
            )

    def test_assert_relay_success_raises_on_failure(self) -> None:
        results = {
            self.mod.ARBITRUM_CHAIN_ID: _simulation_result(
                [_simulated_call("approve_hub_deposit", success=True)]
            ),
            self.mod.HYPEREVM_CHAIN_ID: _simulation_result(
                [_simulated_call("ccip_receive:deadbeef", success=False)]
            ),
        }
        with pytest.raises(AssertionError, match="simulation calls failed"):
            self.mod._assert_relay_success(results)
        self.mod._assert_relay_success(
            {self.mod.ARBITRUM_CHAIN_ID: results[self.mod.ARBITRUM_CHAIN_ID]}
        )

    def test_check_raises_only_on_a_false_condition(self) -> None:
        self.mod._check(True, "must not raise")
        with pytest.raises(AssertionError, match="boom"):
            self.mod._check(False, "boom")

    def test_commands_target_the_spoke_vault(self) -> None:
        deposit = self.mod.deposit_command(Amount(1_000_000))
        assert deposit.action == BusinessAction.DEPOSIT
        assert deposit.target_vault == self.mod.SPOKE_VAULT
        vault, amount_sd, min_shares = decode(
            ["address", "uint256", "uint256"], deposit.action_data
        )
        assert (self.mod.SPOKE_VAULT.lower(), amount_sd, min_shares) == (
            vault,
            1_000_000,
            0,
        )

        redeem = self.mod.redeem_command(Shares(42))
        assert redeem.action == BusinessAction.REDEEM
        assert redeem.target_vault == self.mod.SPOKE_VAULT
        # A fresh command carries no id, sequence or epoch: the executor stamps them.
        assert (redeem.command_id, redeem.sequence, redeem.command_config_epoch) == (
            bytes(32),
            0,
            0,
        )

    def test_transport_covers_both_chains(self) -> None:
        transport = self.mod.transport()
        assert transport.chain_ids == {
            self.mod.ARBITRUM_CHAIN_ID,
            self.mod.HYPEREVM_CHAIN_ID,
        }
        hub = transport.chain(self.mod.ARBITRUM_CHAIN_ID)
        spoke = transport.chain(self.mod.HYPEREVM_CHAIN_ID)
        assert (hub.router, hub.chain_selector, hub.token) == (
            self.mod.ARBITRUM_CCIP_ROUTER,
            self.mod.ARBITRUM_CCIP_SELECTOR,
            self.mod.ARBITRUM_USDC,
        )
        assert (spoke.router, spoke.chain_selector, spoke.token) == (
            self.mod.HYPEREVM_CCIP_ROUTER,
            self.mod.HYPEREVM_CCIP_SELECTOR,
            self.mod.HYPEREVM_USDC,
        )

    def test_addresses_match_the_canary_fixture(self) -> None:
        # The example and the SDK's crosschain tests drive the same live
        # deployment; one drifting from the other fails here, offline.
        hub, spoke = CANARY.hub, CANARY_SPOKE.chain
        expected = {
            "HUB_VAULT": CANARY.vault,
            "EXECUTOR": CANARY_CCIP.executor,
            "SPOKE_VAULT": CANARY_SPOKE.remote_vault,
            "ALPHA": CANARY.owner,
            "BALANCE_PROPOSER": CANARY.balance_proposer,
            "BALANCE_APPROVER": CANARY.balance_approver,
            "ARBITRUM_USDC": hub.usdc,
            "HYPEREVM_USDC": spoke.usdc,
            "ARBITRUM_CCIP_ROUTER": hub.ccip_router,
            "HYPEREVM_CCIP_ROUTER": spoke.ccip_router,
            "ARBITRUM_CCIP_SELECTOR": hub.chain_selector,
            "HYPEREVM_CCIP_SELECTOR": spoke.chain_selector,
            "ARBITRUM_CHAIN_ID": hub.chain_id,
            "HYPEREVM_CHAIN_ID": spoke.chain_id,
            "ARBITRUM_PINNED_BLOCK": hub.block,
            "HYPEREVM_PINNED_BLOCK": spoke.block,
            "CROSSCHAIN_MARKET": CANARY.market_id,
        }
        for name, value in expected.items():
            assert getattr(self.mod, name) == value, name

    def test_attesters_are_distinct_from_the_alpha(self) -> None:
        actors = {
            self.mod.ALPHA,
            self.mod.BALANCE_PROPOSER,
            self.mod.BALANCE_APPROVER,
        }
        assert len(actors) == 3

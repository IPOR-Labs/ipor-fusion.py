"""Offline guards for the crosschain CCIP example on the released contracts.

They run without a provider: importing the module proves import has no chain
side effects, the pure builders are decoded back, the fail-loud helpers are
pinned on both branches, and every hard-coded address is held to its ipor-abi
registry value. The simulation itself is covered by
``test_simulate_examples_crosschain.py``.
"""

from __future__ import annotations

from pathlib import Path
from types import ModuleType

import pytest
from eth_abi.abi import decode
from test_examples_vaults import Loader, _simulated_call, _simulation_result
from test_simulate_crosschain_hyperevm import (
    ARBITRUM_FUSION_FACTORY,
    ARBITRUM_ROUTER,
    ARBITRUM_SELECTOR,
    CORE_LIBRARIES,
    FUSE_SOURCE_REVISION,
    HYPEREVM_FUSION_FACTORY,
    HYPEREVM_ROUTER,
    HYPEREVM_SELECTOR,
    MESSAGE_GAS_LIMIT,
    ROUTE_MAX_FEES,
    TICKET_GAS_LIMIT,
    TOKEN_GAS_LIMIT,
    USDC,
)
from web3 import Web3

from ipor_fusion.core import FusionFactory
from ipor_fusion.crosschain import CrosschainSubstrateLib, CrosschainSubstrateType
from ipor_fusion.crosschain.messages import BusinessAction
from ipor_fusion.market_ids import IporFusionMarkets
from ipor_fusion.types import Amount, Shares

MODULE = "crosschain_ccip_usdc_arbitrum_hyperevm.py"

#: ipor-abi mainnet-arbitrum-fusion and mainnet-hyperevm-fusion addresses.json
#: (identical on both chains), keyed by the example's constant names.
REGISTRY = {
    "CCIP_FACTORY": "0xAd84580810A4e498F5F13Ad8506F73782FB472FA",
    "CCIP_SUPPLY_FUSE": "0x00a20FD711083CcE9C9c4292762aFf79f78C48eb",
    "CCIP_COMMAND_FUSE": "0x997F12b31a6B1011ba844Bd0BC5fbeBCC7604793",
    "CROSSCHAIN_CLAIM_FUSE": "0x77bE16F7b33332BE2589E8E0582b397c524cF0d9",
    "CROSSCHAIN_BALANCE_FUSE": "0x5896f376F06faB3964B1e060F8F3A86131D639c2",
    "CROSSCHAIN_NAV_PRE_HOOK": "0xA17c7A5Ed994aA5FE99C22B37DeCBCbfb32FaFe3",
}
REGISTRY_LIBRARIES = {
    "CcipRouteLib": "0x2045A36f6f884A7e80B8f27BD9673066C5c34d9A",
    "CcipSendLib": "0xf4CD217F464821822e069c8984168B723439B6Fb",
    "CcipDecimalsLib": "0x43bb2B17a1980c06d6039F08640Cac8FcA37F766",
    "CcipExecutorAttestationLib": "0x0921B83330A54212094b9822a6D5697519B0DA9f",
    "CcipExecutorCommandLib": "0xFF29Cf09B706fD333A43A0EF3c8962f097a218Ff",
    "CcipExecutorTransferLib": "0x9AD0ED30fdd23c5e46F9043F13C7733eF932Ef2c",
    "RescueLogicLib": "0x38bbaab37b55d200e87d1a9B1524ADD1d4235E62",
    "CrosschainVaultActionLib": "0x385Aa46Dd127b6976B5803CFB448662647E3B6fC",
    "CcipDispatcherLib": "0x75E4311Bf1fe29673F690df34Db0e5f29FDf2566",
}
EXECUTOR = Web3.to_checksum_address("0x85Be2f2d0a0b4491b66FE132aeAA683BE691acD1")
SPOKE_VAULT = Web3.to_checksum_address("0x532960128293Ed8d724938119E9Cda95732af3a0")


class TestCrosschainCcipUsdcArbitrumHyperevm:
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

    def test_assert_relay_success_raises_on_failure(self) -> None:
        results = {
            self.mod.ARBITRUM_CHAIN_ID: _simulation_result(
                [_simulated_call("configure_creation_codes", success=True)]
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

    def test_addresses_match_the_registry(self) -> None:
        for name, address in REGISTRY.items():
            assert getattr(self.mod, name) == Web3.to_checksum_address(address), name
        assert {name: address for _, name, address in self.mod.RELEASE_LIBRARIES} == {
            name: Web3.to_checksum_address(address)
            for name, address in REGISTRY_LIBRARIES.items()
        }
        assert self.mod.ARBITRUM_FUSION_FACTORY == ARBITRUM_FUSION_FACTORY
        assert self.mod.HYPEREVM_FUSION_FACTORY == HYPEREVM_FUSION_FACTORY

    def test_lane_constants_match_the_release_rehearsal(self) -> None:
        # The example and the release rehearsal configure the same factory
        # policy for the same asset; one drifting from the other fails here.
        mod = self.mod
        assert mod.RELEASE_COMMIT == FUSE_SOURCE_REVISION
        assert [(source, name) for source, name, _ in mod.RELEASE_LIBRARIES] == [
            (contract.source, contract.name) for contract in CORE_LIBRARIES
        ]
        assert (mod.ARBITRUM_USDC, mod.HYPEREVM_USDC) == USDC.tokens
        assert (mod.ARBITRUM_USDC_USD_FEED, mod.HYPEREVM_USDC_USD_FEED) == USDC.feeds
        assert mod.USDC_ASSET_ID == USDC.asset_id
        assert mod.USDC_DECIMALS == USDC.decimals
        assert (mod.ARBITRUM_CCIP_ROUTER, mod.HYPEREVM_CCIP_ROUTER) == (
            ARBITRUM_ROUTER,
            HYPEREVM_ROUTER,
        )
        assert (mod.ARBITRUM_CCIP_SELECTOR, mod.HYPEREVM_CCIP_SELECTOR) == (
            ARBITRUM_SELECTOR,
            HYPEREVM_SELECTOR,
        )
        assert (
            mod.MESSAGE_GAS_LIMIT,
            mod.TOKEN_GAS_LIMIT,
            mod.DISPATCHER_DEPLOYMENT_GAS_LIMIT,
        ) == (MESSAGE_GAS_LIMIT, TOKEN_GAS_LIMIT, TICKET_GAS_LIMIT)
        assert (mod.ARBITRUM_ROUTE_MAX_FEE, mod.HYPEREVM_ROUTE_MAX_FEE) == (
            ROUTE_MAX_FEES
        )
        assert mod.CROSSCHAIN_MARKET == IporFusionMarkets.CROSSCHAIN

    def test_actors_are_distinct(self) -> None:
        mod = self.mod
        actors = {
            mod.FACTORY_OWNER,
            mod.VAULT_OWNER,
            mod.ALPHA,
            mod.DEPOSITOR,
            mod.BALANCE_PROPOSER,
            mod.BALANCE_APPROVER,
            mod.RESCUE_GUARDIAN,
        }
        assert len(actors) == 7
        safety = mod.safety_config()
        assert (safety.balance_proposer, safety.balance_approver) == (
            mod.BALANCE_PROPOSER,
            mod.BALANCE_APPROVER,
        )
        assert (safety.rescue_admin, safety.rescue_guardian) == (
            mod.VAULT_OWNER,
            mod.RESCUE_GUARDIAN,
        )
        assert 0 < safety.min_update_interval < safety.balance_staleness_max

    def test_creation_codes_must_be_the_release_build(self) -> None:
        assert len(self.mod.EXECUTOR_CREATION_CODE_HASH) == 32
        assert len(self.mod.DISPATCHER_CREATION_CODE_HASH) == 32
        with pytest.raises(AssertionError, match="executor creation code"):
            self.mod.check_creation_codes(self.mod.CreationCodes(b"\x60", b"\x60"))

    def test_forge_inspect_command_links_every_release_library(self) -> None:
        command = self.mod.forge_inspect_command("CcipCrosschainExecutor")
        assert command[:2] == ["forge", "inspect"]
        assert command[-2:] == [
            "contracts/crosschain/ccip/CcipCrosschainExecutor.sol:CcipCrosschainExecutor",
            "bytecode",
        ]
        flags = command[2:-2]
        assert flags[0::2] == ["--libraries"] * len(CORE_LIBRARIES)
        assert flags[1::2] == [
            f"{contract.source}:{contract.name}:"
            + Web3.to_checksum_address(REGISTRY_LIBRARIES[contract.name])
            for contract in CORE_LIBRARIES
        ]

    def test_load_creation_codes(
        self, monkeypatch: pytest.MonkeyPatch, tmp_path: Path
    ) -> None:
        for name in self.mod.CREATION_CODE_ENV_VARS:
            monkeypatch.delenv(name, raising=False)
        with pytest.raises(SystemExit, match="forge inspect"):
            self.mod._load_creation_codes()
        executor_file, dispatcher_file = tmp_path / "e.hex", tmp_path / "d.hex"
        executor_file.write_text("0x6001\n")
        dispatcher_file.write_text("6002")
        executor_var, dispatcher_var = self.mod.CREATION_CODE_ENV_VARS
        monkeypatch.setenv(executor_var, str(executor_file))
        monkeypatch.setenv(dispatcher_var, str(dispatcher_file))
        assert self.mod._load_creation_codes() == (b"\x60\x01", b"\x60\x02")

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

    def test_routes(self) -> None:
        route = self.mod.factory_route(self.mod.HYPEREVM_CCIP_SELECTOR, 7)
        assert route.peer == self.mod.CCIP_FACTORY
        assert route.fee_token == Web3.to_checksum_address("0x" + "00" * 20)
        assert (route.message_gas_limit, route.token_gas_limit, route.max_fee) == (
            self.mod.MESSAGE_GAS_LIMIT,
            self.mod.TOKEN_GAS_LIMIT,
            7,
        )
        assert route.enabled
        executor_route = self.mod.executor_route(EXECUTOR)
        assert executor_route.peer == EXECUTOR
        assert executor_route.chain_selector == self.mod.HYPEREVM_CCIP_SELECTOR
        assert executor_route.max_fee == self.mod.ARBITRUM_ROUTE_MAX_FEE

    def test_crosschain_substrates_grant_one_executor_and_one_vault(self) -> None:
        decoded = [
            CrosschainSubstrateLib.bytes32_to_substrate(raw)
            for raw in self.mod.crosschain_substrates(EXECUTOR, SPOKE_VAULT)
        ]
        assert [
            (s.substrate_type, s.chain_id, s.substrate_address) for s in decoded
        ] == [
            (CrosschainSubstrateType.EXECUTOR, 0, EXECUTOR),
            (
                CrosschainSubstrateType.REMOTE_VAULT,
                self.mod.HYPEREVM_CHAIN_ID,
                SPOKE_VAULT,
            ),
        ]

    def test_nav_pre_hook_guards_entry_points_only(self) -> None:
        selectors = self.mod.nav_guarded_selectors()
        assert len(set(selectors)) == len(selectors) == 7
        # deposit(uint256,address) and redeem(uint256,address,address)
        assert bytes.fromhex("6e553f65") in selectors
        assert bytes.fromhex("ba087652") in selectors
        update_markets_balances = bytes(
            Web3.keccak(text="updateMarketsBalances(uint256[])")[:4]
        )
        assert update_markets_balances not in selectors

    def test_commands_target_the_spoke_vault(self) -> None:
        deposit = self.mod.deposit_command(SPOKE_VAULT, Amount(1_000_000))
        assert deposit.action == BusinessAction.DEPOSIT
        vault, amount_sd, min_shares = decode(
            ["address", "uint256", "uint256"], deposit.action_data
        )
        assert (Web3.to_checksum_address(vault), amount_sd, min_shares) == (
            SPOKE_VAULT,
            1_000_000,
            0,
        )
        redeem = self.mod.redeem_command(SPOKE_VAULT, Shares(42))
        assert redeem.action == BusinessAction.REDEEM
        vault, shares, min_assets = decode(
            ["address", "uint256", "uint256"], redeem.action_data
        )
        assert (Web3.to_checksum_address(vault), shares, min_assets) == (
            SPOKE_VAULT,
            42,
            0,
        )

    def test_clones(self) -> None:
        for build, factory, token in (
            (
                self.mod.hub_clone,
                ARBITRUM_FUSION_FACTORY,
                self.mod.ARBITRUM_USDC,
            ),
            (
                self.mod.spoke_clone,
                HYPEREVM_FUSION_FACTORY,
                self.mod.HYPEREVM_USDC,
            ),
        ):
            call = build(FusionFactory(factory))
            assert call.to == factory
            args = FusionFactory.decode_clone_calldata(call.calldata)
            assert args.underlying_token == token
            assert args.owner == self.mod.VAULT_OWNER
            assert args.redemption_delay_seconds == 1

    def test_lane_fuses(self) -> None:
        fuses = self.mod.lane_fuses()
        assert (fuses.supply, fuses.command, fuses.claim) == (
            self.mod.CCIP_SUPPLY_FUSE,
            self.mod.CCIP_COMMAND_FUSE,
            self.mod.CROSSCHAIN_CLAIM_FUSE,
        )
